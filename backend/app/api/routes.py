import asyncio
import json
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import github, runner
from app.observability import limiter
from app.services import gate as gate_svc
from app.services.config import ConfigError, GateConfig, ReviewConfig, parse_config
from app.services.exports import build_patch, build_sarif
from app.analysis.diff import parse_diff
from app.core.config import get_settings
from app.core.types import SourceFile, detect_language
from app.db.models import FindingRow, Review, get_db
from app.rag.index import get_index
from app.schemas import ReviewIn, ReviewOut, ReviewSummary, TriageIn
from app.services_events import hub

log = logging.getLogger(__name__)
router = APIRouter()


def _new_review(db: Session, title: str, source: str, ref: str | None, files: list[SourceFile], config: ReviewConfig | None = None, meta: dict | None = None) -> Review:
    r = Review(title=title, source=source, source_ref=ref, files=[f.model_dump() for f in files],
               config=(config or ReviewConfig()).model_dump(mode="json"), meta=meta or {})
    db.add(r)
    db.commit()
    return r


async def _repo_config(owner: str, repo: str, sha: str) -> ReviewConfig | None:
    """The repository's own .codereview.yml at the PR's head commit. A missing or invalid file never blocks a review."""
    try:
        text = await github.fetch_text_file(owner, repo, ".codereview.yml", sha)
        return parse_config(text) if text else None
    except (github.GitHubError, ConfigError, httpx.HTTPError) as e:
        log.warning("ignoring repo config for %s/%s: %s", owner, repo, e)
        return None


async def _post_back(owner: str, repo: str, num: int, head_sha: str):
    """After the review: inline comments (new findings only when there is a baseline, with one-click suggestion
    blocks where a fix is known), plus the quality gate as a commit status."""
    async def on_done(review_id: str, result: dict) -> None:
        pairs = list(zip(result["findings"], result["is_new"]))
        scope = [f for f, new in pairs if new] if result["has_baseline"] else [f for f, _ in pairs]
        comments = [{"path": f.file, "line": f.line, "side": "RIGHT", "body": github.review_comment(f)}
                    for f in scope if f.severity.value in ("critical", "high", "medium")]
        gate = result["gate"]
        header = f"**Automated review: {result['grade']} ({result['score']}/100)** · quality gate {'passed' if gate['passed'] else 'FAILED'}"
        try:
            await github.post_review(owner, repo, num, head_sha, f"{header}\n\n{result['summary']}", comments)
        except Exception:
            log.exception("posting review to GitHub failed")
        try:
            await github.post_status(owner, repo, head_sha, "success" if gate["passed"] else "failure", gate_svc.status_description(gate))
        except Exception:
            log.exception("setting commit status failed")
    return on_done


@router.post("/reviews", response_model=ReviewSummary, status_code=202)
@limiter.limit(get_settings().review_rate_limit)
async def create_review(request: Request, body: ReviewIn, db: Session = Depends(get_db)):
    s = get_settings()
    on_done = None
    meta: dict = {}
    try:
        explicit = parse_config(body.config) if body.config else None
    except ConfigError as e:
        raise HTTPException(422, f"Invalid config: {e}")
    cfg = explicit
    if body.pr_url:
        try:
            owner, repo, num = github.parse_pr_url(body.pr_url)
            diff, meta = await github.fetch_pr(owner, repo, num)
        except github.GitHubError as e:
            raise HTTPException(400, str(e))
        except httpx.HTTPError:
            raise HTTPException(502, "Could not reach GitHub")
        files = parse_diff(diff)
        if not files:
            raise HTTPException(422, "The pull request contains no reviewable changes")
        title, source, ref = f"{repo}#{num}: {meta.get('title', '')}"[:200], "github_pr", body.pr_url
        head_sha = meta["head"]["sha"]
        cfg = explicit or await _repo_config(owner, repo, head_sha)
        meta = {"owner": owner, "repo": repo, "number": num, "head_sha": head_sha}
        if body.post_to_github and s.github_token:
            on_done = await _post_back(owner, repo, num, head_sha)
    else:
        files = [SourceFile(path=f.path, content=f.content, language=detect_language(f.path)) for f in body.files]
        title, source, ref = body.title or files[0].path, "paste", None
    r = _new_review(db, title, source, ref, files, cfg, meta)
    runner.start(r.id, files, on_done)
    return r


@router.get("/reviews", response_model=list[ReviewSummary])
def list_reviews(limit: int = Query(30, le=100), db: Session = Depends(get_db)):
    return db.scalars(select(Review).order_by(Review.created_at.desc()).limit(limit)).all()


@router.get("/reviews/{review_id}", response_model=ReviewOut)
def get_review(review_id: str, db: Session = Depends(get_db)):
    r = db.get(Review, review_id)
    if not r:
        raise HTTPException(404, "Review not found")
    return r


@router.delete("/reviews/{review_id}", status_code=204)
def delete_review(review_id: str, db: Session = Depends(get_db)):
    r = db.get(Review, review_id)
    if not r:
        raise HTTPException(404, "Review not found")
    db.delete(r)
    db.commit()


@router.get("/reviews/{review_id}/events")
async def stream_events(review_id: str, db: Session = Depends(get_db)):
    """Server-sent events: replay what already happened, then follow live until the review ends."""
    r = db.get(Review, review_id)
    if not r:
        raise HTTPException(404, "Review not found")
    finished_events = list(r.events) if r.status in ("done", "failed") else None

    async def gen():
        def sse(ev: dict) -> str:
            return f"data: {json.dumps(ev)}\n\n"

        if finished_events is not None:
            for ev in finished_events:
                yield sse(ev)
            yield sse({"node": "complete", "status": "done", "data": {}})
            return
        past, q = hub.subscribe(review_id)
        try:
            for ev in past:
                yield sse(ev)
            if any(e["node"] == "complete" for e in past):
                return
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                if ev is None:
                    return
                yield sse(ev)
        finally:
            hub.unsubscribe(review_id, q)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.patch("/findings/{finding_id}")
def triage_finding(finding_id: int, body: TriageIn, db: Session = Depends(get_db)):
    f = db.get(FindingRow, finding_id)
    if not f:
        raise HTTPException(404, "Finding not found")
    f.triage = body.triage
    db.commit()
    return {"id": f.id, "triage": f.triage}


@router.get("/rules/search")
async def search_rules(q: str = Query(min_length=2, max_length=200), category: str | None = None, k: int = Query(5, le=10)):
    index = await asyncio.to_thread(get_index)
    return await asyncio.to_thread(index.search, q, k, category)


@router.post("/evaluation/run")
async def run_evaluation():
    """Score the deterministic pipeline against the labelled cases in eval/cases.json (no LLM, no network)."""
    from app import evaluation
    return await evaluation.evaluate()


@router.get("/stats")
def stats(db: Session = Depends(get_db)):
    done = db.scalars(select(Review).where(Review.status == "done").order_by(Review.created_at.desc()).limit(30)).all()
    sev = dict(db.execute(select(FindingRow.severity, func.count()).group_by(FindingRow.severity)).all())
    cat = dict(db.execute(select(FindingRow.category, func.count()).group_by(FindingRow.category)).all())
    top = db.execute(select(FindingRow.title, func.count().label("n")).group_by(FindingRow.title)
                     .order_by(func.count().desc()).limit(6)).all()
    return {
        "reviews": db.scalar(select(func.count()).select_from(Review)) or 0,
        "avg_score": round(sum(r.score for r in done) / len(done), 1) if done else None,
        "avg_duration_s": round(sum(r.duration_s or 0 for r in done) / len(done), 1) if done else None,
        "by_severity": sev, "by_category": cat, "top_issues": [{"title": t, "count": n} for t, n in top],
        "score_trend": [{"id": r.id, "title": r.title, "score": r.score, "at": r.created_at.isoformat()} for r in reversed(done)],
    }


@router.post("/webhooks/github", status_code=202)
async def github_webhook(request: Request, db: Session = Depends(get_db)):
    secret = get_settings().github_webhook_secret
    if not secret:
        raise HTTPException(404, "Webhook not configured")
    body = await request.body()
    if not github.verify_signature(secret, body, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(401, "Bad signature")
    if request.headers.get("X-GitHub-Event") != "pull_request":
        return {"ignored": "event"}
    payload = json.loads(body)
    if payload.get("action") not in ("opened", "synchronize", "reopened"):
        return {"ignored": "action"}
    pr, repo = payload["pull_request"], payload["repository"]
    owner, name = repo["owner"]["login"], repo["name"]
    try:
        diff, _ = await github.fetch_pr(owner, name, pr["number"])
    except github.GitHubError as e:
        raise HTTPException(502, str(e))
    files = parse_diff(diff)
    if not files:
        return {"ignored": "no reviewable changes"}
    sha = pr["head"]["sha"]
    cfg = await _repo_config(owner, name, sha)
    r = _new_review(db, f"{name}#{pr['number']}: {pr['title']}"[:200], "webhook", pr["html_url"], files, cfg,
                    {"owner": owner, "repo": name, "number": pr["number"], "head_sha": sha})
    runner.start(r.id, files, await _post_back(owner, name, pr["number"], sha))
    return {"review_id": r.id}


# ---------------------------------------------------------------------------- quality gate, exports, rerun
def _done_review(db: Session, review_id: str) -> Review:
    r = db.get(Review, review_id)
    if not r:
        raise HTTPException(404, "Review not found")
    if r.status != "done":
        raise HTTPException(409, f"Review is {r.status}; wait until it is done")
    return r


@router.get("/reviews/{review_id}/gate")
def quality_gate(review_id: str, fail_on: str | None = Query(None, description="comma-separated severities, e.g. critical,high"),
                 max_new_medium: int | None = Query(None, ge=0), min_score: int | None = Query(None, ge=0, le=100),
                 only_new: bool | None = None, db: Session = Depends(get_db)):
    """Pass/fail for CI. Uses the review's stored config unless overridden here. HTTP 200 either way: read `passed`."""
    r = _done_review(db, review_id)
    base = GateConfig.model_validate((r.config or {}).get("gate", {}))
    upd = {}
    if fail_on is not None:
        try:
            upd["fail_on"] = [x.strip() for x in fail_on.split(",") if x.strip()]
            GateConfig(**upd)
        except Exception:
            raise HTTPException(422, "fail_on must be a comma-separated list of: critical, high, medium, low, info")
    if max_new_medium is not None:
        upd["max_new_medium"] = max_new_medium
    if min_score is not None:
        upd["min_score"] = min_score
    if only_new is not None:
        upd["only_new"] = only_new
    cfg = GateConfig.model_validate({**base.model_dump(), **upd})
    rows = [{"severity": f.severity, "triage": f.triage, "is_new": f.is_new} for f in r.findings]
    out = gate_svc.evaluate(rows, r.score, cfg, has_baseline=bool(r.baseline_id))
    out["review_id"] = r.id
    return out


@router.get("/reviews/{review_id}/sarif")
def sarif(review_id: str, db: Session = Depends(get_db)):
    """SARIF 2.1.0: upload to GitHub code scanning (github/codeql-action/upload-sarif) to see findings in the Security tab."""
    r = _done_review(db, review_id)
    return Response(json.dumps(build_sarif(r, r.findings)), media_type="application/sarif+json",
                    headers={"Content-Disposition": f"attachment; filename=review-{r.id[:8]}.sarif"})


@router.get("/reviews/{review_id}/patch.diff")
def patch(review_id: str, db: Session = Depends(get_db)):
    """A unified diff of every suggested fix, ready for `git apply`. Pasted code only (PR reviews see just the changed lines)."""
    r = _done_review(db, review_id)
    if r.source != "paste":
        raise HTTPException(409, "Patches are only generated for pasted code. For pull requests the fixes are posted as one-click suggestions.")
    diff, n = build_patch(r.files, r.findings)
    if not n:
        raise HTTPException(404, "No finding in this review has an automatic fix")
    return Response(diff, media_type="text/x-diff", headers={"X-Fixes-Applied": str(n), "Content-Disposition": f"attachment; filename=review-{r.id[:8]}.diff"})


@router.post("/reviews/{review_id}/rerun", status_code=202)
async def rerun_review(review_id: str, db: Session = Depends(get_db)):
    r = db.get(Review, review_id)
    if not r:
        raise HTTPException(404, "Review not found")
    if r.status in ("queued", "running"):
        raise HTTPException(409, "Review is already in progress")
    runner.rerun(review_id)
    return {"restarted": review_id}

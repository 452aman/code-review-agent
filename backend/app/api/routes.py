import asyncio
import json
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import github, runner
from app.analysis.diff import parse_diff
from app.core.config import get_settings
from app.core.types import SourceFile, detect_language
from app.db.models import FindingRow, Review, get_db
from app.rag.index import get_index
from app.schemas import ReviewIn, ReviewOut, ReviewSummary, TriageIn
from app.services_events import hub

log = logging.getLogger(__name__)
router = APIRouter()


def _new_review(db: Session, title: str, source: str, ref: str | None, files: list[SourceFile]) -> Review:
    r = Review(title=title, source=source, source_ref=ref, files=[f.model_dump() for f in files])
    db.add(r)
    db.commit()
    return r


async def _post_back(owner: str, repo: str, num: int, head_sha: str):
    async def on_done(review_id: str, result: dict) -> None:
        comments = [{"path": f.file, "line": f.line, "side": "RIGHT",
                     "body": f"**{f.severity.value.upper()}** · {f.title}\n\n{f.explanation}\n\n_Suggestion:_ {f.suggestion}"}
                    for f in result["findings"] if f.severity.value in ("critical", "high", "medium")]
        try:
            await github.post_review(owner, repo, num, head_sha,
                                     f"**Automated review: {result['grade']} ({result['score']}/100)**\n\n{result['summary']}", comments)
        except Exception:
            log.exception("posting review to GitHub failed")
    return on_done


@router.post("/reviews", response_model=ReviewSummary, status_code=202)
async def create_review(body: ReviewIn, db: Session = Depends(get_db)):
    s = get_settings()
    on_done = None
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
        if body.post_to_github and s.github_token:
            on_done = await _post_back(owner, repo, num, meta["head"]["sha"])
    else:
        files = [SourceFile(path=f.path, content=f.content, language=detect_language(f.path)) for f in body.files]
        title, source, ref = body.title or files[0].path, "paste", None
    r = _new_review(db, title, source, ref, files)
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
    r = _new_review(db, f"{name}#{pr['number']}: {pr['title']}"[:200], "webhook", pr["html_url"], files)
    runner.start(r.id, files, await _post_back(owner, name, pr["number"], pr["head"]["sha"]))
    return {"review_id": r.id}

"""Runs a review in the background: executes the graph, applies repo config, compares to a baseline, grades the gate,
streams events and persists everything. Reviews are recoverable: the source files live in the database."""
import asyncio
import logging
import time

from sqlalchemy import delete, select

from app.agents.graph import run_review, score_of
from app.agents.llm import ReviewLLM, make_llm
from app.core.types import Category, Severity, SourceFile
from app.db.models import FindingRow, Review, SessionLocal
from app.observability import metrics
from app.rag.index import get_index
from app.services import fingerprint as fp
from app.services import gate as gate_svc
from app.services.config import ReviewConfig, apply_config
from app.services_events import hub

log = logging.getLogger(__name__)
_llm: ReviewLLM | None = None
_tasks: set[asyncio.Task] = set()
_sem = asyncio.Semaphore(3)  # bound concurrent reviews so we don't blow LLM rate limits


def get_llm() -> ReviewLLM:
    global _llm
    if _llm is None:
        _llm = make_llm()
    return _llm


def set_llm(llm: ReviewLLM | None) -> None:
    global _llm
    _llm = llm


def start(review_id: str, files: list[SourceFile], on_done=None) -> None:
    task = asyncio.create_task(_run(review_id, files, on_done))
    _tasks.add(task)  # keep a reference so the task isn't garbage collected mid-flight
    task.add_done_callback(_tasks.discard)


def find_baseline(db, review: Review) -> Review | None:
    """The previous finished review of the same PR (or, for pasted code, the same title) to diff against."""
    q = select(Review).where(Review.status == "done", Review.id != review.id, Review.created_at < review.created_at)
    q = q.where(Review.source_ref == review.source_ref) if review.source_ref else q.where(Review.source == review.source, Review.title == review.title)
    return db.scalars(q.order_by(Review.created_at.desc()).limit(1)).first()


def _stats(findings, rejected: int, suppressed: int, new: int, existing: int, fixed: list[dict]) -> dict:
    return {"by_severity": {s.value: sum(1 for f in findings if f.severity == s) for s in Severity},
            "by_category": {c.value: sum(1 for f in findings if f.category == c) for c in Category},
            "rejected_hallucinations": rejected, "suppressed_by_config": suppressed,
            "new": new, "existing": existing, "fixed": len(fixed), "fixed_items": fixed[:20]}


async def _run(review_id: str, files: list[SourceFile], on_done) -> None:
    async def emit(node: str, status: str, data: dict) -> None:
        await hub.publish(review_id, node, status, data)

    async with _sem:
        t0 = time.perf_counter()
        with SessionLocal() as db:
            r = db.get(Review, review_id)
            r.status = "running"
            cfg = ReviewConfig.model_validate(r.config or {})
            db.commit()
        try:
            index = await asyncio.to_thread(get_index)
            result = await asyncio.wait_for(run_review(files, get_llm(), index, emit), timeout=180)

            findings, suppressed = apply_config(result["findings"], cfg)       # repo rules can silence or re-grade findings
            score, grade = score_of(findings)
            content = {f.path: f.content for f in files}
            prints = [fp.fingerprint(f.file, f.category.value, fp.line_of(content.get(f.file, ""), f.line), f.rule_id) for f in findings]

            with SessionLocal() as db:
                r = db.get(Review, review_id)
                base = find_baseline(db, r)
                base_prints: dict[str, str] = {}
                if base:
                    r.baseline_id = base.id
                    base_prints = {b.fingerprint: b.title for b in db.scalars(select(FindingRow).where(FindingRow.review_id == base.id)) if b.fingerprint}
                is_new = [p not in base_prints for p in prints]
                fixed = [{"title": t} for p, t in base_prints.items() if p not in set(prints)]
                rows = []
                for f, p, n in zip(findings, prints, is_new):
                    row = FindingRow(review_id=review_id, fingerprint=p, is_new=n, **f.model_dump(mode="json"))
                    db.add(row)
                    rows.append(row)
                db.flush()
                gate = gate_svc.evaluate([{"severity": x.severity, "triage": x.triage, "is_new": x.is_new} for x in rows], score, cfg.gate, has_baseline=bool(base))
                r.status, r.score, r.grade, r.summary = "done", score, grade, result["summary"]
                r.stats = _stats(findings, result["stats"]["rejected_hallucinations"], suppressed, sum(is_new), len(is_new) - sum(is_new), fixed)
                r.gate = gate
                r.duration_s = round(time.perf_counter() - t0, 2)
                r.events = hub.history.get(review_id, [])
                db.commit()
            metrics.inc("reviews_completed_total")
            if not gate["passed"]:
                metrics.inc("quality_gate_failures_total")
            await emit("complete", "done", {"score": score, "grade": grade, "gate": gate["passed"]})
            if on_done:
                result = {**result, "findings": findings, "score": score, "grade": grade, "gate": gate, "is_new": is_new, "has_baseline": bool(base)}
                await on_done(review_id, result)
        except Exception as e:
            log.exception("review %s failed", review_id)
            metrics.inc("reviews_failed_total")
            with SessionLocal() as db:
                r = db.get(Review, review_id)
                r.status, r.error = "failed", str(e)[:500]
                r.duration_s = round(time.perf_counter() - t0, 2)
                r.events = hub.history.get(review_id, [])
                db.commit()
            await emit("complete", "error", {"message": str(e)[:200]})
        finally:
            await hub.close(review_id)
            await asyncio.sleep(5)  # let slow SSE clients drain before dropping history
            hub.forget(review_id)


def _files_of(r: Review) -> list[SourceFile]:
    return [SourceFile(**f) for f in r.files]


def rerun(review_id: str, on_done=None) -> bool:
    """Run a review again from its stored files (after a failure, or because the LLM was down)."""
    with SessionLocal() as db:
        r = db.get(Review, review_id)
        if not r:
            return False
        db.execute(delete(FindingRow).where(FindingRow.review_id == review_id))
        r.status, r.error, r.score, r.grade, r.summary, r.gate, r.stats, r.events = "queued", None, None, None, "", {}, {}, []
        files = _files_of(r)
        db.commit()
    hub.history.pop(review_id, None)
    start(review_id, files, on_done)
    return True


def recover_stuck() -> int:
    """At startup: reviews a crash left queued/running are started again from their stored files."""
    with SessionLocal() as db:
        ids = list(db.scalars(select(Review.id).where(Review.status.in_(["queued", "running"]))))
    for rid in ids:
        rerun(rid)
    return len(ids)

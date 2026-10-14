"""Runs a review in the background: executes the graph, streams events, persists results."""
import asyncio
import logging
import time

from app.agents.graph import run_review
from app.agents.llm import ReviewLLM, make_llm
from app.core.types import SourceFile
from app.db.models import FindingRow, Review, SessionLocal
from app.rag.index import get_index
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


async def _run(review_id: str, files: list[SourceFile], on_done) -> None:
    async def emit(node: str, status: str, data: dict) -> None:
        await hub.publish(review_id, node, status, data)

    async with _sem:
        t0 = time.perf_counter()
        with SessionLocal() as db:
            r = db.get(Review, review_id)
            r.status = "running"
            db.commit()
        try:
            index = await asyncio.to_thread(get_index)
            result = await asyncio.wait_for(run_review(files, get_llm(), index, emit), timeout=180)
            with SessionLocal() as db:
                r = db.get(Review, review_id)
                r.status, r.score, r.grade = "done", result["score"], result["grade"]
                r.summary, r.stats = result["summary"], result["stats"]
                r.duration_s = round(time.perf_counter() - t0, 2)
                for f in result["findings"]:
                    db.add(FindingRow(review_id=review_id, **f.model_dump(mode="json")))
                r.events = hub.history.get(review_id, [])
                db.commit()
            await emit("complete", "done", {"score": result["score"], "grade": result["grade"]})
            if on_done:
                await on_done(review_id, result)
        except Exception as e:
            log.exception("review %s failed", review_id)
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

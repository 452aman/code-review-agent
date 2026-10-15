import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text

from app import observability, runner
from app.api.routes import router
from app.core.config import get_settings
from app.db.models import Base, SessionLocal, engine, ensure_schema
from app.rag.index import get_index

log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)
    ensure_schema()                                 # upgrade databases created before gating / baselines existed
    await asyncio.to_thread(get_index)              # load embedding model + build FAISS index once, at startup
    runner.get_llm()
    resumed = runner.recover_stuck()                # reviews a crash left in flight are started again from their stored files
    if resumed:
        log.info("resumed interrupted reviews", extra={"extra_fields": {"count": resumed}})
    yield


app = FastAPI(title="Code Review Agent API", version="3.0.0", lifespan=lifespan)
app.state.limiter = observability.limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(CORSMiddleware, allow_origins=get_settings().cors_origins, allow_methods=["*"], allow_headers=["*"], expose_headers=["X-Request-ID", "X-Fixes-Applied"])
observability.install(app)
app.include_router(router, prefix="/api/v1")


@app.get("/health")
def health():
    llm = runner.get_llm()
    return {"status": "ok", "llm": "groq" if llm.available else "disabled (static analysis only)"}


@app.get("/ready")
def ready():
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
    except Exception:
        raise HTTPException(503, "database unavailable")
    return {"status": "ready"}

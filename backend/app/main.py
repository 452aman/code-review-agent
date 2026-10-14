import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.core.config import get_settings
from app.db.models import Base, engine
from app.rag.index import get_index
from app.runner import get_llm


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)
    await asyncio.to_thread(get_index)  # load embedding model + build FAISS index once, at startup
    get_llm()
    yield


app = FastAPI(title="Code Review Agent API", version="2.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=get_settings().cors_origins, allow_methods=["*"], allow_headers=["*"])
app.include_router(router, prefix="/api/v1")


@app.get("/health")
def health():
    llm = get_llm()
    return {"status": "ok", "llm": "groq" if llm.available else "disabled (static analysis only)"}

"""FAISS-backed semantic search over the review knowledge base."""
import hashlib
import re
import threading

import faiss
import numpy as np

from app.core.config import get_settings
from app.rag.knowledge import KB

DIM_HASH = 512


class HashEmbedder:
    """Offline fallback: signed feature hashing of word unigrams+bigrams. Deterministic, no downloads."""

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), DIM_HASH), dtype="float32")
        for row, t in enumerate(texts):
            words = re.findall(r"[a-z0-9_]+", t.lower())
            for tok in words + [f"{a}_{b}" for a, b in zip(words, words[1:])]:
                h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
                out[row, h % DIM_HASH] += 1.0 if (h >> 64) & 1 else -1.0
        return out


class FastEmbedder:
    def __init__(self) -> None:
        from fastembed import TextEmbedding

        self.model = TextEmbedding("BAAI/bge-small-en-v1.5")

    def embed(self, texts: list[str]) -> np.ndarray:
        return np.array(list(self.model.embed(texts)), dtype="float32")


class KnowledgeIndex:
    def __init__(self, embedder=None) -> None:
        if embedder is None:
            embedder = FastEmbedder() if get_settings().embedding_backend == "fastembed" else HashEmbedder()
        self.embedder = embedder
        self.docs = KB
        vecs = self._norm(embedder.embed([f"{d['title']}. {d['text']}" for d in KB]))
        self.index = faiss.IndexFlatIP(vecs.shape[1])  # inner product on unit vectors == cosine similarity
        self.index.add(vecs)

    @staticmethod
    def _norm(v: np.ndarray) -> np.ndarray:
        n = np.linalg.norm(v, axis=1, keepdims=True)
        return v / np.maximum(n, 1e-9)

    def search(self, query: str, k: int = 4, category: str | None = None) -> list[dict]:
        q = self._norm(self.embedder.embed([query]))
        scores, ids = self.index.search(q, len(self.docs))  # tiny corpus: rank all, filter, then cut
        hits = []
        for s, i in zip(scores[0], ids[0]):
            d = self.docs[int(i)]
            if category and d["category"] != category:
                continue
            hits.append({**d, "score": round(float(s), 3)})
            if len(hits) == k:
                break
        return hits


_lock = threading.Lock()
_instance: KnowledgeIndex | None = None


def get_index() -> KnowledgeIndex:
    global _instance
    with _lock:
        if _instance is None:
            _instance = KnowledgeIndex()
        return _instance

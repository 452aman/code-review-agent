import os
import tempfile

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["EMBEDDING_BACKEND"] = "hash"
os.environ["GROQ_API_KEY"] = ""
os.environ["REVIEW_RATE_LIMIT"] = "12/minute"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import runner  # noqa: E402
from app.agents.llm import AgentFinding, NullLLM  # noqa: E402
from app.core.types import Severity  # noqa: E402
from app.db.models import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


class FakeLLM:
    available = True

    async def review(self, system, user):
        if "application security" in system:
            return [AgentFinding(file="app.py", line=4, severity=Severity.high, title="Unvalidated input reaches eval",
                                 explanation="User controlled.", suggestion="Remove eval.", fix="x = json.loads(s)"),
                    AgentFinding(file="app.py", line=500, severity=Severity.high, title="Invented", explanation="", suggestion="")]
        return []

    async def summarize(self, prompt):
        return "Fake summary."


@pytest.fixture()
def client():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    runner.set_llm(NullLLM())
    app.state.limiter.reset()
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def fake_llm():
    runner.set_llm(FakeLLM())
    yield
    runner.set_llm(NullLLM())

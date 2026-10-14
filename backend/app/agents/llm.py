"""LLM access behind a small interface, so the pipeline is testable and degrades gracefully."""
import asyncio
import logging
import re
from typing import Protocol

from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.types import Severity

log = logging.getLogger(__name__)


class AgentFinding(BaseModel):
    file: str
    line: int = Field(ge=1, description="1-based line number in the numbered listing")
    severity: Severity
    title: str = Field(max_length=140)
    explanation: str = Field(description="Why this is a problem, one or two sentences")
    suggestion: str = Field(description="Concrete remediation")
    fix: str | None = Field(default=None, description="Replacement code for the flagged line, if you can give one")
    confidence: float = Field(default=0.7, ge=0, le=1)


class AgentReport(BaseModel):
    findings: list[AgentFinding] = Field(default_factory=list)


class ReviewLLM(Protocol):
    available: bool

    async def review(self, system: str, user: str) -> list[AgentFinding]: ...
    async def summarize(self, prompt: str) -> str: ...


class NullLLM:
    """Used when no API key is configured: the pipeline then runs static analysis + retrieval only."""

    available = False

    async def review(self, system: str, user: str) -> list[AgentFinding]:
        return []

    async def summarize(self, prompt: str) -> str:
        return ""


_RETRY_HINT = re.compile(r"try again in ([\d.]+)\s*(ms|s)", re.I)


def _rate_limit_wait(exc: Exception) -> float | None:
    """Seconds to wait if `exc` is a rate-limit error (honours the server's hint), else None."""
    text = str(exc)
    if "429" not in text and "rate_limit" not in text:
        return None
    m = _RETRY_HINT.search(text)
    if not m:
        return 5.0
    secs = float(m.group(1)) / (1000 if m.group(2).lower() == "ms" else 1)
    return min(secs + 0.5, 20.0)


class GroqLLM:
    available = True
    _gate = asyncio.Semaphore(2)  # at most two in-flight calls: parallel agents otherwise burst past per-minute token limits

    def __init__(self) -> None:
        from langchain_groq import ChatGroq

        s = get_settings()
        self._chat = ChatGroq(model=s.llm_model, api_key=s.groq_api_key, temperature=0, timeout=s.llm_timeout_s, max_retries=2)
        self._structured = self._chat.with_structured_output(AgentReport)

    async def review(self, system: str, user: str) -> list[AgentFinding]:
        for attempt in range(4):
            try:
                async with self._gate:
                    report: AgentReport = await asyncio.wait_for(
                        self._structured.ainvoke([("system", system), ("human", user)]), timeout=get_settings().llm_timeout_s + 5)
                return report.findings
            except Exception as e:
                wait = _rate_limit_wait(e)
                if wait is None or attempt == 3:
                    raise
                log.info("rate limited; retrying in %.1fs", wait)
                await asyncio.sleep(wait)
        return []

    async def summarize(self, prompt: str) -> str:
        for attempt in range(3):
            try:
                async with self._gate:
                    r = await asyncio.wait_for(self._chat.ainvoke([("human", prompt)]), timeout=get_settings().llm_timeout_s)
                return str(r.content).strip()
            except Exception as e:
                wait = _rate_limit_wait(e)
                if wait is None or attempt == 2:
                    raise
                await asyncio.sleep(wait)
        return ""


def make_llm() -> ReviewLLM:
    return GroqLLM() if get_settings().groq_api_key else NullLLM()

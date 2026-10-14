"""The review pipeline as a LangGraph state machine.

ingest -> static_analysis -> retrieve ->  [security | bugs | performance | maintainability]  (parallel)
       -> verify -> summarize
"""
import asyncio
import logging
import operator
import re
from typing import Annotated, Any, Awaitable, Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agents.llm import AgentFinding, ReviewLLM
from app.analysis.static import analyze
from app.core.config import get_settings
from app.core.types import SEVERITY_ORDER, SEVERITY_WEIGHT, Category, Finding, Severity, Source, SourceFile
from app.rag.index import KnowledgeIndex

log = logging.getLogger(__name__)

Emit = Callable[[str, str, dict], Awaitable[None]]

AGENTS = {
    "security": (Category.security, Source.security_agent,
                 "application security: injection, authn/authz, secrets, crypto, unsafe deserialization, SSRF, XSS, path traversal"),
    "bugs": (Category.bug, Source.bug_agent,
             "correctness: logic errors, null handling, off-by-one, races, resource leaks, exception handling, async misuse"),
    "performance": (Category.performance, Source.performance_agent,
                    "performance: N+1 queries, quadratic algorithms, missing timeouts, unbounded memory, repeated work"),
    "maintainability": (Category.maintainability, Source.maintainability_agent,
                        "maintainability: complexity, duplication, naming, missing validation/types, testability"),
}

MAX_CHARS_PER_FILE = 14_000
MAX_CHARS_TOTAL = 40_000


class ReviewState(TypedDict, total=False):
    files: list[SourceFile]
    static: list[Finding]
    context: dict[str, list[dict]]
    agent_findings: Annotated[list[Finding], operator.add]
    findings: list[Finding]
    summary: str
    score: int
    grade: str
    stats: dict


def numbered(f: SourceFile) -> str:
    lines = f.content.splitlines()
    return "\n".join(f"{i:>4}| {l}" for i, l in enumerate(lines, 1))[:MAX_CHARS_PER_FILE]


def build_listing(files: list[SourceFile]) -> str:
    parts, used = [], 0
    for f in files:
        block = f"### FILE: {f.path}  (language: {f.language})"
        if f.changed_lines is not None:
            block += f"\nOnly report issues on changed lines: {f.changed_lines[:200]}"
        block += f"\n{numbered(f)}"
        if used + len(block) > MAX_CHARS_TOTAL:
            parts.append(f"### FILE: {f.path} (omitted: review size budget reached)")
            continue
        used += len(block)
        parts.append(block)
    return "\n\n".join(parts)


_WORD = re.compile(r"[a-z0-9]+")
_GENERIC = {"a", "the", "of", "in", "to", "and", "is", "via", "on", "with", "for", "use", "using", "not", "no"}


def _title_words(f: Finding) -> set[str]:
    title = re.sub(r"[-\u2010-\u2015\u2212_]", "", f.title.lower())  # "hard-coded" == "hardcoded"
    return {w for w in _WORD.findall(title) if w not in _GENERIC and len(w) > 2}


def same_issue(a: Finding, b: Finding) -> bool:
    if a.line == b.line and a.category == b.category and a.rule_id and a.rule_id == b.rule_id:
        return True
    wa, wb = _title_words(a), _title_words(b)
    if not wa or not wb:
        return False
    overlap = len(wa & wb) / min(len(wa), len(wb))  # containment, so a short title matches a longer one
    return overlap >= 0.5


def score_of(findings: list[Finding]) -> tuple[int, str]:
    penalty = sum(SEVERITY_WEIGHT[f.severity] for f in findings)
    score = round(100 * 0.5 ** (penalty / 60))  # halves every 60 penalty points: never collapses to 0 on a noisy review
    grade = "A" if score >= 90 else "B" if score >= 75 else "C" if score >= 60 else "D" if score >= 40 else "F"
    return score, grade


def build_graph(llm: ReviewLLM, index: KnowledgeIndex):
    def emit_of(config) -> Emit:
        return config["configurable"]["emit"]

    async def ingest(state: ReviewState, config):
        emit = emit_of(config)
        s = get_settings()
        files = [f for f in state["files"] if f.content.strip()][: s.max_files]
        files = [f.model_copy(update={"content": f.content[: s.max_file_bytes]}) for f in files]
        await emit("ingest", "done", {"files": len(files), "lines": sum(len(f.content.splitlines()) for f in files)})
        return {"files": files}

    async def static_analysis(state: ReviewState, config):
        emit = emit_of(config)
        await emit("static_analysis", "running", {})
        found = [x for f in state["files"] for x in analyze(f)]
        await emit("static_analysis", "done", {"findings": len(found)})
        return {"static": found}

    async def retrieve(state: ReviewState, config):
        emit = emit_of(config)
        await emit("retrieve", "running", {})
        ctx: dict[str, list[dict]] = {}
        for name, (cat, _, _) in AGENTS.items():
            leads = " ".join(x.title for x in state["static"] if x.category == cat)
            sample = " ".join(f.content[:600] for f in state["files"][:3])
            ctx[name] = await asyncio.to_thread(index.search, f"{cat.value} {leads} {sample}", 4, cat.value)
        await emit("retrieve", "done", {"rules": sum(len(v) for v in ctx.values())})
        return {"context": ctx}

    def make_agent(name: str):
        cat, source, focus = AGENTS[name]

        async def run(state: ReviewState, config):
            emit = emit_of(config)
            if not llm.available:
                await emit(name, "skipped", {"reason": "no LLM key configured"})
                return {"agent_findings": []}
            await emit(name, "running", {})
            rules = "\n".join(f"- [{r['id']}] {r['title']}: {r['text']}" for r in state["context"].get(name, []))
            leads = "\n".join(f"- {x.file}:{x.line} {x.title}" for x in state["static"] if x.category == cat) or "none"
            system = (
                f"You are a senior engineer reviewing code for {focus}.\n"
                "Report ONLY issues in your own area; other specialists cover the rest, so skip anything outside it. "
                "Report only real, specific issues you can point to a line for; do not invent problems and do not pad.\n"
                "Line numbers MUST come from the numbered listing. Prefer fewer, higher-confidence findings. "
                "Use severity critical only for exploitable or data-loss bugs.\n"
                f"Relevant review guidance:\n{rules}\n"
                f"Static analyzers already flagged these (don't repeat them, but find what they missed):\n{leads}"
            )
            try:
                raw: list[AgentFinding] = await llm.review(system, build_listing(state["files"]))
            except Exception as e:  # network, rate limit, bad structured output: degrade, don't fail the review
                log.warning("agent %s failed: %s", name, e)
                await emit(name, "error", {"message": str(e)[:200]})
                return {"agent_findings": []}
            out = [Finding(file=r.file, line=r.line, severity=r.severity, category=cat, title=r.title, explanation=r.explanation,
                           suggestion=r.suggestion, fix=r.fix, source=source, confidence=r.confidence) for r in raw]
            await emit(name, "done", {"findings": len(out)})
            return {"agent_findings": out}

        return run

    async def verify(state: ReviewState, config):
        """Reject hallucinated locations, drop out-of-scope lines, merge duplicates, rank."""
        emit = emit_of(config)
        await emit("verify", "running", {})
        by_path = {f.path: f for f in state["files"]}
        kept: list[Finding] = list(state.get("static", []))
        rejected = 0
        for f in state.get("agent_findings", []):
            src = by_path.get(f.file)
            if not src or f.line > len(src.content.splitlines()):
                rejected += 1
                continue
            if src.changed_lines is not None and f.line not in set(src.changed_lines):
                rejected += 1
                continue
            kept.append(f)
        # Rank so that static findings (trusted) and higher severities anchor each cluster.
        kept.sort(key=lambda f: (SEVERITY_ORDER[f.severity], f.source != Source.static))
        merged: list[Finding] = []
        for f in kept:
            twin = next((m for m in merged if m.file == f.file and abs(m.line - f.line) <= 1 and same_issue(m, f)), None)
            if twin is None:
                merged.append(f)
            else:  # same problem flagged twice: keep the anchor, enrich it with whatever the duplicate adds
                idx = merged.index(twin)
                merged[idx] = twin.model_copy(update={
                    "explanation": twin.explanation or f.explanation, "fix": twin.fix or f.fix,
                    "suggestion": twin.suggestion or f.suggestion, "confidence": max(twin.confidence, f.confidence)})
        final = sorted(merged, key=lambda f: (SEVERITY_ORDER[f.severity], f.file, f.line))
        score, grade = score_of(final)
        counts = {s.value: sum(1 for f in final if f.severity == s) for s in Severity}
        await emit("verify", "done", {"kept": len(final), "rejected": rejected})
        return {"findings": final, "score": score, "grade": grade,
                "stats": {"by_severity": counts, "rejected_hallucinations": rejected,
                          "by_category": {c.value: sum(1 for f in final if f.category == c) for c in Category}}}

    async def summarize(state: ReviewState, config):
        emit = emit_of(config)
        await emit("summarize", "running", {})
        fs = state["findings"]
        top = "\n".join(f"- [{f.severity.value}] {f.file}:{f.line} {f.title}" for f in fs[:12])
        text = ""
        if llm.available and fs:
            try:
                text = await llm.summarize(
                    "Write a 3-4 sentence code review summary for a pull request author. Be direct and specific, "
                    f"mention the most serious problems first, no greeting.\nScore {state['score']}/100.\nFindings:\n{top}")
            except Exception as e:
                log.warning("summary failed: %s", e)
        if not text:
            sev = state["stats"]["by_severity"]
            text = (f"Reviewed {len(state['files'])} file(s): {len(fs)} issue(s) found "
                    f"({sev['critical']} critical, {sev['high']} high, {sev['medium']} medium). "
                    + (f"Start with: {fs[0].title} ({fs[0].file}:{fs[0].line})." if fs else "No issues detected."))
        await emit("summarize", "done", {})
        return {"summary": text}

    g = StateGraph(ReviewState)
    g.add_node("ingest", ingest)
    g.add_node("static_analysis", static_analysis)
    g.add_node("retrieve", retrieve)
    for name in AGENTS:
        g.add_node(name, make_agent(name))
    g.add_node("verify", verify)
    g.add_node("summarize", summarize)
    g.add_edge(START, "ingest")
    g.add_edge("ingest", "static_analysis")
    g.add_edge("static_analysis", "retrieve")
    for name in AGENTS:
        g.add_edge("retrieve", name)
    g.add_edge(list(AGENTS), "verify")  # join: wait for all four specialists
    g.add_edge("verify", "summarize")
    g.add_edge("summarize", END)
    return g.compile()


async def run_review(files: list[SourceFile], llm: ReviewLLM, index: KnowledgeIndex, emit: Emit) -> dict[str, Any]:
    graph = build_graph(llm, index)
    return await graph.ainvoke({"files": files, "agent_findings": []}, config={"configurable": {"emit": emit}})

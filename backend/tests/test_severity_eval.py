import asyncio
import random

from app import evaluation
from app.agents.graph import run_review
from app.agents.llm import AgentFinding
from app.core.types import Category, Finding, Severity, Source, SourceFile
from app.rag.index import get_index
from app.services.severity import band_for, calibrate, tier
from tests.test_api import wait_done


def finding(title, severity, category, source=Source.security_agent, explanation=""):
    return Finding(file="a.py", line=1, severity=severity, category=category, title=title, explanation=explanation, source=source)


def test_a_style_nit_called_critical_is_capped():
    fixed, note = calibrate(finding("Inconsistent quotes", Severity.critical, Category.style, Source.maintainability_agent))
    assert fixed.severity == Severity.low
    assert note["from"] == "critical" and note["to"] == "low" and note["reason"]


def test_an_exploitable_flaw_marked_low_is_raised():
    fixed, note = calibrate(finding("SQL injection in search", Severity.low, Category.security))
    assert fixed.severity == Severity.high and note["to"] == "high"


def test_critical_is_only_reachable_for_exploitable_or_data_loss_kinds():
    assert band_for(finding("Race condition on counter", Severity.high, Category.bug)).cap == Severity.high
    assert band_for(finding("Hardcoded API key", Severity.high, Category.security)).cap == Severity.critical
    fixed, _ = calibrate(finding("Slow loop", Severity.critical, Category.performance, Source.performance_agent))
    assert fixed.severity == Severity.medium


def test_in_band_severity_is_left_alone_and_static_findings_are_never_touched():
    ok, note = calibrate(finding("Missing None check", Severity.medium, Category.bug, Source.bug_agent))
    assert ok.severity == Severity.medium and note is None
    static, note = calibrate(finding("Debug output left in code", Severity.critical, Category.style, Source.static))
    assert static.severity == Severity.critical and note is None


def test_three_reporting_tiers():
    assert [tier(s) for s in Severity] == ["high", "high", "medium", "low", "low"]


class MessyLLM:
    """Returns an inflated severity for a maintainability nit, to prove the pipeline corrects it."""
    available = True

    async def review(self, system, user):
        if "maintainability" in system:
            return [AgentFinding(file="app.py", line=3, severity=Severity.critical, title="Variable name could be clearer",
                                 explanation="Naming.", suggestion="Rename it.")]
        return []

    async def summarize(self, prompt):
        return "ok"


def test_pipeline_corrects_agent_severity_and_reports_it(client):
    from app import runner
    runner.set_llm(MessyLLM())
    try:
        rid = client.post("/api/v1/reviews", json={"files": [{"path": "app.py", "content": "a = 1\nb = 2\nx = 3\n"}]}).json()["id"]
        done = wait_done(client, rid)
    finally:
        from app.agents.llm import NullLLM
        runner.set_llm(NullLLM())
    nit = next(f for f in done["findings"] if f["title"].startswith("Variable name"))
    assert nit["severity"] == "medium"                       # capped from critical
    cal = done["stats"]["severity_calibration"]
    assert cal["adjusted"] == 1 and cal["changes"][0]["from"] == "critical"
    assert set(done["stats"]["by_tier"]) == {"high", "medium", "low"}
    assert any(e["node"] == "calibrate" for e in done["events"])


class JitteryLLM:
    """The four agents finish in a different order on every call: results must not depend on it."""
    available = True

    async def review(self, system, user):
        await asyncio.sleep(random.random() * 0.05)
        return [AgentFinding(file="app.py", line=2, severity=Severity.high, title="Unvalidated input reaches eval",
                             explanation="x", suggestion="y")]

    async def summarize(self, prompt):
        return "s"


def test_same_input_gives_the_same_output_regardless_of_agent_timing():
    async def once():
        async def emit(*a): return None
        r = await run_review([SourceFile(path="app.py", content="x = 1\ny = eval(x)\n", language="python")], JitteryLLM(), get_index(), emit)
        return [f.model_dump(mode="json") for f in r["findings"]], r["score"]
    runs = [asyncio.run(once()) for _ in range(5)]
    assert all(r == runs[0] for r in runs)


def test_evaluation_harness_scores_the_pipeline():
    res = asyncio.run(evaluation.evaluate())
    d = res["detection"]
    assert d["precision"] >= 0.95 and d["recall_without_agents"] == 1.0
    assert d["severity_3_tier"] == 1.0 and d["deterministic_runs"] == 1.0
    assert d["agent_only_found"] == 0 and d["agent_only_expected"] > 0     # honest: these need the LLM agents
    assert res["retrieval"]["hit@3"] >= 0.6      # tests use the offline hash embedder; the real model scores higher (see README)


def test_evaluation_endpoint(client):
    r = client.post("/api/v1/evaluation/run")
    assert r.status_code == 200 and r.json()["detection"]["cases"] >= 14

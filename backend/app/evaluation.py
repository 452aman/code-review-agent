"""Gradable evaluation of the review pipeline.

`eval/cases.json` holds labelled code samples (what a correct review must find, with the severity it should get, and
clean files that must produce nothing) plus labelled questions for the FAISS rubric corpus. Running this module scores
the real pipeline against those labels, so quality is a measured number instead of an impression:

    python -m app.evaluation          # human-readable report
    python -m app.evaluation --json   # machine-readable, for CI

Detection runs the deterministic stages (static analysis, retrieval, verification, severity rubric) with no LLM, so
the scores are reproducible offline. Pass a real LLM to `evaluate()` to measure the agents as well.
"""
import asyncio
import json
import sys
from pathlib import Path

from app.agents.graph import run_review
from app.agents.llm import NullLLM, ReviewLLM
from app.core.types import SourceFile, detect_language
from app.rag.index import KnowledgeIndex, get_index
from app.services.severity import tier

CASES_FILE = Path(__file__).resolve().parent.parent / "eval" / "cases.json"
LINE_TOLERANCE = 1


def load_cases(path: Path = CASES_FILE) -> dict:
    return json.loads(path.read_text())


def _line_of(code: str, anchor: str) -> int:
    for i, line in enumerate(code.splitlines(), 1):
        if anchor in line:
            return i
    raise ValueError(f"anchor {anchor!r} not found in case code")


async def _noop(node: str, status: str, data: dict) -> None:
    return None


def _ratio(num: int, den: int) -> float | None:
    return round(num / den, 3) if den else None


def _f1(p: float | None, r: float | None) -> float | None:
    return round(2 * p * r / (p + r), 3) if p and r else (0.0 if p is not None and r is not None else None)


async def evaluate_detection(cases: list[dict], llm: ReviewLLM, index: KnowledgeIndex) -> dict:
    tp = fp = fn = 0
    agent_total = agent_found = 0     # expected findings that only an LLM agent could catch (no static rule exists)
    sev_exact = sev_tier = matched = 0
    failures: list[dict] = []
    by_cat: dict[str, dict[str, int]] = {}
    identical = 0
    for case in cases:
        sf = SourceFile(path=case["path"], content=case["code"], language=detect_language(case["path"]))
        result = await run_review([sf], llm, index, _noop)
        again = await run_review([sf], llm, index, _noop)
        identical += [f.model_dump(mode="json") for f in result["findings"]] == [f.model_dump(mode="json") for f in again["findings"]]
        predicted = list(result["findings"])
        used: set[int] = set()
        for exp in case["expected"]:
            line = _line_of(case["code"], exp["anchor"])
            hit = next((i for i, p in enumerate(predicted) if i not in used and p.category.value == exp["category"]
                        and abs(p.line - line) <= LINE_TOLERANCE), None)
            cat = by_cat.setdefault(exp["category"], {"tp": 0, "fp": 0, "fn": 0})
            agent_total += bool(case.get("requires_llm"))
            if hit is None:
                fn += 1
                cat["fn"] += 1
                failures.append({"case": case["id"], "type": "missed", "expected": exp})
                continue
            used.add(hit)
            agent_found += bool(case.get("requires_llm"))
            tp += 1
            cat["tp"] += 1
            matched += 1
            got = predicted[hit].severity
            sev_exact += got.value == exp["severity"]
            sev_tier += tier(got) == tier(type(got)(exp["severity"]))
            if got.value != exp["severity"]:
                failures.append({"case": case["id"], "type": "severity", "expected": exp["severity"], "got": got.value})
        for i, p in enumerate(predicted):
            if i not in used:
                fp += 1
                by_cat.setdefault(p.category.value, {"tp": 0, "fp": 0, "fn": 0})["fp"] += 1
                failures.append({"case": case["id"], "type": "unexpected", "got": f"{p.category.value} {p.severity.value} line {p.line}: {p.title}"})
    precision, recall = _ratio(tp, tp + fp), _ratio(tp, tp + fn)
    return {"cases": len(cases), "true_positives": tp, "false_positives": fp, "false_negatives": fn,
            "precision": precision, "recall": recall, "f1": _f1(precision, recall),
            "recall_without_agents": _ratio(tp - agent_found, (tp + fn) - agent_total),
            "agent_only_expected": agent_total, "agent_only_found": agent_found,
            "severity_exact": _ratio(sev_exact, matched), "severity_3_tier": _ratio(sev_tier, matched),
            "deterministic_runs": _ratio(identical, len(cases)),
            "by_category": {c: {"precision": _ratio(v["tp"], v["tp"] + v["fp"]), "recall": _ratio(v["tp"], v["tp"] + v["fn"])} for c, v in by_cat.items()},
            "failures": failures}


def evaluate_retrieval(questions: list[dict], index: KnowledgeIndex, k: int = 3) -> dict:
    hit1 = hitk = 0
    misses = []
    for q in questions:
        ids = [d["id"] for d in index.search(q["query"], k)]
        hit1 += bool(ids) and ids[0] == q["expect"]
        ok = q["expect"] in ids
        hitk += ok
        if not ok:
            misses.append({"query": q["query"], "expected": q["expect"], "got": ids})
    return {"questions": len(questions), "hit@1": _ratio(hit1, len(questions)), f"hit@{k}": _ratio(hitk, len(questions)), "misses": misses}


async def evaluate(llm: ReviewLLM | None = None, index: KnowledgeIndex | None = None, data: dict | None = None) -> dict:
    data = data or load_cases()
    index = index or get_index()
    detection = await evaluate_detection(data["cases"], llm or NullLLM(), index)
    return {"detection": detection, "retrieval": evaluate_retrieval(data["retrieval"], index)}


def report(res: dict) -> str:
    d, r = res["detection"], res["retrieval"]
    pct = lambda x: "n/a" if x is None else f"{x:.0%}"
    lines = [f"Detection over {d['cases']} labelled cases",
             f"  precision {pct(d['precision'])}   recall {pct(d['recall'])}   F1 {pct(d['f1'])}",
             f"  recall on rule-detectable issues {pct(d['recall_without_agents'])}; {d['agent_only_expected']} further issues need the LLM agents (found {d['agent_only_found']})",
             f"  severity correct: {pct(d['severity_exact'])} exact, {pct(d['severity_3_tier'])} within the 3 tiers (high/medium/low)",
             f"  identical findings on repeat runs: {pct(d['deterministic_runs'])}",
             f"  TP {d['true_positives']}  FP {d['false_positives']}  FN {d['false_negatives']}"]
    for c, v in d["by_category"].items():
        lines.append(f"    {c:<16} precision {pct(v['precision'])}  recall {pct(v['recall'])}")
    lines.append(f"Rubric retrieval (FAISS) over {r['questions']} questions: hit@1 {pct(r['hit@1'])}, hit@3 {pct(r['hit@3'])}")
    for f in d["failures"]:
        lines.append(f"  ! {f}")
    for m in r["misses"]:
        lines.append(f"  ! retrieval miss: {m}")
    return "\n".join(lines)


if __name__ == "__main__":
    out = asyncio.run(evaluate())
    print(json.dumps(out, indent=2) if "--json" in sys.argv else report(out))

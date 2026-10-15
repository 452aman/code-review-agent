"""Quality gate: turns a review into a pass/fail a CI pipeline (or a GitHub status check) can act on."""
from app.services.config import GateConfig

_ORDER = ["critical", "high", "medium", "low", "info"]


def evaluate(findings: list[dict], score: int | None, cfg: GateConfig, has_baseline: bool) -> dict:
    """`findings` are dicts with severity, triage and is_new. Dismissed findings never count."""
    live = [f for f in findings if f.get("triage") != "dismissed"]
    scoped = [f for f in live if f.get("is_new", True)] if (cfg.only_new and has_baseline) else live
    counts = {s: sum(1 for f in scoped if f["severity"] == s) for s in _ORDER}
    reasons = []
    for s in cfg.fail_on:
        if counts.get(s.value, 0):
            reasons.append(f"{counts[s.value]} {s.value} finding(s)")
    if cfg.max_new_medium is not None and counts["medium"] > cfg.max_new_medium:
        reasons.append(f"{counts['medium']} medium findings (limit {cfg.max_new_medium})")
    if cfg.min_score is not None and score is not None and score < cfg.min_score:
        reasons.append(f"score {score} is below {cfg.min_score}")
    return {"passed": not reasons, "reasons": reasons, "counted": "new findings only" if (cfg.only_new and has_baseline) else "all findings",
            "counts": counts}


def status_description(result: dict) -> str:
    if result["passed"]:
        return f"Passed ({result['counted']})"[:140]
    return ("Failed: " + "; ".join(result["reasons"]))[:140]

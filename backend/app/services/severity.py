"""Deterministic severity rubric.

LLM agents assign a severity to each finding, and that is the least reliable thing they produce: the same issue can
come back as "high" on one run and "medium" on the next, and a model will happily call a style nit "critical".
This module checks every agent-assigned severity against an explicit rule set after generation. A finding whose
severity falls outside the allowed band for its kind of problem is clamped into the band and the change is recorded,
so the result is reproducible and every adjustment is explainable. Static-analysis findings come from fixed rules and
are never touched.
"""
import re
from dataclasses import dataclass

from app.core.types import SEVERITY_ORDER, Category, Finding, Severity, Source

S = Severity


@dataclass(frozen=True)
class Band:
    floor: Severity        # least severe an issue of this kind may be graded
    cap: Severity          # most severe an issue of this kind may be graded
    reason: str


def _rx(words: str) -> re.Pattern:
    return re.compile(words, re.I)


# First matching rule wins. "critical" is only reachable by the exploitable / data-loss rules at the top,
# which enforces the prompt's promise ("critical only for exploitable or data-loss bugs") in code instead of hoping.
RULES: list[tuple[Category, re.Pattern, Band]] = [
    (Category.security, _rx(r"hard-?coded|credential|secret|api[ _-]?key|private key|access key|committed token"),
     Band(S.high, S.critical, "leaked secrets are directly exploitable")),
    (Category.security, _rx(r"sql injection|command injection|code injection|\beval\b|\bexec\b|deseriali[sz]|remote code|\brce\b|path traversal|ssrf"),
     Band(S.high, S.critical, "injection and code-execution flaws are exploitable")),
    (Category.security, _rx(r"\bxss\b|cross-site|innerhtml|csrf|open redirect|cors|debug mode|\bmd5\b|\bsha-?1\b|weak hash|tls|certificate|verify\s*=\s*false"),
     Band(S.medium, S.high, "hardening issue that needs another condition to be exploitable")),
    (Category.security, _rx(r".*"), Band(S.low, S.high, "security finding without a known exploit pattern")),
    (Category.bug, _rx(r"data loss|corrupt|race|toctou|deadlock|resource leak|leak|crash|unhandled|syntax error"),
     Band(S.medium, S.high, "can lose data or fail at runtime")),
    (Category.bug, _rx(r".*"), Band(S.low, S.high, "ordinary correctness issue")),
    (Category.performance, _rx(r"n\+1|quadratic|cubic|unbounded|timeout|memory"),
     Band(S.medium, S.high, "degrades under load")),
    (Category.performance, _rx(r".*"), Band(S.low, S.medium, "optimisation opportunity")),
    (Category.maintainability, _rx(r".*"), Band(S.info, S.medium, "maintainability never blocks a release by itself")),
    (Category.style, _rx(r".*"), Band(S.info, S.low, "style is cosmetic")),
]

TIER_OF = {S.critical: "high", S.high: "high", S.medium: "medium", S.low: "low", S.info: "low"}   # 5 levels -> 3 reporting tiers


def tier(severity: Severity) -> str:
    return TIER_OF[severity]


def band_for(f: Finding) -> Band:
    text = f"{f.title} {f.explanation}"
    for category, pattern, band in RULES:
        if f.category == category and pattern.search(text):
            return band
    return Band(S.info, S.critical, "no rule")


def calibrate(f: Finding) -> tuple[Finding, dict | None]:
    """Return the finding with its severity forced into the allowed band, and a note if it was changed."""
    if f.source == Source.static:
        return f, None
    band = band_for(f)
    lo, hi = SEVERITY_ORDER[band.cap], SEVERITY_ORDER[band.floor]     # lower index = more severe
    idx = SEVERITY_ORDER[f.severity]
    target = f.severity
    if idx < lo:
        target = band.cap
    elif idx > hi:
        target = band.floor
    if target == f.severity:
        return f, None
    note = {"file": f.file, "line": f.line, "title": f.title, "from": f.severity.value, "to": target.value, "reason": band.reason}
    return f.model_copy(update={"severity": target}), note

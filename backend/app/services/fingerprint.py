"""Stable identity for a finding, so the same issue is recognised across reviews even when line numbers shift."""
import hashlib
import re


def normalise(line: str) -> str:
    return re.sub(r"\s+", " ", line.strip())


def fingerprint(file: str, category: str, line_text: str, rule_id: str | None) -> str:
    """file + category + the flagged line's text (+ rule id for static rules).

    Titles are deliberately excluded: an LLM words the same problem differently on every run, but the offending
    line of code stays the same.
    """
    raw = f"{file}|{category}|{rule_id or ''}|{normalise(line_text)}"
    return hashlib.sha1(raw.encode()).hexdigest()[:20]


def line_of(content: str, line: int) -> str:
    lines = content.splitlines()
    return lines[line - 1] if 1 <= line <= len(lines) else ""

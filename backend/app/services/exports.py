"""Machine-readable outputs: a unified diff of the suggested fixes, and SARIF 2.1.0 for GitHub code scanning."""
import difflib
import re

LEVEL = {"critical": "error", "high": "error", "medium": "warning", "low": "note", "info": "note"}


def build_patch(files: list[dict], findings: list) -> tuple[str, int]:
    """Apply each finding's single-line `fix` to its file and return (unified diff, number of fixes applied)."""
    by_file: dict[str, list] = {}
    for f in findings:
        if f.fix and f.triage != "dismissed":
            by_file.setdefault(f.file, []).append(f)
    chunks, applied = [], 0
    for src in files:
        fixes = by_file.get(src["path"])
        if not fixes:
            continue
        old = src["content"].splitlines()
        new = list(old)
        taken: set[int] = set()
        for f in sorted(fixes, key=lambda x: -x.line):          # bottom-up so earlier line numbers stay valid
            if f.line in taken or not 1 <= f.line <= len(new):
                continue
            indent = re.match(r"\s*", new[f.line - 1]).group(0)
            replacement = f.fix.splitlines() or [""]
            if replacement[0] and not replacement[0].startswith((" ", "\t")):
                replacement = [indent + replacement[0]] + replacement[1:]   # keep the original indentation
            new[f.line - 1:f.line] = replacement
            taken.add(f.line)
            applied += 1
        diff = list(difflib.unified_diff(old, new, f"a/{src['path']}", f"b/{src['path']}", lineterm=""))
        if diff:
            chunks.append("\n".join(diff))
    return ("\n".join(chunks) + "\n") if chunks else "", applied


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] or "finding"


def build_sarif(review, findings: list) -> dict:
    rules, results = {}, []
    for f in findings:
        if f.triage == "dismissed":
            continue
        rid = f.rule_id or _slug(f.title)
        rules.setdefault(rid, {"id": rid, "name": f.title, "shortDescription": {"text": f.title},
                               "properties": {"category": f.category}})
        msg = f.title + (f": {f.explanation}" if f.explanation else "") + (f" Suggestion: {f.suggestion}" if f.suggestion else "")
        results.append({
            "ruleId": rid, "level": LEVEL.get(f.severity, "warning"), "message": {"text": msg},
            "locations": [{"physicalLocation": {"artifactLocation": {"uri": f.file}, "region": {"startLine": f.line, **({"endLine": f.end_line} if f.end_line else {})}}}],
            "partialFingerprints": {"primaryLocationLineHash": f.fingerprint or ""},
            "properties": {"severity": f.severity, "source": f.source, "confidence": f.confidence, "isNew": f.is_new},
        })
    return {"$schema": "https://json.schemastore.org/sarif-2.1.0.json", "version": "2.1.0",
            "runs": [{"tool": {"driver": {"name": "code-review-agent", "informationUri": "https://github.com/452aman/code-review-agent", "rules": list(rules.values())}},
                      "results": results}]}

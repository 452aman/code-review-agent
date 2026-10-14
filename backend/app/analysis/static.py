"""Deterministic static analysis. Python gets AST checks; every language gets regex rules.

These run before any LLM so the review has a trustworthy baseline, the agents
get concrete leads, and the whole pipeline still works with no API key.
"""
import ast
import re
from dataclasses import dataclass

from app.core.types import Category, Finding, Severity, SourceFile


@dataclass(frozen=True)
class RegexRule:
    id: str
    pattern: re.Pattern
    severity: Severity
    category: Category
    title: str
    suggestion: str
    languages: tuple[str, ...] | None = None  # None = all


def R(id, pat, sev, cat, title, sug, langs=None):
    return RegexRule(id, re.compile(pat), sev, cat, title, sug, langs)


REGEX_RULES = [
    R("SEC001", r"""(?i)(password|passwd|secret|api[_-]?key|token|private[_-]?key)\s*[:=]\s*["'][^"'\s]{6,}["']""",
      Severity.critical, Category.security, "Hardcoded credential",
      "Load secrets from environment variables or a secrets manager and rotate this one: it is now in version control."),
    R("SEC002", r"AKIA[0-9A-Z]{16}", Severity.critical, Category.security, "AWS access key committed",
      "Revoke the key immediately and use IAM roles or environment-provided credentials."),
    R("SEC003", r"""(?i)(execute|query|raw)\s*\(\s*f?["'].*(%s|\{|\+\s*\w+|%\s*\w+)""", Severity.high, Category.security,
      "Possible SQL injection via string-built query",
      "Use parameterized queries (placeholders) so user input is never concatenated into SQL."),
    R("SEC004", r"\beval\s*\(", Severity.high, Category.security, "Use of eval()",
      "Avoid eval on dynamic input; use ast.literal_eval / JSON.parse or an explicit dispatch table."),
    R("SEC005", r"\bexec\s*\(", Severity.high, Category.security, "Use of exec()", "Avoid executing dynamic code.", ("python",)),
    R("SEC006", r"subprocess\.\w+\([^)]*shell\s*=\s*True", Severity.high, Category.security,
      "subprocess with shell=True", "Pass an argument list with shell=False to prevent command injection.", ("python",)),
    R("SEC007", r"pickle\.loads?\(|yaml\.load\((?![^)]*Loader\s*=\s*yaml\.SafeLoader)", Severity.high, Category.security,
      "Unsafe deserialization", "Use json or yaml.safe_load; never unpickle untrusted data.", ("python",)),
    R("SEC008", r"(?i)\b(md5|sha1)\s*\(|hashlib\.(md5|sha1)", Severity.medium, Category.security,
      "Weak hash function", "Use SHA-256+ for integrity, and bcrypt/argon2 for passwords."),
    R("SEC009", r"verify\s*=\s*False", Severity.high, Category.security, "TLS verification disabled",
      "Keep certificate verification on; fix the trust chain instead."),
    R("SEC010", r"\.innerHTML\s*=|dangerouslySetInnerHTML", Severity.medium, Category.security,
      "Raw HTML injection sink", "Sanitize with DOMPurify or render text via textContent.", ("javascript", "typescript")),
    R("SEC011", r"(?i)debug\s*=\s*True", Severity.medium, Category.security, "Debug mode enabled",
      "Never ship debug=True: it exposes stack traces and can allow code execution.", ("python",)),
    R("BUG001", r"==\s*None|!=\s*None", Severity.low, Category.bug, "Comparison to None with ==", "Use `is None` / `is not None`.", ("python",)),
    R("BUG002", r"except\s*:\s*$", Severity.medium, Category.bug, "Bare except swallows all errors",
      "Catch specific exceptions; a bare except also hides KeyboardInterrupt and SystemExit.", ("python",)),
    R("BUG003", r"==\s*(true|false)\b|[^=!]==[^=]", Severity.info, Category.style, "Loose equality",
      "Prefer === in JavaScript.", ("javascript",)),
    R("PERF001", r"requests\.(get|post|put|delete|patch)\((?![^)]*timeout)", Severity.medium, Category.performance,
      "HTTP call without timeout", "Always pass timeout=...; otherwise a stalled server hangs the worker forever.", ("python",)),
    R("MNT001", r"(?i)#\s*(TODO|FIXME|HACK|XXX)", Severity.info, Category.maintainability, "Unresolved TODO/FIXME",
      "Track it in an issue or resolve it."),
    R("MNT002", r"console\.log\(|\bprint\(", Severity.info, Category.style, "Debug output left in code",
      "Use a structured logger.", ("python", "javascript", "typescript")),
]

COMPLEXITY_LIMIT = 10
FUNC_LINES_LIMIT = 50


def _complexity(fn: ast.AST) -> int:
    """McCabe complexity: 1 + decision points."""
    n = 1
    for node in ast.walk(fn):
        if isinstance(node, (ast.If, ast.For, ast.While, ast.AsyncFor, ast.ExceptHandler, ast.IfExp, ast.With, ast.Assert)):
            n += 1
        elif isinstance(node, ast.BoolOp):
            n += len(node.values) - 1
        elif isinstance(node, ast.comprehension):
            n += 1 + len(node.ifs)
    return n


_LOOPS = (ast.For, ast.While, ast.AsyncFor)


def _loop_depth(node: ast.AST) -> int:
    inner = [_loop_depth(c) for c in ast.iter_child_nodes(node)]
    return max(inner, default=0) + (1 if isinstance(node, _LOOPS) else 0)


def _nested_loop_ids(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for loop in (n for n in ast.walk(tree) if isinstance(n, _LOOPS)):
        ids.update(id(n) for n in ast.walk(loop) if n is not loop and isinstance(n, _LOOPS))
    return ids


def analyze_python(f: SourceFile) -> list[Finding]:
    out: list[Finding] = []
    try:
        tree = ast.parse(f.content)
    except SyntaxError as e:
        return [Finding(file=f.path, line=e.lineno or 1, severity=Severity.high, category=Category.bug,
                        title="Syntax error", explanation=str(e.msg), suggestion="Fix the syntax before merging.", rule_id="BUG000")]
    nested = _nested_loop_ids(tree)  # inner loops are reported via their outermost loop only
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(node, "end_lineno", node.lineno)
            c = _complexity(node)
            if c > COMPLEXITY_LIMIT:
                out.append(Finding(file=f.path, line=node.lineno, end_line=end, severity=Severity.medium if c < 20 else Severity.high,
                                   category=Category.maintainability, title=f"`{node.name}` has cyclomatic complexity {c}",
                                   explanation=f"Complexity above {COMPLEXITY_LIMIT} makes functions hard to test and reason about.",
                                   suggestion="Extract branches into helper functions or use early returns / lookup tables.", rule_id="MNT003"))
            if end - node.lineno + 1 > FUNC_LINES_LIMIT:
                out.append(Finding(file=f.path, line=node.lineno, end_line=end, severity=Severity.low, category=Category.maintainability,
                                   title=f"`{node.name}` is {end - node.lineno + 1} lines long",
                                   suggestion="Split into smaller single-purpose functions.", rule_id="MNT004"))
            for d in node.args.defaults + node.args.kw_defaults:
                if isinstance(d, (ast.List, ast.Dict, ast.Set)):
                    out.append(Finding(file=f.path, line=d.lineno, severity=Severity.medium, category=Category.bug,
                                       title="Mutable default argument",
                                       explanation="The default object is created once and shared across every call.",
                                       suggestion="Default to None and create the container inside the function.", rule_id="BUG004"))
        if isinstance(node, ast.Try):
            for h in node.handlers:
                if len(h.body) == 1 and isinstance(h.body[0], ast.Pass):
                    out.append(Finding(file=f.path, line=h.lineno, severity=Severity.medium, category=Category.bug,
                                       title="Exception silently swallowed", suggestion="Log the error or handle it explicitly.", rule_id="BUG005"))
        if isinstance(node, (ast.For, ast.While, ast.AsyncFor)) and id(node) not in nested and _loop_depth(node) >= 3:
            out.append(Finding(file=f.path, line=node.lineno, severity=Severity.medium, category=Category.performance,
                               title="Triple-nested loop (O(n^3) pattern)",
                               suggestion="Index data with a dict/set or restructure to cut a nesting level.", rule_id="PERF002"))
    return out


def analyze(f: SourceFile) -> list[Finding]:
    findings: list[Finding] = analyze_python(f) if f.language == "python" else []
    for lineno, line in enumerate(f.content.splitlines(), start=1):
        for rule in REGEX_RULES:
            if rule.languages and f.language not in rule.languages:
                continue
            if rule.pattern.search(line):
                findings.append(Finding(file=f.path, line=lineno, severity=rule.severity, category=rule.category,
                                        title=rule.title, suggestion=rule.suggestion, rule_id=rule.id))
    if f.changed_lines is not None:
        scope = set(f.changed_lines)
        findings = [x for x in findings if x.line in scope]
    return findings

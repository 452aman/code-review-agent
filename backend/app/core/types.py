from enum import Enum

from pydantic import BaseModel, Field


class Severity(str, Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"
    info = "info"


SEVERITY_ORDER = {s: i for i, s in enumerate(Severity)}
SEVERITY_WEIGHT = {Severity.critical: 25, Severity.high: 12, Severity.medium: 5, Severity.low: 2, Severity.info: 0}


class Category(str, Enum):
    security = "security"
    bug = "bug"
    performance = "performance"
    maintainability = "maintainability"
    style = "style"


class Source(str, Enum):
    static = "static"
    security_agent = "security_agent"
    bug_agent = "bug_agent"
    performance_agent = "performance_agent"
    maintainability_agent = "maintainability_agent"


class Finding(BaseModel):
    file: str
    line: int = Field(ge=1)
    end_line: int | None = None
    severity: Severity
    category: Category
    title: str = Field(max_length=140)
    explanation: str = ""
    suggestion: str = ""
    fix: str | None = None            # replacement code for the flagged line(s), when the agent can give one
    rule_id: str | None = None
    source: Source = Source.static
    confidence: float = 0.8


class SourceFile(BaseModel):
    path: str
    content: str
    language: str = "text"
    changed_lines: list[int] | None = None   # set for PR reviews: only these lines are in scope


EXT_LANG = {".py": "python", ".js": "javascript", ".jsx": "javascript", ".ts": "typescript", ".tsx": "typescript",
            ".java": "java", ".go": "go", ".rb": "ruby", ".php": "php", ".rs": "rust", ".c": "c", ".cpp": "cpp", ".sql": "sql"}


def detect_language(path: str) -> str:
    for ext, lang in EXT_LANG.items():
        if path.lower().endswith(ext):
            return lang
    return "text"

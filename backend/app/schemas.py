from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FileIn(BaseModel):
    path: str = Field(min_length=1, max_length=300)
    content: str = Field(max_length=300_000)


class ReviewIn(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    files: list[FileIn] | None = None
    pr_url: str | None = None
    post_to_github: bool = False

    @model_validator(mode="after")
    def one_source(self):
        if bool(self.files) == bool(self.pr_url):
            raise ValueError("Provide either `files` or `pr_url`")
        return self


class FindingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    file: str
    line: int
    end_line: int | None
    severity: str
    category: str
    title: str
    explanation: str
    suggestion: str
    fix: str | None
    rule_id: str | None
    source: str
    confidence: float
    triage: str


class ReviewSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    title: str
    source: str
    source_ref: str | None
    status: str
    score: int | None
    grade: str | None
    stats: dict
    duration_s: float | None
    created_at: datetime


class ReviewOut(ReviewSummary):
    summary: str
    error: str | None
    files: list[dict]
    events: list[dict]
    findings: list[FindingOut]


class TriageIn(BaseModel):
    triage: str = Field(pattern="^(open|accepted|dismissed)$")

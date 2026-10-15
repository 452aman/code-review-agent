from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class FileIn(BaseModel):
    path: str = Field(min_length=1, max_length=300)
    content: str = Field(max_length=300_000)


class ReviewIn(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    files: list[FileIn] | None = None
    pr_url: str | None = None
    post_to_github: bool = False
    config: str | None = Field(default=None, max_length=8000, description=".codereview.yml contents (YAML)")

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
    fingerprint: str | None = None
    is_new: bool = True

    @field_validator("is_new", mode="before")
    @classmethod
    def _legacy_new(cls, v):   # rows written before baselines existed
        return True if v is None else v


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
    gate: dict = {}
    baseline_id: str | None = None
    created_at: datetime

    @field_validator("gate", mode="before")
    @classmethod
    def _legacy_gate(cls, v):  # NULL in rows written before the quality gate existed
        return v or {}


class ReviewOut(ReviewSummary):
    summary: str
    error: str | None
    config: dict = {}

    @field_validator("config", mode="before")
    @classmethod
    def _legacy_config(cls, v):
        return v or {}

    files: list[dict]
    events: list[dict]
    findings: list[FindingOut]


class TriageIn(BaseModel):
    triage: str = Field(pattern="^(open|accepted|dismissed)$")

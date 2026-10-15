import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from app.core.config import get_settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    title: Mapped[str] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(20))              # paste | github_pr | webhook
    source_ref: Mapped[str | None] = mapped_column(String(300), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)  # queued|running|done|failed
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    grade: Mapped[str | None] = mapped_column(String(2), nullable=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    files: Mapped[list] = mapped_column(JSON, default=list)       # [{path, language, content, changed_lines}]
    events: Mapped[list] = mapped_column(JSON, default=list)      # pipeline timeline, replayed to late SSE clients
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    baseline_id: Mapped[str | None] = mapped_column(String(32), nullable=True)   # the earlier review of the same PR/title this one is compared to
    config: Mapped[dict] = mapped_column(JSON, default=dict)                      # .codereview.yml in effect (validated)
    gate: Mapped[dict] = mapped_column(JSON, default=dict)                        # last quality-gate result
    meta: Mapped[dict] = mapped_column(JSON, default=dict)                        # PR coordinates: owner, repo, number, head_sha
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    findings: Mapped[list["FindingRow"]] = relationship(back_populates="review", cascade="all, delete-orphan",
                                                         order_by="FindingRow.id")


class FindingRow(Base):
    __tablename__ = "findings"

    id: Mapped[int] = mapped_column(primary_key=True)
    review_id: Mapped[str] = mapped_column(ForeignKey("reviews.id"), index=True)
    file: Mapped[str] = mapped_column(String(300))
    line: Mapped[int] = mapped_column(Integer)
    end_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    severity: Mapped[str] = mapped_column(String(10), index=True)
    category: Mapped[str] = mapped_column(String(20), index=True)
    title: Mapped[str] = mapped_column(String(160))
    explanation: Mapped[str] = mapped_column(Text, default="")
    suggestion: Mapped[str] = mapped_column(Text, default="")
    fix: Mapped[str | None] = mapped_column(Text, nullable=True)
    rule_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source: Mapped[str] = mapped_column(String(30))
    confidence: Mapped[float] = mapped_column(Float, default=0.8)
    triage: Mapped[str] = mapped_column(String(12), default="open")  # open | accepted | dismissed
    fingerprint: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    is_new: Mapped[bool] = mapped_column(default=True)               # False when the same issue was already in the baseline review

    review: Mapped[Review] = relationship(back_populates="findings")


_url = get_settings().database_url
engine = create_engine(_url, connect_args={"check_same_thread": False} if _url.startswith("sqlite") else {})
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_db():
    with SessionLocal() as db:
        yield db


def ensure_schema() -> None:
    """Add columns introduced after a database was created (create_all only creates missing tables). Idempotent."""
    from sqlalchemy import inspect, text

    additions = {
        "reviews": {"baseline_id": "VARCHAR(32)", "config": "JSON", "gate": "JSON", "meta": "JSON"},
        "findings": {"fingerprint": "VARCHAR(40)", "is_new": "BOOLEAN DEFAULT 1"},
    }
    insp = inspect(engine)
    with engine.begin() as conn:
        for table, cols in additions.items():
            if not insp.has_table(table):
                continue
            have = {c["name"] for c in insp.get_columns(table)}
            for col, ddl in cols.items():
                if col not in have:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))

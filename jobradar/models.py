from datetime import date, datetime, timezone

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .crypto import decrypt_json, encrypt_json
from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


JOB_STATUSES = ["new", "saved", "queued", "applied", "interviewing", "offer", "rejected", "dismissed"]
PIPELINE_STATUSES = ["saved", "queued", "applied", "interviewing", "offer", "rejected"]
EXPERIENCE_LEVELS = ["intern", "entry", "mid", "senior", "lead"]

DEFAULT_COVER_LETTER = """Dear {company} hiring team,

I'm excited to apply for the {title} position. My background in {top_skills} lines up closely with what you're looking for, and I'd love to bring that experience to {company}.

{highlight}

I'd welcome the chance to talk about how I can contribute to your team. Thank you for your time and consideration.

Best regards,
{full_name}
{email} | {phone}
"""


class Profile(Base):
    __tablename__ = "profile"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    full_name: Mapped[str] = mapped_column(String(200), default="")
    email: Mapped[str] = mapped_column(String(200), default="")
    phone: Mapped[str] = mapped_column(String(50), default="")
    city: Mapped[str] = mapped_column(String(200), default="")
    linkedin_url: Mapped[str] = mapped_column(String(300), default="")
    github_url: Mapped[str] = mapped_column(String(300), default="")
    portfolio_url: Mapped[str] = mapped_column(String(300), default="")
    school: Mapped[str] = mapped_column(String(200), default="")
    degree: Mapped[str] = mapped_column(String(200), default="")
    graduation_date: Mapped[str] = mapped_column(String(50), default="")
    gpa: Mapped[str] = mapped_column(String(20), default="")
    work_authorized: Mapped[bool] = mapped_column(Boolean, default=True)
    needs_sponsorship: Mapped[bool] = mapped_column(Boolean, default=False)

    resume_filename: Mapped[str] = mapped_column(String(300), default="")
    resume_text: Mapped[str] = mapped_column(Text, default="")
    resume_skills: Mapped[list] = mapped_column(JSON, default=list)
    years_experience: Mapped[float] = mapped_column(Float, default=0.0)

    target_titles: Mapped[list] = mapped_column(JSON, default=list)
    locations: Mapped[list] = mapped_column(JSON, default=list)
    remote_ok: Mapped[bool] = mapped_column(Boolean, default=True)
    remote_only: Mapped[bool] = mapped_column(Boolean, default=False)
    min_salary: Mapped[int] = mapped_column(Integer, default=0)
    experience_level: Mapped[str] = mapped_column(String(20), default="entry")
    must_have_keywords: Mapped[list] = mapped_column(JSON, default=list)
    exclude_keywords: Mapped[list] = mapped_column(JSON, default=list)
    excluded_companies: Mapped[list] = mapped_column(JSON, default=list)
    max_age_days: Mapped[int] = mapped_column(Integer, default=30)

    daily_goal: Mapped[int] = mapped_column(Integer, default=10)
    alert_threshold: Mapped[int] = mapped_column(Integer, default=60)
    sync_interval_minutes: Mapped[int] = mapped_column(Integer, default=60)
    digest_hour: Mapped[int] = mapped_column(Integer, default=8)
    last_digest_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    follow_up_days: Mapped[int] = mapped_column(Integer, default=7)
    cover_letter_template: Mapped[str] = mapped_column(Text, default=DEFAULT_COVER_LETTER)
    resume_highlight: Mapped[str] = mapped_column(Text, default="")


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(50))
    name: Mapped[str] = mapped_column(String(200))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    config_encrypted: Mapped[str] = mapped_column(Text, default="")
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_status: Mapped[str] = mapped_column(String(20), default="never")
    last_error: Mapped[str] = mapped_column(Text, default="")
    last_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    jobs: Mapped[list["Job"]] = relationship(back_populates="source")

    @property
    def config(self) -> dict:
        return decrypt_json(self.config_encrypted)

    @config.setter
    def config(self, value: dict) -> None:
        self.config_encrypted = encrypt_json(value)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("source_kind", "external_id", name="uq_job_source_external"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id", ondelete="SET NULL"), nullable=True)
    source_kind: Mapped[str] = mapped_column(String(50))
    external_id: Mapped[str] = mapped_column(String(300))
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    board: Mapped[str] = mapped_column(String(100), default="")

    title: Mapped[str] = mapped_column(String(300))
    company: Mapped[str] = mapped_column(String(300), default="")
    location: Mapped[str] = mapped_column(String(300), default="")
    remote: Mapped[bool] = mapped_column(Boolean, default=False)
    url: Mapped[str] = mapped_column(String(1000), default="")
    apply_url: Mapped[str] = mapped_column(String(1000), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    salary_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    salary_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    employment_type: Mapped[str] = mapped_column(String(100), default="")
    posted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

    score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    score_details: Mapped[dict] = mapped_column(JSON, default=dict)
    filtered_out: Mapped[bool] = mapped_column(Boolean, default=False)
    alerted: Mapped[bool] = mapped_column(Boolean, default=False)

    status: Mapped[str] = mapped_column(String(20), default="new", index=True)
    queued_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    follow_up_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    source: Mapped[Source | None] = relationship(back_populates="jobs")

    @property
    def link(self) -> str:
        return self.apply_url or self.url

    @property
    def salary_label(self) -> str:
        if self.salary_min and self.salary_max and self.salary_min != self.salary_max:
            return f"${self.salary_min:,} - ${self.salary_max:,}"
        value = self.salary_min or self.salary_max
        return f"${value:,}" if value else ""


class Answer(Base):
    __tablename__ = "answers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question: Mapped[str] = mapped_column(String(500))
    answer: Mapped[str] = mapped_column(Text, default="")
    keywords: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(200))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    config_encrypted: Mapped[str] = mapped_column(Text, default="")
    last_error: Mapped[str] = mapped_column(Text, default="")

    @property
    def config(self) -> dict:
        return decrypt_json(self.config_encrypted)

    @config.setter
    def config(self, value: dict) -> None:
        self.config_encrypted = encrypt_json(value)


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    fetched: Mapped[int] = mapped_column(Integer, default=0)
    new_jobs: Mapped[int] = mapped_column(Integer, default=0)
    alerted: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[list] = mapped_column(JSON, default=list)

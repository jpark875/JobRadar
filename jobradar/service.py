import logging
import threading
from datetime import date, datetime, timedelta

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import notify
from .matching import JobView, Matcher, Preferences
from .models import Answer, Channel, Job, Profile, Source, SyncRun, utcnow
from .sources import JobPosting, SourceContext, SourceError, get_source
from .text import clean, fingerprint

log = logging.getLogger("jobradar")
_sync_lock = threading.Lock()

OPEN_STATUSES = ("new", "saved")

DEFAULT_ANSWERS = [
    ("Are you legally authorized to work in this country?", "Yes", "authorized, eligible, legally"),
    ("Will you now or in the future require visa sponsorship?", "No", "sponsorship, visa, h-1b"),
    ("What are your salary expectations?", "I'm targeting a range in line with the market for this role and location, and I'm flexible depending on the full compensation package.", "salary, compensation, pay"),
    ("Why do you want to work at {company}?", "I'm drawn to {company} because of its work in this space, and the {title} role is a strong fit for my experience with {top_skills}.", "why, interest, motivation"),
    ("When can you start?", "I can start two weeks after receiving an offer.", "start date, availability, notice"),
    ("How did you hear about this position?", "Online job board", "hear, referral, source"),
]


def get_profile(session: Session) -> Profile:
    profile = session.get(Profile, 1)
    if profile is None:
        profile = Profile(id=1)
        session.add(profile)
        for question, answer, keywords in DEFAULT_ANSWERS:
            session.add(Answer(question=question, answer=answer, keywords=keywords))
        session.flush()
    return profile


def source_context(profile: Profile) -> SourceContext:
    return SourceContext(
        keywords=list(profile.target_titles or []),
        locations=list(profile.locations or []),
        remote_ok=profile.remote_ok,
        max_age_days=profile.max_age_days or 30,
    )


def store_postings(session: Session, postings: list[JobPosting], kind: str, source: Source | None = None) -> list[Job]:
    existing_ids = {
        row[0] for row in session.execute(select(Job.external_id).where(Job.source_kind == kind))
    }
    existing_prints = {row[0] for row in session.execute(select(Job.fingerprint))}
    new_jobs = []
    for posting in postings:
        title = clean(posting.title)
        if not title or posting.external_id in existing_ids:
            continue
        print_ = fingerprint(title, posting.company, posting.location)
        if print_ in existing_prints:
            continue
        job = Job(
            source=source,
            source_kind=kind,
            external_id=posting.external_id,
            fingerprint=print_,
            board=posting.board,
            title=title[:300],
            company=clean(posting.company)[:300],
            location=clean(posting.location)[:300],
            remote=posting.remote,
            url=posting.url[:1000],
            apply_url=posting.apply_url[:1000],
            description=posting.description or "",
            salary_min=posting.salary_min,
            salary_max=posting.salary_max,
            employment_type=(posting.employment_type or "")[:100],
            posted_at=posting.posted_at,
            first_seen_at=utcnow(),
        )
        session.add(job)
        new_jobs.append(job)
        existing_ids.add(posting.external_id)
        existing_prints.add(print_)
    session.flush()
    return new_jobs


def build_matcher(session: Session, profile: Profile) -> Matcher:
    corpus = (
        f"{title}\n{description[:4000]}"
        for title, description in session.execute(select(Job.title, Job.description))
    )
    return Matcher(Preferences.from_profile(profile), corpus)


def rescore(session: Session, jobs: list[Job] | None = None) -> int:
    profile = get_profile(session)
    matcher = build_matcher(session, profile)
    if jobs is None:
        jobs = list(session.scalars(select(Job)))
    for job in jobs:
        result = matcher.score(JobView.from_job(job))
        job.score = result.score
        job.score_details = result.details
        job.filtered_out = result.filtered_reason is not None
    session.flush()
    return len(jobs)


def score_one(session: Session, job: Job) -> None:
    rescore(session, [job])


def sync_source(session: Session, source: Source, client: httpx.Client) -> list[Job]:
    profile = get_profile(session)
    adapter = get_source(source.kind)
    try:
        postings = adapter.fetch(source.config, source_context(profile), client)
    except SourceError as exc:
        source.last_status = "error"
        source.last_error = str(exc)
        source.last_synced_at = utcnow()
        raise
    except Exception as exc:
        source.last_status = "error"
        source.last_error = f"Unexpected error: {exc}"
        source.last_synced_at = utcnow()
        log.exception("Source %s failed", source.name)
        raise SourceError(source.last_error) from exc
    new_jobs = store_postings(session, postings, source.kind, source)
    source.last_status = "ok"
    source.last_error = ""
    source.last_count = len(postings)
    source.last_synced_at = utcnow()
    return new_jobs


def _alert_items(jobs: list[Job]) -> list[notify.AlertItem]:
    return [
        notify.AlertItem(title=j.title, company=j.company, location=j.location, score=j.score, url=j.link, board=j.board)
        for j in jobs
    ]


def broadcast(session: Session, heading: str, jobs: list[Job]) -> list[str]:
    errors = []
    for channel in session.scalars(select(Channel).where(Channel.enabled.is_(True))):
        try:
            notify.send(channel.kind, channel.config, heading, _alert_items(jobs))
            channel.last_error = ""
        except notify.NotifyError as exc:
            channel.last_error = str(exc)
            errors.append(f"{channel.name}: {exc}")
    return errors


def alert_new_matches(session: Session, profile: Profile) -> tuple[int, list[str]]:
    candidates = list(session.scalars(
        select(Job)
        .where(Job.alerted.is_(False), Job.filtered_out.is_(False), Job.status == "new", Job.score >= profile.alert_threshold)
        .order_by(Job.score.desc())
    ))
    for job in session.scalars(select(Job).where(Job.alerted.is_(False))):
        job.alerted = True
    if not candidates:
        return 0, []
    noun = "match" if len(candidates) == 1 else "matches"
    heading = f"JobRadar: {len(candidates)} new {noun} (top: {candidates[0].title} at {candidates[0].company or 'unknown'})"
    return len(candidates), broadcast(session, heading[:180], candidates[:25])


def run_sync(
    session: Session, source_ids: list[int] | None = None, send_alerts: bool = True, wait: bool = False
) -> SyncRun:
    if not (_sync_lock.acquire(timeout=600) if wait else _sync_lock.acquire(blocking=False)):
        raise RuntimeError("A sync is already running")
    try:
        run = SyncRun(started_at=utcnow())
        session.add(run)
        profile = get_profile(session)
        query = select(Source).where(Source.enabled.is_(True))
        if source_ids:
            query = select(Source).where(Source.id.in_(source_ids))
        errors, fetched, new_total = [], 0, 0
        with httpx.Client(follow_redirects=True) as client:
            for source in session.scalars(query).all():
                try:
                    new_jobs = sync_source(session, source, client)
                    fetched += source.last_count
                    new_total += len(new_jobs)
                except SourceError as exc:
                    errors.append(f"{source.name}: {exc}")
                session.commit()
        rescore(session)
        alerted = 0
        if send_alerts:
            alerted, alert_errors = alert_new_matches(session, profile)
            errors.extend(alert_errors)
        run.fetched, run.new_jobs, run.alerted, run.errors = fetched, new_total, alerted, errors
        run.finished_at = utcnow()
        session.commit()
        return run
    finally:
        _sync_lock.release()


def sync_running() -> bool:
    return _sync_lock.locked()


def recommended(session: Session, limit: int = 20, min_score: float = 0) -> list[Job]:
    return list(session.scalars(
        select(Job)
        .where(Job.status.in_(OPEN_STATUSES), Job.filtered_out.is_(False), Job.score >= min_score)
        .order_by(Job.score.desc(), Job.first_seen_at.desc())
        .limit(limit)
    ))


def maybe_send_digest(session: Session, now: datetime | None = None, force: bool = False) -> bool:
    now = now or datetime.now()
    profile = get_profile(session)
    if not force and (profile.digest_hour < 0 or now.hour < profile.digest_hour or profile.last_digest_on == now.date()):
        return False
    jobs = recommended(session, limit=15, min_score=max(0, profile.alert_threshold - 15))
    profile.last_digest_on = now.date()
    session.flush()
    if not jobs:
        return False
    queued = len(todays_queue(session, profile, now.date()))
    broadcast(session, f"JobRadar daily digest: top {len(jobs)} open matches, {queued} in today's queue", jobs)
    return True


def todays_queue(session: Session, profile: Profile, today: date | None = None) -> list[Job]:
    today = today or date.today()
    return list(session.scalars(
        select(Job).where(Job.status == "queued", Job.queued_on <= today).order_by(Job.score.desc())
    ))


def fill_queue(session: Session, profile: Profile, today: date | None = None) -> list[Job]:
    today = today or date.today()
    queue = todays_queue(session, profile, today)
    applied_today = applied_count(session, today)
    room = max(0, profile.daily_goal - len(queue) - applied_today)
    if room:
        for job in recommended(session, limit=room, min_score=0):
            job.status = "queued"
            job.queued_on = today
            queue.append(job)
    session.flush()
    return queue


def queue_job(job: Job, today: date | None = None) -> None:
    job.status = "queued"
    job.queued_on = today or date.today()


def mark_applied(job: Job, profile: Profile, when: datetime | None = None) -> None:
    when = when or datetime.now()
    job.status = "applied"
    job.applied_at = when
    job.follow_up_on = when.date() + timedelta(days=profile.follow_up_days or 7)


def set_status(job: Job, status: str, profile: Profile) -> None:
    if status == "applied":
        mark_applied(job, profile)
    elif status == "queued":
        queue_job(job)
    else:
        job.status = status
        if status in ("rejected", "offer", "dismissed"):
            job.follow_up_on = None


def applied_count(session: Session, day: date) -> int:
    start = datetime.combine(day, datetime.min.time())
    return session.scalar(
        select(func.count(Job.id)).where(Job.applied_at >= start, Job.applied_at < start + timedelta(days=1))
    ) or 0


def follow_ups_due(session: Session, today: date | None = None) -> list[Job]:
    today = today or date.today()
    return list(session.scalars(
        select(Job)
        .where(Job.follow_up_on.is_not(None), Job.follow_up_on <= today, Job.status.in_(("applied", "interviewing")))
        .order_by(Job.follow_up_on)
    ))


def streak(session: Session, today: date | None = None) -> int:
    today = today or date.today()
    days = {
        d.date() if isinstance(d, datetime) else d
        for (d,) in session.execute(select(Job.applied_at).where(Job.applied_at.is_not(None)))
    }
    count, cursor = 0, today
    if cursor not in days:
        cursor -= timedelta(days=1)
    while cursor in days:
        count += 1
        cursor -= timedelta(days=1)
    return count


def dashboard_stats(session: Session, profile: Profile) -> dict:
    today = date.today()
    since = utcnow() - timedelta(days=1)
    by_status = dict(session.execute(select(Job.status, func.count(Job.id)).group_by(Job.status)).all())
    week_start = datetime.combine(today - timedelta(days=6), datetime.min.time())
    return {
        "total": sum(by_status.values()),
        "by_status": by_status,
        "new_24h": session.scalar(
            select(func.count(Job.id)).where(Job.first_seen_at >= since, Job.filtered_out.is_(False))
        ) or 0,
        "applied_today": applied_count(session, today),
        "applied_week": session.scalar(select(func.count(Job.id)).where(Job.applied_at >= week_start)) or 0,
        "daily_goal": profile.daily_goal,
        "streak": streak(session, today),
        "sources": session.scalar(select(func.count(Source.id)).where(Source.enabled.is_(True))) or 0,
        "last_run": session.scalars(select(SyncRun).order_by(SyncRun.id.desc()).limit(1)).first(),
    }

import asyncio
import logging
import secrets
import threading
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode, urlparse

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from .. import apply, grader, notify, resume, service
from ..config import get_settings
from ..db import get_session, init_db, session_scope
from ..models import EXPERIENCE_LEVELS, JOB_STATUSES, PIPELINE_STATUSES, Answer, Channel, Job, Source, SyncRun, utcnow
from ..skills import normalize_skill
from ..sources import REGISTRY, JobPosting, get_source
from ..sources.base import split_list
from ..sources.email_alerts import BOARDS, parse_alert_html, parse_message
from ..text import html_to_text

log = logging.getLogger("jobradar")
HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(HERE / "templates"))
PAGE_SIZE = 50
SETTINGS_PATHS = ("/profile", "/sources", "/alerts", "/answers", "/import")


def _timeago(value: datetime | None) -> str:
    if not value:
        return ""
    delta = utcnow() - value
    if delta < timedelta(minutes=1):
        return "just now"
    if delta < timedelta(hours=1):
        return f"{int(delta.total_seconds() // 60)}m ago"
    if delta < timedelta(days=1):
        return f"{int(delta.total_seconds() // 3600)}h ago"
    if delta < timedelta(days=60):
        return f"{delta.days}d ago"
    return value.strftime("%b %d, %Y")


def _score_class(score: float) -> str:
    return "hi" if score >= 70 else "mid" if score >= 45 else "lo"


templates.env.filters["timeago"] = _timeago
templates.env.filters["score_class"] = _score_class
templates.env.globals["statuses"] = JOB_STATUSES
templates.env.tests["fresh"] = lambda job: bool(job.first_seen_at) and utcnow() - job.first_seen_at < timedelta(days=2)


def _background_sync(source_ids: list[int] | None = None) -> None:
    try:
        with session_scope() as session:
            service.run_sync(session, source_ids, wait=bool(source_ids))
    except RuntimeError:
        pass
    except Exception:
        log.exception("Background sync failed")


async def _scheduler() -> None:
    while True:
        await asyncio.sleep(30)
        try:
            with session_scope() as session:
                profile = service.get_profile(session)
                interval = timedelta(minutes=max(10, profile.sync_interval_minutes or 60))
                last = session.scalars(select(SyncRun).order_by(SyncRun.id.desc()).limit(1)).first()
                has_sources = session.scalar(select(func.count(Source.id)).where(Source.enabled.is_(True)))
                due = has_sources and not service.sync_running() and (not last or utcnow() - last.started_at >= interval)
            if due:
                await run_in_threadpool(_background_sync)
            with session_scope() as session:
                await run_in_threadpool(service.maybe_send_digest, session)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Scheduler tick failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    with session_scope() as session:
        service.get_profile(session)
    task = None if get_settings().disable_scheduler else asyncio.create_task(_scheduler())
    yield
    if task:
        task.cancel()


app = FastAPI(title="JobRadar", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")


@app.middleware("http")
async def same_origin_guard(request: Request, call_next):
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        origin = request.headers.get("origin") or request.headers.get("referer") or ""
        if origin and not origin.startswith(("chrome-extension://", "moz-extension://")):
            if urlparse(origin).netloc != request.url.netloc:
                return PlainTextResponse("Cross-origin request blocked", status_code=403)
        if request.url.path.startswith("/api/") and request.headers.get("x-jobradar") != "1":
            return PlainTextResponse("Missing X-JobRadar header", status_code=403)
    return await call_next(request)


def redirect(path: str, msg: str = "", err: str = "") -> RedirectResponse:
    params = {k: v for k, v in (("msg", msg), ("err", err)) if v}
    joiner = "&" if "?" in path else "?"
    return RedirectResponse(path + (joiner + urlencode(params) if params else ""), status_code=303)


def render(request: Request, name: str, session: Session, **context) -> HTMLResponse:
    context.setdefault("profile", service.get_profile(session))
    context["msg"] = request.query_params.get("msg", "")
    context["err"] = request.query_params.get("err", "")
    context["syncing"] = service.sync_running()
    context["nav"] = request.url.path
    context["in_settings"] = request.url.path.startswith(SETTINGS_PATHS)
    context["queue_count"] = session.scalar(
        select(func.count(Job.id)).where(Job.status == "queued", Job.queued_on <= date.today())
    )
    return templates.TemplateResponse(request, name, context)


def _next(value, default: str) -> str:
    value = str(value or "")
    return value if value.startswith("/") and not value.startswith("//") else default


def _job_or_404(session: Session, job_id: int) -> Job:
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


def _back(request: Request, fallback: str) -> str:
    ref = request.headers.get("referer", "")
    parsed = urlparse(ref)
    if ref and parsed.netloc == request.url.netloc:
        query = "&".join(p for p in parsed.query.split("&") if p and not p.startswith(("msg=", "err=")))
        return parsed.path + ("?" + query if query else "")
    return fallback


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, session: Session = Depends(get_session)):
    profile = service.get_profile(session)
    if not profile.onboarded and not profile.resume_text and not session.scalar(select(func.count(Source.id))):
        return RedirectResponse("/welcome", status_code=303)
    return render(
        request, "dashboard.html", session,
        stats=service.dashboard_stats(session, profile),
        top=service.recommended(session, limit=10, min_score=30),
        queue=service.todays_queue(session, profile),
        follow_ups=service.follow_ups_due(session)[:8],
        setup_needed=not profile.resume_text or not session.scalar(select(func.count(Source.id))),
        report=grader.grade_resume(profile.resume_text, profile.years_experience) if profile.resume_text else None,
    )


@app.post("/sync")
def sync_all(request: Request):
    if service.sync_running():
        return redirect(_back(request, "/"), err="Already checking for jobs. Give it a minute.")
    threading.Thread(target=_background_sync, daemon=True).start()
    return redirect(_back(request, "/"), msg="Checking every connected site for new jobs. This page refreshes on its own.")


@app.get("/jobs", response_class=HTMLResponse)
def jobs_list(
    request: Request,
    q: str = "",
    status: str = "open",
    board: str = "",
    min_score: int = 0,
    days: int = 0,
    sort: str = "score",
    show_filtered: int = 0,
    page: int = 1,
    session: Session = Depends(get_session),
):
    query = select(Job)
    if status == "open":
        query = query.where(Job.status.in_(service.OPEN_STATUSES))
    elif status and status != "all":
        query = query.where(Job.status == status)
    if not show_filtered:
        query = query.where(Job.filtered_out.is_(False))
    if board:
        query = query.where(Job.board == board)
    if min_score:
        query = query.where(Job.score >= min_score)
    if days:
        cutoff = utcnow() - timedelta(days=days)
        query = query.where(or_(Job.posted_at >= cutoff, (Job.posted_at.is_(None)) & (Job.first_seen_at >= cutoff)))
    if q:
        like = f"%{q}%"
        query = query.where(or_(Job.title.ilike(like), Job.company.ilike(like), Job.location.ilike(like), Job.description.ilike(like)))
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    order = {
        "score": (Job.score.desc(), Job.first_seen_at.desc()),
        "newest": (func.coalesce(Job.posted_at, Job.first_seen_at).desc(),),
        "company": (Job.company.asc(), Job.score.desc()),
    }.get(sort, (Job.score.desc(),))
    page = max(1, page)
    jobs = list(session.scalars(query.order_by(*order).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)))
    boards = [b for (b,) in session.execute(select(Job.board).distinct().order_by(Job.board)) if b]
    params = dict(request.query_params)
    params.pop("page", None)
    params.pop("msg", None)
    params.pop("err", None)
    return render(
        request, "jobs.html", session,
        jobs=jobs, total=total, page=page, pages=max(1, -(-total // PAGE_SIZE)), boards=boards,
        filters={"q": q, "status": status, "board": board, "min_score": min_score, "days": days, "sort": sort, "show_filtered": show_filtered},
        base_query=urlencode(params),
    )


@app.post("/jobs/bulk")
async def jobs_bulk(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    ids = [int(i) for i in form.getlist("ids") if str(i).isdigit()]
    action = form.get("action", "")
    profile = service.get_profile(session)
    if action not in ("queued", "saved", "dismissed", "applied", "new"):
        return redirect(_back(request, "/jobs"), err="Unknown action")
    for job in session.scalars(select(Job).where(Job.id.in_(ids))):
        service.set_status(job, action, profile)
    return redirect(_back(request, "/jobs"), msg=f"Updated {len(ids)} job{'s' if len(ids) != 1 else ''}")


@app.get("/jobs/new", response_class=HTMLResponse)
def job_new_form(request: Request, session: Session = Depends(get_session)):
    return render(request, "job_new.html", session)


@app.post("/jobs/new")
def job_new(
    title: str = Form(...),
    company: str = Form(""),
    location: str = Form(""),
    url: str = Form(""),
    description: str = Form(""),
    remote: bool = Form(False),
    session: Session = Depends(get_session),
):
    posting = JobPosting(
        external_id=url or f"manual:{utcnow().timestamp()}", title=title, company=company,
        location=location, url=url, description=html_to_text(description), remote=remote, board="Manual",
    )
    jobs = service.store_postings(session, [posting], "manual")
    if not jobs:
        return redirect("/jobs/new", err="That job is already in your list")
    service.score_one(session, jobs[0])
    jobs[0].status = "saved"
    return redirect(f"/jobs/{jobs[0].id}", msg="Job added")


@app.get("/jobs/{job_id:int}", response_class=HTMLResponse)
def job_detail(job_id: int, request: Request, session: Session = Depends(get_session)):
    job = _job_or_404(session, job_id)
    profile = service.get_profile(session)
    fit = grader.job_fit(profile.resume_text, job.title, job.description) if profile.resume_text and job.description else None
    answers = list(session.scalars(select(Answer).order_by(Answer.id)))
    return render(
        request, "job_detail.html", session,
        job=job,
        cover_letter=apply.render(profile.cover_letter_template, profile, job),
        fit=fit,
        answers=[(a, apply.render(a.answer, profile, job), apply.render(a.question, profile, job)) for a in answers],
    )


@app.post("/jobs/{job_id:int}/status")
def job_status(job_id: int, request: Request, status: str = Form(""), session: Session = Depends(get_session)):
    if not status:
        return redirect(_back(request, f"/jobs/{job_id}"))
    if status not in JOB_STATUSES:
        raise HTTPException(400, "Bad status")
    job = _job_or_404(session, job_id)
    service.set_status(job, status, service.get_profile(session))
    labels = {"queued": "Added to today's queue", "dismissed": "Marked not interested", "applied": "Marked as applied. A follow-up reminder is set."}
    return redirect(_back(request, f"/jobs/{job_id}"), msg=labels.get(status, f"Moved to {status}"))


@app.post("/jobs/{job_id:int}/notes")
def job_notes(
    job_id: int,
    notes: str = Form(""),
    follow_up_on: str = Form(""),
    session: Session = Depends(get_session),
):
    job = _job_or_404(session, job_id)
    job.notes = notes
    job.follow_up_on = date.fromisoformat(follow_up_on) if follow_up_on else None
    return redirect(f"/jobs/{job_id}", msg="Saved")


@app.get("/jobs/{job_id:int}/cover-letter.txt")
def cover_letter_download(job_id: int, session: Session = Depends(get_session)):
    job = _job_or_404(session, job_id)
    profile = service.get_profile(session)
    text = apply.render(profile.cover_letter_template, profile, job)
    slug = "".join(c if c.isalnum() else "-" for c in f"{job.company}-{job.title}".lower()).strip("-")[:60]
    return PlainTextResponse(text, headers={"Content-Disposition": f'attachment; filename="cover-letter-{slug}.txt"'})


@app.get("/queue", response_class=HTMLResponse)
def queue_page(request: Request, session: Session = Depends(get_session)):
    profile = service.get_profile(session)
    today = date.today()
    applied_today = list(session.scalars(
        select(Job).where(Job.applied_at >= datetime.combine(today, datetime.min.time())).order_by(Job.applied_at.desc())
    ))
    return render(
        request, "queue.html", session,
        queue=service.todays_queue(session, profile, today),
        applied_today=applied_today,
        goal=profile.daily_goal,
    )


@app.post("/queue/fill")
def queue_fill(session: Session = Depends(get_session)):
    profile = service.get_profile(session)
    before = len(service.todays_queue(session, profile))
    after = len(service.fill_queue(session, profile))
    added = after - before
    if not added:
        return redirect("/queue", msg="Queue is already full for today's goal, or there are no open matches left")
    return redirect("/queue", msg=f"Added {added} top matches to today's queue")


@app.post("/queue/clear")
def queue_clear(session: Session = Depends(get_session)):
    for job in session.scalars(select(Job).where(Job.status == "queued")):
        job.status = "saved"
        job.queued_on = None
    return redirect("/queue", msg="Queue cleared; jobs moved back to saved")


@app.get("/tracker", response_class=HTMLResponse)
def tracker(request: Request, session: Session = Depends(get_session)):
    columns = {
        status: list(session.scalars(
            select(Job).where(Job.status == status).order_by(Job.updated_at.desc()).limit(100)
        ))
        for status in PIPELINE_STATUSES
    }
    return render(request, "tracker.html", session, columns=columns, follow_ups=service.follow_ups_due(session))


@app.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request, session: Session = Depends(get_session)):
    return render(request, "profile.html", session, levels=EXPERIENCE_LEVELS, placeholders=apply.placeholders())


def _lines(value: str) -> list[str]:
    return split_list(value)


@app.post("/profile")
async def profile_save(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    profile = service.get_profile(session)
    text_fields = [
        "full_name", "email", "phone", "city", "linkedin_url", "github_url", "portfolio_url", "school", "degree",
        "graduation_date", "gpa", "cover_letter_template", "resume_highlight",
    ]
    for name in text_fields:
        if name in form:
            setattr(profile, name, str(form.get(name, "")).strip())
    for name in ("target_titles", "locations", "must_have_keywords", "exclude_keywords", "excluded_companies"):
        if name in form:
            setattr(profile, name, _lines(str(form.get(name, ""))))
    if "resume_skills" in form:
        profile.resume_skills = sorted({normalize_skill(s) for s in _lines(str(form.get("resume_skills", "")))})
    int_fields = {
        "min_salary": (0, 10_000_000), "daily_goal": (1, 200), "alert_threshold": (0, 100),
        "sync_interval_minutes": (10, 1440), "digest_hour": (-1, 23), "follow_up_days": (1, 60), "max_age_days": (0, 365),
    }
    for name, (low, high) in int_fields.items():
        if name in form:
            try:
                setattr(profile, name, max(low, min(high, int(str(form.get(name)) or 0))))
            except ValueError:
                pass
    if "years_experience" in form:
        try:
            profile.years_experience = max(0.0, float(str(form.get("years_experience")) or 0))
        except ValueError:
            pass
    if form.get("experience_level") in EXPERIENCE_LEVELS:
        profile.experience_level = str(form.get("experience_level"))
    if form.get("section") == "preferences":
        for name in ("remote_ok", "remote_only"):
            setattr(profile, name, form.get(name) == "on")
    if form.get("section") == "personal":
        for name in ("work_authorized", "needs_sponsorship"):
            setattr(profile, name, form.get(name) == "on")
    session.flush()
    if form.get("section") in ("preferences", "resume"):
        service.rescore(session)
    rescored = form.get("section") in ("preferences", "resume")
    return redirect(_next(form.get("next"), "/profile"), msg="Saved" + (". Match scores updated." if rescored else "."))


async def _read_resume(file: UploadFile) -> tuple[str, bytes]:
    data = await file.read()
    if not data:
        raise ValueError("That file was empty.")
    try:
        text = resume.extract_text(file.filename or "resume.txt", data)
    except resume.ResumeError as exc:
        raise ValueError(str(exc)) from exc
    except Exception as exc:
        raise ValueError(f"Couldn't read that file ({exc}). Try saving it as PDF or DOCX again.") from exc
    if len(text) < 50:
        raise ValueError("Couldn't find any text in that file. If it's a scanned PDF, upload a DOCX or TXT version instead.")
    return text, data


@app.post("/profile/resume")
async def resume_upload(
    file: UploadFile = File(...), next: str = Form("/resume"), session: Session = Depends(get_session)
):
    back = _next(next, "/resume")
    try:
        text, data = await _read_resume(file)
    except ValueError as exc:
        return redirect(back.split("?")[0] if "step=" not in back else "/welcome", err=str(exc))
    target = get_settings().upload_dir / Path(file.filename or "resume").name
    target.write_bytes(data)
    analysis = resume.analyze(text)
    profile = service.get_profile(session)
    profile.resume_filename = target.name
    profile.resume_text = text
    profile.resume_skills = analysis["skills"]
    profile.years_experience = analysis["years_experience"]
    service.rescore(session)
    report = grader.grade_resume(text, analysis["years_experience"], service.skill_demand(session))
    return redirect(
        back,
        msg=f"Resume read: {len(analysis['skills'])} skills, about {analysis['years_experience']:g} years of experience. "
            f"Resume grade: {report.grade} ({report.score}/100).",
    )


@app.get("/sources", response_class=HTMLResponse)
def sources_page(request: Request, session: Session = Depends(get_session)):
    sources = list(session.scalars(select(Source).order_by(Source.created_at)))
    counts = dict(session.execute(select(Job.source_id, func.count(Job.id)).group_by(Job.source_id)).all())
    return render(request, "sources.html", session, sources=sources, registry=REGISTRY, counts=counts)


@app.get("/sources/new", response_class=HTMLResponse)
def source_new(request: Request, kind: str, session: Session = Depends(get_session)):
    adapter = get_source(kind)
    return render(request, "source_form.html", session, adapter=adapter, source=None, config={})


@app.get("/sources/{source_id:int}/edit", response_class=HTMLResponse)
def source_edit(source_id: int, request: Request, session: Session = Depends(get_session)):
    source = session.get(Source, source_id)
    if not source:
        raise HTTPException(404)
    return render(request, "source_form.html", session, adapter=get_source(source.kind), source=source, config=source.config)


async def _config_from_form(request: Request, adapter, existing: dict) -> tuple[str, dict]:
    form = await request.form()
    config = {}
    for field in adapter.fields:
        value = str(form.get(field.name, "")).strip()
        if field.type == "password" and not value:
            value = existing.get(field.name, "")
        config[field.name] = value
    missing = [f.label for f in adapter.fields if f.required and not config.get(f.name)]
    if missing:
        raise ValueError("Missing: " + ", ".join(missing))
    return str(form.get("name") or adapter.label).strip(), config


@app.post("/sources")
async def source_create(request: Request, kind: str = Form(...), session: Session = Depends(get_session)):
    adapter = get_source(kind)
    form = await request.form()
    back = _next(form.get("next"), "/sources")
    try:
        name, config = await _config_from_form(request, adapter, {})
    except ValueError as exc:
        return redirect(back if back != "/sources" else f"/sources/new?kind={kind}", err=str(exc))
    source = Source(kind=kind, name=name)
    source.config = config
    session.add(source)
    session.flush()
    session.commit()
    threading.Thread(target=_background_sync, args=([source.id],), daemon=True).start()
    return redirect(back, msg=f"Connected {name}. Pulling in jobs now; they'll show up in a minute.")


@app.post("/sources/{source_id:int}")
async def source_update(source_id: int, request: Request, session: Session = Depends(get_session)):
    source = session.get(Source, source_id)
    if not source:
        raise HTTPException(404)
    try:
        name, config = await _config_from_form(request, get_source(source.kind), source.config)
    except ValueError as exc:
        return redirect(f"/sources/{source_id}/edit", err=str(exc))
    source.name = name
    source.config = config
    return redirect("/sources", msg="Saved")


@app.post("/sources/{source_id:int}/toggle")
def source_toggle(source_id: int, session: Session = Depends(get_session)):
    source = session.get(Source, source_id)
    if source:
        source.enabled = not source.enabled
    return redirect("/sources")


@app.post("/sources/{source_id:int}/delete")
def source_delete(source_id: int, session: Session = Depends(get_session)):
    source = session.get(Source, source_id)
    if source:
        session.delete(source)
    return redirect("/sources", msg="Source removed. Jobs it found are kept.")


@app.post("/sources/{source_id:int}/sync")
def source_sync(source_id: int, session: Session = Depends(get_session)):
    if service.sync_running():
        return redirect("/sources", err="A sync is already running")
    try:
        run = service.run_sync(session, [source_id])
    except RuntimeError as exc:
        return redirect("/sources", err=str(exc))
    if run.errors:
        return redirect("/sources", err="; ".join(run.errors))
    return redirect("/sources", msg=f"Fetched {run.fetched} postings, {run.new_jobs} new")


@app.get("/import", response_class=HTMLResponse)
def import_page(request: Request, session: Session = Depends(get_session)):
    return render(request, "import.html", session, boards=list(BOARDS))


@app.post("/import")
async def import_email(
    board: str = Form(""),
    html: str = Form(""),
    file: UploadFile | None = File(None),
    session: Session = Depends(get_session),
):
    postings: list[JobPosting] = []
    if file is not None and file.filename:
        raw = await file.read()
        postings = parse_message(raw, board or None)
        if not postings and board:
            postings = parse_alert_html(raw.decode("utf-8", errors="ignore"), board)
    elif html.strip():
        if board not in BOARDS:
            return redirect("/import", err="Pick which site the email came from")
        content = html if "<a" in html else html.replace("\n", "<br>")
        postings = parse_alert_html(content, board, utcnow())
    if not postings:
        return redirect("/import", err="No job postings found. Try uploading the original .eml file.")
    new_jobs = service.store_postings(session, postings, "email")
    service.rescore(session)
    return redirect("/jobs?sort=newest", msg=f"Found {len(postings)} postings, {len(new_jobs)} new")


@app.get("/alerts", response_class=HTMLResponse)
def alerts_page(request: Request, session: Session = Depends(get_session)):
    channels = list(session.scalars(select(Channel).order_by(Channel.id)))
    return render(request, "alerts.html", session, channels=channels, channel_fields=notify.CHANNEL_FIELDS)


@app.post("/alerts")
async def alert_create(request: Request, kind: str = Form(...), session: Session = Depends(get_session)):
    if kind not in notify.CHANNEL_FIELDS:
        raise HTTPException(400)
    label, fields = notify.CHANNEL_FIELDS[kind]
    form = await request.form()
    config = {f.name: str(form.get(f.name, "")).strip() or f.default for f in fields}
    back = _next(form.get("next"), "/alerts")
    missing = [f.label for f in fields if f.required and not config.get(f.name)]
    if missing:
        return redirect(back, err="Missing: " + ", ".join(missing))
    channel = Channel(kind=kind, name=str(form.get("name") or label))
    channel.config = config
    session.add(channel)
    return redirect(back, msg=f"Added {channel.name}. Use Test to make sure it arrives.")


@app.post("/alerts/{channel_id:int}/test")
def alert_test(channel_id: int, session: Session = Depends(get_session)):
    channel = session.get(Channel, channel_id)
    if not channel:
        raise HTTPException(404)
    sample = service.recommended(session, limit=3) or []
    items = service._alert_items(sample) or [
        notify.AlertItem("Example Software Engineer", "Example Co", "Remote", 88, "https://example.com", "Test")
    ]
    try:
        notify.send(channel.kind, channel.config, "JobRadar test notification", items)
    except notify.NotifyError as exc:
        channel.last_error = str(exc)
        return redirect("/alerts", err=f"Test failed: {exc}")
    channel.last_error = ""
    return redirect("/alerts", msg="Test notification sent")


@app.post("/alerts/{channel_id:int}/toggle")
def alert_toggle(channel_id: int, session: Session = Depends(get_session)):
    channel = session.get(Channel, channel_id)
    if channel:
        channel.enabled = not channel.enabled
    return redirect("/alerts")


@app.post("/alerts/{channel_id:int}/delete")
def alert_delete(channel_id: int, session: Session = Depends(get_session)):
    channel = session.get(Channel, channel_id)
    if channel:
        session.delete(channel)
    return redirect("/alerts", msg="Channel removed")


@app.post("/digest")
def digest_now(session: Session = Depends(get_session)):
    sent = service.maybe_send_digest(session, force=True)
    return redirect("/alerts", msg="Digest sent" if sent else "No open matches to send yet")


@app.get("/answers", response_class=HTMLResponse)
def answers_page(request: Request, session: Session = Depends(get_session)):
    answers = list(session.scalars(select(Answer).order_by(Answer.id)))
    return render(request, "answers.html", session, answers=answers, placeholders=apply.placeholders())


@app.post("/answers")
def answer_save(
    answer_id: int = Form(0),
    question: str = Form(...),
    answer: str = Form(""),
    keywords: str = Form(""),
    session: Session = Depends(get_session),
):
    record = session.get(Answer, answer_id) if answer_id else None
    if record is None:
        record = Answer()
        session.add(record)
    record.question, record.answer, record.keywords = question.strip(), answer.strip(), keywords.strip()
    return redirect("/answers", msg="Saved")


@app.post("/answers/{answer_id:int}/delete")
def answer_delete(answer_id: int, session: Session = Depends(get_session)):
    record = session.get(Answer, answer_id)
    if record:
        session.delete(record)
    return redirect("/answers", msg="Deleted")


def _find_job_by_url(session: Session, url: str) -> Job | None:
    if not url:
        return None
    def key(value: str) -> str:
        parsed = urlparse(value)
        return f"{parsed.netloc.removeprefix('www.')}{parsed.path}".rstrip("/").lower()

    base = key(url)
    if not base:
        return None
    candidates = session.scalars(
        select(Job).where(or_(Job.url.contains(base.split("/", 1)[-1]), Job.apply_url.contains(base.split("/", 1)[-1])))
    )
    for job in candidates:
        if base in (key(job.url), key(job.apply_url)) or key(job.apply_url).startswith(base + "/") or base.startswith(key(job.url) + "/"):
            return job
    return None


def _job_json(job: Job) -> dict:
    return {
        "id": job.id, "title": job.title, "company": job.company, "location": job.location,
        "score": job.score, "status": job.status, "url": job.link,
    }


@app.get("/api/autofill")
def api_autofill(session: Session = Depends(get_session)):
    p = service.get_profile(session)
    first, _, last = (p.full_name or "").partition(" ")
    return {
        "profile": {
            "full_name": p.full_name, "first_name": first, "last_name": last, "email": p.email, "phone": p.phone,
            "city": p.city, "linkedin": p.linkedin_url, "github": p.github_url, "portfolio": p.portfolio_url,
            "school": p.school, "degree": p.degree, "graduation_date": p.graduation_date, "gpa": p.gpa,
            "work_authorized": p.work_authorized, "needs_sponsorship": p.needs_sponsorship,
            "years_experience": p.years_experience, "resume_filename": p.resume_filename,
        },
    }


@app.post("/api/answer")
async def api_answer(request: Request, session: Session = Depends(get_session)):
    body = await request.json()
    profile = service.get_profile(session)
    job = _find_job_by_url(session, body.get("url", ""))
    if job is None and body.get("company"):
        job = Job(title=body.get("title") or "this", company=body["company"], location="", board="")
    answers = list(session.scalars(select(Answer)))
    results = {}
    for question in body.get("questions", [])[:60]:
        match = apply.best_answer(question, answers)
        if match:
            results[question] = apply.render(match.answer, profile, job)
    return {"answers": results, "cover_letter": apply.render(profile.cover_letter_template, profile, job)}


@app.post("/api/jobs/capture")
async def api_capture(request: Request, session: Session = Depends(get_session)):
    body = await request.json()
    url = body.get("url", "")
    job = _find_job_by_url(session, url)
    if job is None:
        if not body.get("title"):
            return JSONResponse({"error": "title required"}, status_code=400)
        posting = JobPosting(
            external_id=url or f"manual:{utcnow().timestamp()}", title=body["title"][:300],
            company=body.get("company", ""), location=body.get("location", ""), url=url,
            description=html_to_text(body.get("description", ""))[:20000], board=body.get("board") or "Saved from browser",
        )
        created = service.store_postings(session, [posting], "manual")
        if not created:
            return JSONResponse({"error": "duplicate"}, status_code=409)
        job = created[0]
        service.score_one(session, job)
        job.status = "saved"
    if body.get("applied"):
        service.mark_applied(job, service.get_profile(session))
    session.flush()
    return _job_json(job)


@app.get("/api/queue")
def api_queue(session: Session = Depends(get_session)):
    profile = service.get_profile(session)
    return {
        "queue": [_job_json(j) for j in service.todays_queue(session, profile)],
        "applied_today": service.applied_count(session, date.today()),
        "goal": profile.daily_goal,
    }


@app.get("/api/lookup")
def api_lookup(url: str, session: Session = Depends(get_session)):
    job = _find_job_by_url(session, url)
    return {"job": _job_json(job) if job else None}


TITLE_SUGGESTIONS = {
    "python": ["Software Engineer", "Backend Engineer", "Data Engineer"],
    "javascript": ["Frontend Engineer", "Full Stack Developer"],
    "react": ["Frontend Engineer", "Full Stack Developer"],
    "java": ["Software Engineer", "Backend Engineer"],
    "sql": ["Data Analyst", "Business Analyst"],
    "excel": ["Business Analyst", "Financial Analyst", "Operations Analyst"],
    "tableau": ["Data Analyst", "Business Intelligence Analyst"],
    "machine learning": ["Machine Learning Engineer", "Data Scientist"],
    "statistics": ["Data Scientist", "Data Analyst"],
    "aws": ["Cloud Engineer", "DevOps Engineer"],
    "kubernetes": ["DevOps Engineer", "Site Reliability Engineer"],
    "figma": ["Product Designer", "UX Designer"],
    "ui/ux design": ["UX Designer", "Product Designer"],
    "product management": ["Product Manager", "Associate Product Manager"],
    "project management": ["Project Manager", "Program Coordinator"],
    "seo": ["Marketing Coordinator", "Digital Marketing Specialist"],
    "social media": ["Social Media Coordinator", "Marketing Coordinator"],
    "content marketing": ["Content Marketing Specialist", "Marketing Coordinator"],
    "sales": ["Sales Development Representative", "Account Executive"],
    "accounting": ["Staff Accountant", "Accounting Analyst"],
    "financial analysis": ["Financial Analyst", "FP&A Analyst"],
    "human resources": ["HR Coordinator", "Recruiting Coordinator"],
    "customer service": ["Customer Success Associate", "Customer Support Specialist"],
    "cad": ["Mechanical Engineer", "Design Engineer"],
    "lab skills": ["Research Assistant", "Lab Technician"],
    "clinical": ["Clinical Research Coordinator", "Medical Assistant"],
}
FREE_SOURCES = [("remotive", "Remotive", {}), ("remoteok", "Remote OK", {}), ("themuse", "The Muse", {"pages": "2"})]
WELCOME_STEPS = ["Resume", "What you want", "Where to look", "Alerts"]


def _title_suggestions(profile) -> list[str]:
    seen = []
    for skill in profile.resume_skills or []:
        for title in TITLE_SUGGESTIONS.get(skill, []):
            if title not in seen and title not in (profile.target_titles or []):
                seen.append(title)
    return seen[:8]


@app.get("/welcome", response_class=HTMLResponse)
def welcome(request: Request, step: int = 1, session: Session = Depends(get_session)):
    profile = service.get_profile(session)
    step = max(1, min(len(WELCOME_STEPS), step))
    sources = list(session.scalars(select(Source).order_by(Source.created_at)))
    channels = list(session.scalars(select(Channel)))
    return render(
        request, "welcome.html", session,
        step=step, steps=WELCOME_STEPS, levels=EXPERIENCE_LEVELS, suggestions=_title_suggestions(profile),
        sources=sources, channels=channels, registry=REGISTRY, channel_fields=notify.CHANNEL_FIELDS,
        free_connected={s.kind for s in sources} >= {k for k, _, _ in FREE_SOURCES},
        ntfy_topic="jobradar-" + secrets.token_hex(4),
        report=grader.grade_resume(profile.resume_text, profile.years_experience) if profile.resume_text else None,
    )


@app.post("/welcome/finish")
def welcome_finish(session: Session = Depends(get_session)):
    profile = service.get_profile(session)
    profile.onboarded = True
    has_sources = session.scalar(select(func.count(Source.id)))
    session.commit()
    if has_sources:
        if not service.sync_running():
            threading.Thread(target=_background_sync, daemon=True).start()
        return redirect("/", msg="You're set up. Checking your job sites now; matches will appear here in a minute.")
    return redirect("/", msg="You're set up. Connect a job site any time from Settings → Job sites.")


@app.post("/sources/quick-add")
def sources_quick_add(request: Request, next: str = Form("/sources"), session: Session = Depends(get_session)):
    existing = {kind for (kind,) in session.execute(select(Source.kind))}
    added = []
    for kind, name, config in FREE_SOURCES:
        if kind in existing:
            continue
        source = Source(kind=kind, name=name)
        source.config = config
        session.add(source)
        added.append(name)
    session.commit()
    if added:
        threading.Thread(target=_background_sync, daemon=True).start()
    message = f"Added {', '.join(added)}." if added else "Those free sources are already connected."
    return redirect(_next(next, "/sources"), msg=message)


def _fit_jobs(session: Session) -> list[Job]:
    return list(session.scalars(
        select(Job).where(Job.description != "", Job.status != "dismissed").order_by(Job.score.desc()).limit(40)
    ))


@app.get("/resume", response_class=HTMLResponse)
def resume_page(request: Request, job: int = 0, session: Session = Depends(get_session)):
    profile = service.get_profile(session)
    demand = service.skill_demand(session)
    report = grader.grade_resume(profile.resume_text, profile.years_experience, demand) if profile.resume_text else None
    fit_job = session.get(Job, job) if job else None
    fit = grader.job_fit(profile.resume_text, fit_job.title, fit_job.description) if fit_job and profile.resume_text else None
    return render(
        request, "resume.html", session,
        report=report, demand=demand, fit=fit, fit_job=fit_job, fit_jobs=_fit_jobs(session), draft=False,
    )


@app.post("/resume/grade", response_class=HTMLResponse)
async def resume_grade(
    request: Request,
    file: UploadFile | None = File(None),
    text: str = Form(""),
    job_text: str = Form(""),
    job_title: str = Form(""),
    session: Session = Depends(get_session),
):
    profile = service.get_profile(session)
    draft = False
    resume_text = profile.resume_text
    if file is not None and file.filename:
        try:
            resume_text, _ = await _read_resume(file)
        except ValueError as exc:
            return redirect("/resume", err=str(exc))
        draft = True
    elif text.strip():
        resume_text, draft = text, True
    if not resume_text:
        return redirect("/resume", err="Upload a resume first.")
    demand = service.skill_demand(session)
    years = resume.estimate_years_experience(resume_text) if draft else profile.years_experience
    report = grader.grade_resume(resume_text, years, demand)
    fit = grader.job_fit(resume_text, job_title or "this role", job_text) if job_text.strip() else None
    fit_job = Job(title=job_title or "Pasted job description", company="") if fit else None
    return render(
        request, "resume.html", session,
        report=report, demand=demand, fit=fit, fit_job=fit_job, fit_jobs=_fit_jobs(session), draft=draft,
    )

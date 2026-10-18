import argparse
import logging
import sys
from pathlib import Path

from .config import get_settings
from .db import init_db, session_scope


def _serve(args) -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "jobradar.web.app:app",
        host=args.host or settings.host,
        port=args.port or settings.port,
        reload=args.reload,
    )


def _sync(args) -> None:
    from . import service

    with session_scope() as session:
        run = service.run_sync(session, send_alerts=not args.no_alerts)
        print(f"Checked {run.fetched} postings, {run.new_jobs} new, {run.alerted} alerted")
        for error in run.errors:
            print(f"  error: {error}", file=sys.stderr)


def _digest(args) -> None:
    from . import service

    with session_scope() as session:
        sent = service.maybe_send_digest(session, force=True)
        print("Digest sent" if sent else "No open matches to send")


def _rescore(args) -> None:
    from . import service

    with session_scope() as session:
        print(f"Re-scored {service.rescore(session)} jobs")


def _resume(args) -> None:
    from . import resume, service

    path = Path(args.path)
    text = resume.extract_text(path.name, path.read_bytes())
    analysis = resume.analyze(text)
    with session_scope() as session:
        profile = service.get_profile(session)
        profile.resume_filename = path.name
        profile.resume_text = text
        profile.resume_skills = analysis["skills"]
        profile.years_experience = analysis["years_experience"]
        service.rescore(session)
    print(f"Found {len(analysis['skills'])} skills: {', '.join(analysis['skills'])}")
    print(f"Estimated experience: {analysis['years_experience']:g} years")


def _top(args) -> None:
    from . import service

    with session_scope() as session:
        for job in service.recommended(session, limit=args.limit):
            print(f"{job.score:5.1f}  {job.title} | {job.company} | {job.location} | {job.link}")


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="jobradar", description="Find, rank and apply to jobs faster.")
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="Run the web app (default)")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    serve.add_argument("--reload", action="store_true")
    serve.set_defaults(func=_serve)

    sync = sub.add_parser("sync", help="Fetch jobs from every enabled connection")
    sync.add_argument("--no-alerts", action="store_true")
    sync.set_defaults(func=_sync)

    sub.add_parser("digest", help="Send the daily digest now").set_defaults(func=_digest)
    sub.add_parser("rescore", help="Recompute every match score").set_defaults(func=_rescore)

    res = sub.add_parser("resume", help="Import a resume file (PDF, DOCX or TXT)")
    res.add_argument("path")
    res.set_defaults(func=_resume)

    top = sub.add_parser("top", help="Print your best open matches")
    top.add_argument("--limit", type=int, default=20)
    top.set_defaults(func=_top)

    args = parser.parse_args(argv)
    init_db()
    if not getattr(args, "func", None):
        args = parser.parse_args(["serve", *(argv or [])])
    args.func(args)

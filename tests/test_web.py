import pytest
from conftest import RESUME
from fastapi.testclient import TestClient

from jobradar import service
from jobradar.models import Job
from jobradar.sources import JobPosting
from jobradar.web.app import app

API = {"X-JobRadar": "1"}


@pytest.fixture
def client(db):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def seeded(client, db):
    client.post("/profile/resume", files={"file": ("resume.txt", RESUME.encode())})
    client.post("/profile", data={"section": "preferences", "target_titles": "Software Engineer", "locations": "Seattle"})
    postings = [
        JobPosting(external_id=str(i), title=t, company=f"Co {i}", location="Seattle, WA", url=f"https://jobs.example.com/{i}",
                   description=d, board="Test")
        for i, (t, d) in enumerate([
            ("Software Engineer", "Python, AWS, React, Docker"),
            ("Data Engineer", "SQL, Airflow, Spark"),
            ("Sales Associate", "Retail sales"),
        ])
    ]
    service.store_postings(db, postings, "test")
    service.rescore(db)
    db.commit()
    return client


def test_all_pages_render(seeded, db):
    job_id = db.query(Job).first().id
    for path in ["/", "/jobs", "/jobs?status=all&sort=newest&days=7&min_score=10&q=engineer", "/queue", "/tracker",
                 "/profile", "/sources", "/alerts", "/answers", "/import", "/jobs/new", f"/jobs/{job_id}",
                 "/sources/new?kind=email", f"/jobs/{job_id}/cover-letter.txt"]:
        response = seeded.get(path)
        assert response.status_code == 200, path


def test_resume_upload_extracts_skills(seeded, db):
    profile = service.get_profile(db)
    db.refresh(profile)
    assert "python" in profile.resume_skills
    assert profile.years_experience > 2


def test_job_detail_shows_cover_letter_and_answers(seeded, db):
    job = db.query(Job).filter_by(title="Software Engineer").one()
    page = seeded.get(f"/jobs/{job.id}").text
    assert "Co 0 hiring team" in page
    assert "python" in page
    assert "Why do you want to work at Co 0?" in page


def test_bulk_queue_and_queue_page(seeded, db):
    ids = [j.id for j in db.query(Job).all()[:2]]
    response = seeded.post("/jobs/bulk", data={"ids": ids, "action": "queued"}, follow_redirects=False)
    assert response.status_code == 303
    page = seeded.get("/queue").text
    assert page.count('data-queue-item=') == 2


def test_import_pasted_email(seeded, fixture_text):
    response = seeded.post("/import", data={"board": "Indeed", "html": fixture_text("indeed_alert.html")})
    assert response.status_code == 200
    assert "Contoso Analytics" in response.text


def test_source_create_validates_required_fields(seeded):
    response = seeded.post("/sources", data={"kind": "adzuna", "name": "Adzuna"}, follow_redirects=False)
    assert "Missing" in response.headers["location"]


def test_cross_origin_posts_are_blocked(seeded):
    response = seeded.post("/sync", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    assert seeded.post("/api/jobs/capture", json={"title": "x"}).status_code == 403


def test_extension_api(seeded, db):
    autofill = seeded.get("/api/autofill").json()
    assert autofill["profile"]["email"] == ""
    found = seeded.get("/api/lookup", params={"url": "https://jobs.example.com/0?utm=1"}).json()
    assert found["job"]["title"] == "Software Engineer"

    created = seeded.post("/api/jobs/capture", headers=API, json={
        "url": "https://boards.greenhouse.io/x/jobs/9", "title": "Platform Engineer", "company": "X",
        "description": "<p>Kubernetes and Python</p>",
    }).json()
    assert created["status"] == "saved" and created["score"] > 0

    applied = seeded.post("/api/jobs/capture", headers=API, json={"url": "https://boards.greenhouse.io/x/jobs/9", "applied": True}).json()
    assert applied["id"] == created["id"] and applied["status"] == "applied"

    answers = seeded.post("/api/answer", headers=API, json={
        "url": "https://boards.greenhouse.io/x/jobs/9",
        "questions": ["Will you now or in the future require sponsorship for employment visa status?", "Favorite color?"],
    }).json()
    assert list(answers["answers"].values()) == ["No"]
    assert "X hiring team" in answers["cover_letter"]


def test_first_visit_goes_to_setup_guide(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/welcome"
    for step in range(1, 5):
        assert client.get(f"/welcome?step={step}").status_code == 200


def test_setup_guide_flow(client, db):
    response = client.post("/profile/resume", data={"next": "/welcome?step=2"},
                           files={"file": ("resume.txt", RESUME.encode())}, follow_redirects=False)
    assert response.headers["location"].startswith("/welcome?step=2")
    assert "Resume+grade" in response.headers["location"]

    step2 = client.get("/welcome?step=2").text
    assert "Software Engineer" in step2 or "Data Engineer" in step2

    response = client.post("/profile", data={
        "section": "preferences", "next": "/welcome?step=3", "target_titles": "Data Engineer",
        "experience_level": "entry", "remote_ok": "on", "min_salary": "", "daily_goal": "15",
    }, follow_redirects=False)
    assert response.headers["location"].startswith("/welcome?step=3")
    profile = service.get_profile(db)
    db.refresh(profile)
    assert profile.target_titles == ["Data Engineer"] and profile.daily_goal == 15

    client.post("/sources/quick-add", data={"next": "/welcome?step=3"})
    assert "Connected" in client.get("/welcome?step=3").text

    response = client.post("/alerts", data={"kind": "ntfy", "name": "Phone", "topic": "jobradar-test", "next": "/welcome?step=4"},
                           follow_redirects=False)
    assert response.headers["location"].startswith("/welcome?step=4")

    response = client.post("/welcome/finish", follow_redirects=False)
    assert response.headers["location"].startswith("/?msg=")
    db.refresh(profile)
    assert profile.onboarded
    assert client.get("/", follow_redirects=False).status_code == 200


def test_resume_page_grades_and_matches(seeded, db):
    page = seeded.get("/resume").text
    assert "/100" in page and "What to fix" in page
    job = db.query(Job).filter_by(title="Data Engineer").one()
    page = seeded.get(f"/resume?job={job.id}").text
    assert "They want, not on your resume" in page and "spark" in page

    draft = seeded.post("/resume/grade", data={"text": "Jordan\n- Responsible for stuff and things at work\n" * 3}).text
    assert "You're looking at a draft" in draft

    pasted = seeded.post("/resume/grade", data={"job_title": "Analyst", "job_text": "Looker and Tableau and SQL. Looker dashboards."}).text
    assert "looker" in pasted


def test_resume_page_without_resume(client):
    assert "Upload your resume to get it graded" in client.get("/resume").text


def test_job_detail_shows_tailoring(seeded, db):
    job = db.query(Job).filter_by(title="Data Engineer").one()
    assert "Tailor your resume" in seeded.get(f"/jobs/{job.id}").text


def test_bad_resume_upload_shows_error(client):
    response = client.post("/profile/resume", files={"file": ("resume.xyz", b"hello")}, follow_redirects=False)
    assert "err=" in response.headers["location"]


def test_old_databases_get_new_columns(db):
    from sqlalchemy import inspect, text

    from jobradar.db import get_engine, init_db

    with get_engine().begin() as conn:
        conn.execute(text("ALTER TABLE profile DROP COLUMN onboarded"))
    init_db()
    assert "onboarded" in {c["name"] for c in inspect(get_engine()).get_columns("profile")}


def test_job_status_and_notes(seeded, db):
    job = db.query(Job).filter_by(title="Software Engineer").one()
    response = seeded.post(f"/jobs/{job.id}/status", data={"status": "applied"}, follow_redirects=False)
    assert response.status_code == 303 and "follow-up" in response.headers["location"]
    assert seeded.post(f"/jobs/{job.id}/status", data={"status": ""}, follow_redirects=False).status_code == 303
    response = seeded.post(f"/jobs/{job.id}/notes", data={"notes": "Talked to Dana", "follow_up_on": "2026-11-01"}, follow_redirects=False)
    assert response.headers["location"].startswith(f"/jobs/{job.id}")
    db.refresh(job)
    assert job.status == "applied" and job.notes == "Talked to Dana"

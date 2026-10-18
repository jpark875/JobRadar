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

from datetime import date, datetime, timedelta

from conftest import RESUME

from jobradar.matching import JobView, Matcher, Preferences, detect_level
from jobradar.resume import analyze, estimate_years_experience
from jobradar.skills import extract_skills

NOW = datetime(2026, 10, 18, 12, 0)


def prefs(**overrides):
    analysis = analyze(RESUME)
    base = dict(
        resume_text=RESUME, resume_skills=analysis["skills"], years_experience=3, experience_level="entry",
        target_titles=["Software Engineer", "Data Engineer"], locations=["Seattle"],
    )
    base.update(overrides)
    return Preferences(**base)


def job(title, description="", **kw):
    kw.setdefault("posted_at", NOW - timedelta(days=1))
    return JobView(title=title, description=description, **kw)


def test_skill_extraction_handles_symbols_and_aliases():
    skills = extract_skills("We use C++, C#, Node.js, k8s and Postgres. Experience with REST APIs and CI/CD.")
    for expected in ("c++", "c#", "node.js", "kubernetes", "postgresql", "rest apis", "ci/cd"):
        assert expected in skills
    assert "java" not in extract_skills("JavaScript only")


def test_resume_analysis():
    analysis = analyze(RESUME)
    for skill in ("python", "sql", "aws", "airflow", "react", "typescript", "fastapi", "docker", "kubernetes"):
        assert skill in analysis["skills"]
    assert 3.0 <= estimate_years_experience(RESUME, date(2026, 10, 1)) <= 3.5


def test_relevant_job_outranks_irrelevant_one():
    matcher = Matcher(prefs(), now=NOW)
    good = matcher.score(job(
        "Software Engineer", "Build services in Python, SQL and AWS. Docker, Kubernetes, React a plus.", location="Seattle, WA"
    ))
    bad = matcher.score(job(
        "Registered Nurse", "Provide patient care using Epic EMR. BLS and ACLS required.", location="Miami, FL"
    ))
    assert good.score > 70
    assert bad.score < 35
    assert "python" in good.details["matched_skills"]
    assert any("Title close" in r for r in good.details["reasons"])


def test_seniority_mismatch_is_penalized():
    matcher = Matcher(prefs(), now=NOW)
    entry = matcher.score(job("Software Engineer I", "Python and AWS", location="Seattle"))
    senior = matcher.score(job("Senior Staff Software Engineer", "Python and AWS. 10+ years of experience.", location="Seattle"))
    assert entry.score > senior.score + 15
    assert senior.details["warnings"]


def test_hard_filters():
    matcher = Matcher(prefs(exclude_keywords=["principal"], excluded_companies=["Evil Corp"], remote_only=True), now=NOW)
    assert matcher.score(job("Principal Engineer", remote=True)).filtered_reason
    assert matcher.score(job("Engineer", company="Evil Corp", remote=True)).filtered_reason
    assert matcher.score(job("Engineer", remote=False)).filtered_reason == "Not remote"
    assert matcher.score(job("Engineer", remote=True)).filtered_reason is None
    old = job("Engineer", remote=True, posted_at=NOW - timedelta(days=90))
    assert "Older" in matcher.score(old).filtered_reason


def test_salary_floor():
    matcher = Matcher(prefs(min_salary=120000), now=NOW)
    low = matcher.score(job("Software Engineer", "Python", location="Seattle", salary_max=80000))
    ok = matcher.score(job("Software Engineer", "Python", location="Seattle", salary_max=150000))
    assert ok.score > low.score
    assert any("below" in w for w in low.details["warnings"])


def test_title_only_jobs_from_email_still_rank():
    matcher = Matcher(prefs(), now=NOW)
    titled = matcher.score(job("Data Engineer", location="Seattle, WA"))
    unrelated = matcher.score(job("Barista", location="Seattle, WA"))
    assert titled.score > unrelated.score + 20


def test_detect_level():
    assert detect_level("Software Engineering Intern") == 0
    assert detect_level("New Grad Software Engineer") == 1
    assert detect_level("Sr. Data Scientist") == 3
    assert detect_level("Director of Engineering") == 4
    assert detect_level("Engineer", "Requires 6+ years of professional experience") == 3
    assert detect_level("Engineer") is None

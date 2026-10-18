from datetime import date, datetime, timedelta

import pytest
from conftest import RESUME

from jobradar import notify, service
from jobradar.models import Channel, Job, Source
from jobradar.resume import analyze
from jobradar.sources import REGISTRY, JobPosting
from jobradar.sources.base import BaseSource


class FakeSource(BaseSource):
    kind = "fake"
    label = "Fake"
    covers = "tests"
    postings: list[JobPosting] = []

    def fetch(self, config, ctx, client):
        return list(self.postings)


@pytest.fixture
def fake_source(db, monkeypatch):
    adapter = FakeSource()
    monkeypatch.setitem(REGISTRY, "fake", adapter)
    source = Source(kind="fake", name="Fake")
    source.config = {"token": "secret"}
    db.add(source)
    profile = service.get_profile(db)
    analysis = analyze(RESUME)
    profile.resume_text = RESUME
    profile.resume_skills = analysis["skills"]
    profile.years_experience = 3
    profile.target_titles = ["Software Engineer"]
    profile.locations = ["Seattle"]
    profile.alert_threshold = 60
    profile.daily_goal = 2
    db.flush()
    return adapter


def posting(i, title="Software Engineer", company=None, description="Python, AWS, Docker, React", **kw):
    return JobPosting(external_id=str(i), title=title, company=company or f"Company {i}", location="Seattle, WA",
                      url=f"https://example.com/jobs/{i}", description=description, board="Fake", **kw)


def test_config_is_encrypted_at_rest(fake_source, db):
    source = db.query(Source).one()
    assert "secret" not in source.config_encrypted
    assert source.config == {"token": "secret"}


def test_sync_stores_dedupes_scores_and_alerts(fake_source, db, monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "send", lambda kind, config, heading, items: sent.append((heading, items)))
    channel = Channel(kind="ntfy", name="Phone")
    channel.config = {"topic": "x"}
    db.add(channel)
    fake_source.postings = [
        posting(1),
        posting(2, title="Software  Engineer", company="Company 1"),
        posting(3, title="Line Cook", description="Prepare food"),
    ]
    run = service.run_sync(db)
    assert run.new_jobs == 2
    assert run.errors == []
    jobs = {j.title: j for j in db.query(Job)}
    assert jobs["Software Engineer"].score > 60 > jobs["Line Cook"].score
    assert len(sent) == 1 and len(sent[0][1]) == 1

    run = service.run_sync(db)
    assert run.new_jobs == 0
    assert len(sent) == 1


def test_source_errors_are_recorded(fake_source, db, monkeypatch):
    from jobradar.sources import SourceError

    def boom(*args):
        raise SourceError("bad key")

    monkeypatch.setattr(fake_source, "fetch", boom)
    run = service.run_sync(db, send_alerts=False)
    assert run.errors == ["Fake: bad key"]
    assert db.query(Source).one().last_status == "error"


def test_queue_and_apply_flow(fake_source, db):
    fake_source.postings = [posting(i) for i in range(5)]
    service.run_sync(db, send_alerts=False)
    profile = service.get_profile(db)
    queue = service.fill_queue(db, profile)
    assert len(queue) == 2
    assert all(j.status == "queued" for j in queue)

    service.mark_applied(queue[0], profile)
    assert queue[0].follow_up_on == date.today() + timedelta(days=profile.follow_up_days)
    assert service.applied_count(db, date.today()) == 1
    assert len(service.fill_queue(db, profile)) == 1
    assert service.streak(db) == 1


def test_follow_ups_due(fake_source, db):
    fake_source.postings = [posting(1)]
    service.run_sync(db, send_alerts=False)
    job = db.query(Job).one()
    service.mark_applied(job, service.get_profile(db), when=datetime.now() - timedelta(days=10))
    assert service.follow_ups_due(db) == [job]
    service.set_status(job, "rejected", service.get_profile(db))
    assert service.follow_ups_due(db) == []


def test_digest_sends_once_per_day(fake_source, db, monkeypatch):
    sent = []
    monkeypatch.setattr(service, "broadcast", lambda session, heading, jobs: sent.append(heading) or [])
    fake_source.postings = [posting(1)]
    service.run_sync(db, send_alerts=False)
    morning = datetime.combine(date.today(), datetime.min.time()) + timedelta(hours=9)
    assert service.maybe_send_digest(db, now=morning)
    assert not service.maybe_send_digest(db, now=morning + timedelta(hours=2))
    assert len(sent) == 1

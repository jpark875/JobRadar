from jobradar.sources.email_alerts import board_for_sender, parse_alert_html, parse_message


def test_board_detected_from_sender():
    assert board_for_sender("LinkedIn <jobalerts-noreply@linkedin.com>") == "LinkedIn"
    assert board_for_sender("Indeed <alert@indeed.com>") == "Indeed"
    assert board_for_sender("Handshake <notifications@mail.joinhandshake.com>") == "Handshake"
    assert board_for_sender("someone@example.com") is None


def test_linkedin_eml(fixture_text):
    jobs = parse_message(fixture_text("linkedin_alert.eml").encode())
    assert [j.title for j in jobs] == ["Software Engineer", "Backend Engineer, Payments"]
    first, second = jobs
    assert first.external_id == "linkedin:3901234567"
    assert first.company == "Acme Robotics"
    assert first.location == "Seattle, WA (Hybrid)"
    assert first.board == "LinkedIn"
    assert first.posted_at is not None
    assert second.remote
    assert second.salary_min == 130000


def test_indeed_html(fixture_text):
    jobs = parse_alert_html(fixture_text("indeed_alert.html"), "Indeed")
    assert len(jobs) == 2
    data = jobs[0]
    assert data.title == "Data Engineer"
    assert data.company == "Contoso Analytics"
    assert data.location == "Seattle, WA 98101"
    assert data.external_id == "indeed:4f2a9c1b7e3d5a60"
    assert data.salary_min == 95000
    assert jobs[1].remote


def test_handshake_tracked_links_fall_back_to_cards(fixture_text):
    jobs = parse_alert_html(fixture_text("handshake_alert.html"), "Handshake")
    assert [(j.title, j.company) for j in jobs] == [
        ("Software Engineering Intern - Summer 2027", "Globex Corporation"),
        ("Associate Data Analyst", "Initech"),
    ]
    assert jobs[0].location == "Chicago, IL"

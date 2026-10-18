import httpx
import pytest

from jobradar.sources import REGISTRY, SourceContext, SourceError, get_source

PAYLOADS = {
    "boards-api.greenhouse.io": {
        "jobs": [{
            "id": 101, "title": "Software Engineer, New Grad", "absolute_url": "https://boards.greenhouse.io/acme/jobs/101",
            "location": {"name": "Remote - US"}, "updated_at": "2026-10-14T10:00:00-04:00",
            "first_published": "2026-10-13T09:00:00-04:00", "company_name": "Acme",
            "content": "&lt;p&gt;We use &lt;b&gt;Python&lt;/b&gt; and Kubernetes.&lt;/p&gt;",
        }]
    },
    "api.lever.co": [{
        "id": "abc-123", "text": "Backend Engineer", "hostedUrl": "https://jobs.lever.co/plaid/abc-123",
        "applyUrl": "https://jobs.lever.co/plaid/abc-123/apply", "createdAt": 1760000000000,
        "categories": {"location": "San Francisco", "commitment": "Full-time", "team": "Eng"},
        "descriptionPlain": "Go, PostgreSQL, AWS", "workplaceType": "hybrid",
        "salaryRange": {"min": 150000, "max": 190000, "currency": "USD", "interval": "per-year-salary"},
    }],
    "api.ashbyhq.com": {
        "jobs": [{
            "id": "u1", "title": "Data Engineer", "location": "New York", "isRemote": True, "isListed": True,
            "publishedAt": "2026-10-10T12:00:00Z", "jobUrl": "https://jobs.ashbyhq.com/ramp/u1",
            "applyUrl": "https://jobs.ashbyhq.com/ramp/u1/application", "descriptionPlain": "Airflow, dbt, Snowflake",
            "employmentType": "FullTime",
            "compensation": {"summaryComponents": [
                {"compensationType": "Salary", "interval": "1 YEAR", "minValue": 140000, "maxValue": 180000}
            ]},
        }, {"id": "u2", "title": "Hidden", "isListed": False}]
    },
    "jsearch.p.rapidapi.com": {
        "data": [{
            "job_id": "js1", "job_title": "Software Engineer I", "employer_name": "Contoso", "job_publisher": "LinkedIn",
            "job_apply_link": "https://www.linkedin.com/jobs/view/1", "job_description": "React, TypeScript",
            "job_is_remote": False, "job_posted_at_datetime_utc": "2026-10-15T00:00:00.000Z", "job_city": "Seattle",
            "job_state": "WA", "job_country": "US", "job_min_salary": 50, "job_max_salary": 60,
            "job_salary_period": "HOUR", "job_employment_type": "FULLTIME",
        }]
    },
    "api.adzuna.com": {
        "results": [{
            "id": "4455", "title": "<strong>Junior</strong> Developer", "company": {"display_name": "Fabrikam"},
            "location": {"display_name": "Austin, Texas"}, "redirect_url": "https://www.adzuna.com/details/4455",
            "created": "2026-10-12T08:00:00Z", "description": "JavaScript and SQL", "salary_min": 70000, "salary_max": 90000,
        }]
    },
    "data.usajobs.gov": {
        "SearchResult": {"SearchResultItems": [{
            "MatchedObjectId": "777",
            "MatchedObjectDescriptor": {
                "PositionID": "IT-26-1", "PositionTitle": "IT Specialist", "OrganizationName": "Department of Energy",
                "PositionLocationDisplay": "Washington, DC", "PositionURI": "https://www.usajobs.gov/job/777",
                "ApplyURI": ["https://www.usajobs.gov/job/777/apply"], "PublicationStartDate": "2026-10-01T00:00:00",
                "PositionRemuneration": [{"MinimumRange": "72553", "MaximumRange": "94317", "RateIntervalCode": "PA"}],
                "UserArea": {"Details": {"JobSummary": "Linux and networking", "RemoteIndicator": False}},
                "PositionSchedule": [{"Name": "Full-time"}],
            },
        }]}
    },
    "remotive.com": {
        "jobs": [{
            "id": 9, "url": "https://remotive.com/remote-jobs/9", "title": "Python Developer", "company_name": "Remote Co",
            "job_type": "full_time", "publication_date": "2026-10-16T05:00:00", "candidate_required_location": "USA",
            "salary": "$100k", "description": "<p>Django</p>",
        }]
    },
    "remoteok.com": [
        {"legal": "API terms"},
        {"id": "55", "position": "DevOps Engineer", "company": "Cloudy", "tags": ["devops", "aws"], "url": "https://remoteok.com/55",
         "date": "2026-10-16T00:00:00+00:00", "description": "Terraform", "salary_min": 120000, "salary_max": 150000},
    ],
    "www.themuse.com": {
        "page_count": 1,
        "results": [{
            "id": 3, "name": "Marketing Associate", "contents": "<p>SEO and content</p>", "company": {"name": "Muse Co"},
            "locations": [{"name": "Flexible / Remote"}], "levels": [{"name": "Entry Level"}],
            "publication_date": "2026-10-11T00:00:00Z", "refs": {"landing_page": "https://www.themuse.com/jobs/3"},
        }],
    },
    "api.smartrecruiters.com": {
        "content": [{
            "id": "sr1", "name": "Support Engineer", "company": {"name": "Visa"}, "releasedDate": "2026-10-09T00:00:00.000Z",
            "location": {"city": "Denver", "region": "CO", "country": "us", "remote": False},
            "typeOfEmployment": {"label": "Full-time"}, "function": {"label": "Information Technology"},
        }]
    },
    "jooble.org": {
        "jobs": [{"id": 12, "title": "QA Analyst", "location": "Remote", "snippet": "Selenium", "salary": "$80k",
                  "source": "ZipRecruiter", "type": "Full-time", "link": "https://jooble.org/12", "company": "Tailspin",
                  "updated": "2026-10-14T00:00:00.0000000"}]
    },
}

CONFIGS = {
    "greenhouse": {"companies": "acme"},
    "lever": {"companies": "plaid"},
    "ashby": {"companies": "ramp"},
    "smartrecruiters": {"companies": "Visa"},
    "jsearch": {"api_key": "k"},
    "adzuna": {"app_id": "i", "app_key": "k"},
    "usajobs": {"email": "a@b.c", "api_key": "k"},
    "jooble": {"api_key": "k"},
    "remotive": {},
    "remoteok": {"tags": "aws"},
    "themuse": {"pages": "1"},
}


def make_client(requests: list):
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=PAYLOADS[request.url.host])

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("kind", sorted(CONFIGS))
def test_every_api_source_normalizes(kind):
    requests = []
    ctx = SourceContext(keywords=["software engineer"], locations=["Seattle"])
    jobs = get_source(kind).fetch(CONFIGS[kind], ctx, make_client(requests))
    assert requests, "no HTTP request made"
    assert jobs, f"{kind} returned no jobs"
    for job in jobs:
        assert job.title and job.external_id and job.url
        assert job.board


def test_field_specific_values():
    ctx = SourceContext(keywords=["engineer"])
    gh = get_source("greenhouse").fetch(CONFIGS["greenhouse"], ctx, make_client([]))[0]
    assert gh.remote and "Python" in gh.description and gh.posted_at.isoformat() == "2026-10-13T13:00:00"
    lever = get_source("lever").fetch(CONFIGS["lever"], ctx, make_client([]))[0]
    assert (lever.salary_min, lever.salary_max) == (150000, 190000)
    ashby = get_source("ashby").fetch(CONFIGS["ashby"], ctx, make_client([]))
    assert len(ashby) == 1 and ashby[0].remote and ashby[0].salary_max == 180000
    js = get_source("jsearch").fetch(CONFIGS["jsearch"], ctx, make_client([]))[0]
    assert js.board == "LinkedIn" and js.salary_min == 104000 and js.location == "Seattle, WA, US"
    adzuna = get_source("adzuna").fetch(CONFIGS["adzuna"], ctx, make_client([]))[0]
    assert adzuna.title == "Junior Developer"
    usa = get_source("usajobs").fetch(CONFIGS["usajobs"], ctx, make_client([]))[0]
    assert usa.salary_min == 72553 and usa.apply_url.endswith("/apply")
    remoteok = get_source("remoteok").fetch({"tags": "python"}, ctx, make_client([]))
    assert remoteok == []


def test_keywords_fall_back_to_profile_titles():
    requests = []
    get_source("jsearch").fetch({"api_key": "k"}, SourceContext(keywords=["data analyst"], locations=["Austin"]), make_client(requests))
    assert requests[0].url.params["query"] == "data analyst in Austin"
    assert requests[0].headers["X-RapidAPI-Key"] == "k"


def test_http_errors_become_source_errors():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401)))
    with pytest.raises(SourceError, match="credentials"):
        get_source("adzuna").fetch(CONFIGS["adzuna"], SourceContext(), client)


def test_ats_requires_companies():
    with pytest.raises(SourceError):
        get_source("greenhouse").fetch({}, SourceContext(), make_client([]))


def test_registry_fields_are_well_formed():
    for adapter in REGISTRY.values():
        assert adapter.label and adapter.covers
        assert len({f.name for f in adapter.fields}) == len(adapter.fields)

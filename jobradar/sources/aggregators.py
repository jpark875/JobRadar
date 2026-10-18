import httpx

from ..text import annualize, html_to_text, parse_datetime, parse_money
from .base import BaseSource, Field, JobPosting, SourceError, looks_remote

KEYWORDS_FIELD = Field(
    "keywords", "Search keywords", "text", False,
    "Comma separated. Leave blank to use the target titles from your profile.",
)
LOCATION_FIELD = Field("location", "Location", "text", False, "Leave blank to use the first location in your profile.")


def _date_window(max_age_days: int) -> str:
    if max_age_days <= 1:
        return "today"
    if max_age_days <= 3:
        return "3days"
    if max_age_days <= 7:
        return "week"
    return "month"


class JSearchSource(BaseSource):
    kind = "jsearch"
    label = "JSearch (Indeed, LinkedIn, Glassdoor, ZipRecruiter)"
    covers = "Indeed, LinkedIn, Glassdoor, ZipRecruiter, Monster and other boards indexed by Google for Jobs"
    description = (
        "The most reliable way to pull Indeed and LinkedIn listings. Uses the JSearch API on RapidAPI "
        "(free tier available)."
    )
    fields = [
        Field("api_key", "RapidAPI key", "password", True, "Sign up at rapidapi.com and subscribe to the JSearch API."),
        KEYWORDS_FIELD,
        LOCATION_FIELD,
        Field("country", "Country code", "text", False, "Two-letter code, defaults to us", "us"),
        Field("pages", "Pages per query", "number", False, "Each page is ~10 results and one API call.", "1"),
    ]

    def fetch(self, config, ctx, client):
        location = config.get("location") or (ctx.locations[0] if ctx.locations else "")
        headers = {"X-RapidAPI-Key": config.get("api_key", ""), "X-RapidAPI-Host": "jsearch.p.rapidapi.com"}
        out = []
        for keyword in self.queries(config, ctx):
            if not keyword:
                raise SourceError("Add search keywords or target titles in your profile")
            query = f"{keyword} in {location}" if location else keyword
            params = {
                "query": query,
                "page": 1,
                "num_pages": max(1, min(int(config.get("pages") or 1), 5)),
                "date_posted": _date_window(ctx.max_age_days),
                "country": config.get("country") or "us",
            }
            data = self.get_json(client, "https://jsearch.p.rapidapi.com/search", params=params, headers=headers)
            for job in data.get("data", []):
                location_label = ", ".join(x for x in (job.get("job_city"), job.get("job_state"), job.get("job_country")) if x)
                period = job.get("job_salary_period") or ""
                out.append(JobPosting(
                    external_id=str(job.get("job_id")),
                    title=job.get("job_title", ""),
                    company=job.get("employer_name", ""),
                    location=location_label or job.get("job_location", ""),
                    url=job.get("job_apply_link", ""),
                    apply_url=job.get("job_apply_link", ""),
                    description=job.get("job_description", ""),
                    remote=bool(job.get("job_is_remote")),
                    salary_min=annualize(parse_money(job.get("job_min_salary")), period),
                    salary_max=annualize(parse_money(job.get("job_max_salary")), period),
                    employment_type=job.get("job_employment_type", ""),
                    posted_at=parse_datetime(job.get("job_posted_at_datetime_utc") or job.get("job_posted_at_timestamp")),
                    board=job.get("job_publisher", "") or "JSearch",
                ))
        return out


class AdzunaSource(BaseSource):
    kind = "adzuna"
    label = "Adzuna"
    covers = "Adzuna's index of hundreds of job boards and company sites"
    description = "Free API key from developer.adzuna.com."
    fields = [
        Field("app_id", "App ID", "text", True),
        Field("app_key", "App key", "password", True),
        KEYWORDS_FIELD,
        LOCATION_FIELD,
        Field("country", "Country code", "text", False, "us, gb, ca, au, de, ...", "us"),
    ]

    def fetch(self, config, ctx, client):
        country = (config.get("country") or "us").lower()
        location = config.get("location") or (ctx.locations[0] if ctx.locations else "")
        out = []
        for keyword in self.queries(config, ctx):
            params = {
                "app_id": config.get("app_id", ""),
                "app_key": config.get("app_key", ""),
                "results_per_page": 50,
                "max_days_old": max(1, ctx.max_age_days or 30),
                "sort_by": "date",
                "content-type": "application/json",
            }
            if keyword:
                params["what"] = keyword
            if location:
                params["where"] = location
            data = self.get_json(client, f"https://api.adzuna.com/v1/api/jobs/{country}/search/1", params=params)
            for job in data.get("results", []):
                location_label = (job.get("location") or {}).get("display_name", "")
                description = html_to_text(job.get("description"))
                out.append(JobPosting(
                    external_id=str(job.get("id")),
                    title=html_to_text(job.get("title")),
                    company=(job.get("company") or {}).get("display_name", ""),
                    location=location_label,
                    url=job.get("redirect_url", ""),
                    description=description,
                    remote=looks_remote(location_label, job.get("title", "")),
                    salary_min=parse_money(job.get("salary_min")),
                    salary_max=parse_money(job.get("salary_max")),
                    employment_type=job.get("contract_time") or "",
                    posted_at=parse_datetime(job.get("created")),
                    board="Adzuna",
                ))
        return out


class USAJobsSource(BaseSource):
    kind = "usajobs"
    label = "USAJOBS"
    covers = "U.S. federal government jobs and internships (Pathways)"
    description = "Free API key from developer.usajobs.gov."
    fields = [
        Field("email", "Email used to register", "text", True),
        Field("api_key", "Authorization key", "password", True),
        KEYWORDS_FIELD,
        LOCATION_FIELD,
    ]

    def fetch(self, config, ctx, client):
        headers = {
            "Host": "data.usajobs.gov",
            "User-Agent": config.get("email", ""),
            "Authorization-Key": config.get("api_key", ""),
        }
        location = config.get("location") or (ctx.locations[0] if ctx.locations else "")
        out = []
        for keyword in self.queries(config, ctx):
            params = {"ResultsPerPage": 100, "DatePosted": min(60, max(1, ctx.max_age_days or 30)), "SortField": "opendate"}
            if keyword:
                params["Keyword"] = keyword
            if location:
                params["LocationName"] = location
            data = self.get_json(client, "https://data.usajobs.gov/api/search", params=params, headers=headers)
            for item in (data.get("SearchResult") or {}).get("SearchResultItems", []):
                job = item.get("MatchedObjectDescriptor") or {}
                pay = (job.get("PositionRemuneration") or [{}])[0]
                interval = {"PH": "hour", "PA": "year"}.get(pay.get("RateIntervalCode", ""), pay.get("Description", ""))
                details = (job.get("UserArea") or {}).get("Details") or {}
                location_label = job.get("PositionLocationDisplay", "")
                out.append(JobPosting(
                    external_id=str(job.get("PositionID") or item.get("MatchedObjectId")),
                    title=job.get("PositionTitle", ""),
                    company=job.get("OrganizationName", ""),
                    location=location_label,
                    url=job.get("PositionURI", ""),
                    apply_url=(job.get("ApplyURI") or [""])[0],
                    description="\n".join(filter(None, [details.get("JobSummary"), job.get("QualificationSummary")])),
                    remote=bool(details.get("RemoteIndicator")) or looks_remote(location_label),
                    salary_min=annualize(parse_money(pay.get("MinimumRange")), interval),
                    salary_max=annualize(parse_money(pay.get("MaximumRange")), interval),
                    employment_type=", ".join(s.get("Name", "") for s in job.get("PositionSchedule") or []),
                    posted_at=parse_datetime(job.get("PublicationStartDate")),
                    board="USAJOBS",
                ))
        return out


class JoobleSource(BaseSource):
    kind = "jooble"
    label = "Jooble"
    covers = "Jooble's aggregate of job boards in 60+ countries"
    description = "Free API key from jooble.org/api/about."
    fields = [Field("api_key", "API key", "password", True), KEYWORDS_FIELD, LOCATION_FIELD]

    def fetch(self, config, ctx, client):
        location = config.get("location") or (ctx.locations[0] if ctx.locations else "")
        out = []
        for keyword in self.queries(config, ctx):
            try:
                response = client.post(
                    f"https://jooble.org/api/{config.get('api_key', '')}",
                    json={"keywords": keyword, "location": location, "page": "1"},
                    timeout=30,
                )
            except httpx.HTTPError as exc:
                raise SourceError(f"Jooble request failed: {exc}") from exc
            if response.status_code >= 400:
                raise SourceError(f"Jooble returned HTTP {response.status_code}")
            for job in response.json().get("jobs", []):
                location_label = job.get("location", "")
                out.append(JobPosting(
                    external_id=str(job.get("id")),
                    title=html_to_text(job.get("title")),
                    company=job.get("company", ""),
                    location=location_label,
                    url=job.get("link", ""),
                    description=html_to_text(job.get("snippet")),
                    remote=looks_remote(location_label, job.get("title", "")),
                    salary_min=parse_money(job.get("salary")),
                    employment_type=job.get("type", ""),
                    posted_at=parse_datetime(job.get("updated")),
                    board=job.get("source") or "Jooble",
                ))
        return out


class RemotiveSource(BaseSource):
    kind = "remotive"
    label = "Remotive"
    covers = "Remote jobs from Remotive"
    description = "Free, no key required."
    fields = [KEYWORDS_FIELD, Field("category", "Category", "text", False, "e.g. software-dev, data, marketing, design")]

    def fetch(self, config, ctx, client):
        out = []
        for keyword in self.queries(config, ctx):
            params = {"limit": 100}
            if keyword:
                params["search"] = keyword
            if config.get("category"):
                params["category"] = config["category"]
            data = self.get_json(client, "https://remotive.com/api/remote-jobs", params=params)
            for job in data.get("jobs", []):
                out.append(JobPosting(
                    external_id=str(job.get("id")),
                    title=job.get("title", ""),
                    company=job.get("company_name", ""),
                    location=job.get("candidate_required_location") or "Remote",
                    url=job.get("url", ""),
                    description=html_to_text(job.get("description")),
                    remote=True,
                    salary_min=parse_money(job.get("salary")),
                    employment_type=job.get("job_type", ""),
                    posted_at=parse_datetime(job.get("publication_date")),
                    board="Remotive",
                ))
        return out


class RemoteOKSource(BaseSource):
    kind = "remoteok"
    label = "Remote OK"
    covers = "Remote jobs from remoteok.com"
    description = "Free, no key required. Returns the latest ~100 remote postings."
    fields = [Field("tags", "Only keep jobs tagged with", "text", False, "Comma separated, e.g. python, marketing")]

    def fetch(self, config, ctx, client):
        data = self.get_json(client, "https://remoteok.com/api")
        tags = {t.strip().lower() for t in (config.get("tags") or "").split(",") if t.strip()}
        out = []
        for job in data if isinstance(data, list) else []:
            if not isinstance(job, dict) or "id" not in job or "position" not in job:
                continue
            job_tags = {str(t).lower() for t in job.get("tags") or []}
            if tags and not tags & job_tags:
                continue
            out.append(JobPosting(
                external_id=str(job["id"]),
                title=job.get("position", ""),
                company=job.get("company", ""),
                location=job.get("location") or "Remote",
                url=job.get("url", ""),
                apply_url=job.get("apply_url", ""),
                description=html_to_text(job.get("description")) + "\n" + ", ".join(job_tags),
                remote=True,
                salary_min=parse_money(job.get("salary_min")),
                salary_max=parse_money(job.get("salary_max")),
                posted_at=parse_datetime(job.get("date") or job.get("epoch")),
                board="Remote OK",
            ))
        return out


class TheMuseSource(BaseSource):
    kind = "themuse"
    label = "The Muse"
    covers = "Jobs and internships posted on The Muse"
    description = "Works without a key; add a free key from themuse.com/developers for higher limits."
    fields = [
        Field("api_key", "API key", "password", False),
        Field("category", "Category", "text", False, "e.g. Software Engineering, Data and Analytics, Marketing"),
        Field("level", "Level", "select", False, "", "", ["", "Internship", "Entry Level", "Mid Level", "Senior Level"]),
        LOCATION_FIELD,
        Field("pages", "Pages", "number", False, "20 results per page", "2"),
    ]

    def fetch(self, config, ctx, client):
        out = []
        location = config.get("location") or (ctx.locations[0] if ctx.locations else "")
        for page in range(max(1, min(int(config.get("pages") or 2), 10))):
            params = {"page": page, "descending": "true"}
            for key in ("api_key", "category", "level"):
                if config.get(key):
                    params[key] = config[key]
            if location:
                params["location"] = location
            data = self.get_json(client, "https://www.themuse.com/api/public/jobs", params=params)
            for job in data.get("results", []):
                locations = ", ".join(l.get("name", "") for l in job.get("locations") or [])
                out.append(JobPosting(
                    external_id=str(job.get("id")),
                    title=job.get("name", ""),
                    company=(job.get("company") or {}).get("name", ""),
                    location=locations,
                    url=(job.get("refs") or {}).get("landing_page", ""),
                    description=html_to_text(job.get("contents")),
                    remote=looks_remote(locations),
                    employment_type=", ".join(l.get("name", "") for l in job.get("levels") or []),
                    posted_at=parse_datetime(job.get("publication_date")),
                    board="The Muse",
                ))
            if page + 1 >= int(data.get("page_count") or 0):
                break
        return out

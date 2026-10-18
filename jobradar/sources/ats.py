import httpx

from ..text import annualize, html_to_text, parse_datetime
from .base import BaseSource, Field, JobPosting, SourceContext, SourceError, looks_remote, split_list

COMPANIES_FIELD = Field(
    "companies", "Company board names", "textarea", True,
    "Comma or newline separated board slugs, e.g. the 'stripe' in boards.greenhouse.io/stripe",
)


class _ATSSource(BaseSource):
    def fetch(self, config: dict, ctx: SourceContext, client: httpx.Client) -> list[JobPosting]:
        companies = split_list(config.get("companies"))
        if not companies:
            raise SourceError("Add at least one company board name")
        postings, errors = [], []
        for company in companies:
            try:
                postings.extend(self.fetch_company(company, client))
            except SourceError as exc:
                errors.append(f"{company}: {exc}")
        if errors and not postings:
            raise SourceError("; ".join(errors))
        return postings

    def fetch_company(self, company: str, client: httpx.Client) -> list[JobPosting]:
        raise NotImplementedError


class GreenhouseSource(_ATSSource):
    kind = "greenhouse"
    label = "Greenhouse"
    covers = "Company career pages hosted on Greenhouse"
    description = "Public job boards for thousands of companies (Airbnb, Stripe, Discord...). No login needed."
    fields = [COMPANIES_FIELD]

    def fetch_company(self, company, client):
        data = self.get_json(client, f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs", params={"content": "true"})
        out = []
        for job in data.get("jobs", []):
            location = (job.get("location") or {}).get("name", "")
            description = html_to_text(job.get("content"))
            out.append(JobPosting(
                external_id=f"{company}:{job['id']}",
                title=job.get("title", ""),
                company=job.get("company_name") or company.replace("-", " ").title(),
                location=location,
                url=job.get("absolute_url", ""),
                description=description,
                remote=looks_remote(location),
                posted_at=parse_datetime(job.get("first_published") or job.get("updated_at")),
                board="Greenhouse",
            ))
        return out


class LeverSource(_ATSSource):
    kind = "lever"
    label = "Lever"
    covers = "Company career pages hosted on Lever"
    description = "Public postings for companies using Lever (Netflix, Spotify, Plaid...). No login needed."
    fields = [Field(COMPANIES_FIELD.name, COMPANIES_FIELD.label, "textarea", True, "e.g. the 'plaid' in jobs.lever.co/plaid")]

    def fetch_company(self, company, client):
        data = self.get_json(client, f"https://api.lever.co/v0/postings/{company}", params={"mode": "json"})
        if not isinstance(data, list):
            raise SourceError("Unexpected response")
        out = []
        for job in data:
            categories = job.get("categories") or {}
            location = categories.get("location") or ", ".join(categories.get("allLocations") or [])
            salary = job.get("salaryRange") or {}
            interval = salary.get("interval", "")
            out.append(JobPosting(
                external_id=f"{company}:{job['id']}",
                title=job.get("text", ""),
                company=company.replace("-", " ").title(),
                location=location,
                url=job.get("hostedUrl", ""),
                apply_url=job.get("applyUrl", ""),
                description=job.get("descriptionPlain") or html_to_text(job.get("description")),
                remote=job.get("workplaceType") == "remote" or looks_remote(location),
                salary_min=annualize(salary.get("min"), interval),
                salary_max=annualize(salary.get("max"), interval),
                employment_type=categories.get("commitment", ""),
                posted_at=parse_datetime(job.get("createdAt")),
                board="Lever",
            ))
        return out


class AshbySource(_ATSSource):
    kind = "ashby"
    label = "Ashby"
    covers = "Company career pages hosted on Ashby"
    description = "Public job boards for startups using Ashby (Notion, Ramp, Linear...). No login needed."
    fields = [Field(COMPANIES_FIELD.name, COMPANIES_FIELD.label, "textarea", True, "e.g. the 'ramp' in jobs.ashbyhq.com/ramp")]

    def fetch_company(self, company, client):
        data = self.get_json(
            client, f"https://api.ashbyhq.com/posting-api/job-board/{company}", params={"includeCompensation": "true"}
        )
        out = []
        for job in data.get("jobs", []):
            if job.get("isListed") is False:
                continue
            low = high = None
            for tier in ((job.get("compensation") or {}).get("summaryComponents") or []):
                if tier.get("compensationType") == "Salary":
                    interval = tier.get("interval", "")
                    low = annualize(tier.get("minValue"), interval)
                    high = annualize(tier.get("maxValue"), interval)
            location = job.get("location", "")
            out.append(JobPosting(
                external_id=f"{company}:{job['id']}",
                title=job.get("title", ""),
                company=company.replace("-", " ").title(),
                location=location,
                url=job.get("jobUrl", ""),
                apply_url=job.get("applyUrl", ""),
                description=job.get("descriptionPlain") or html_to_text(job.get("descriptionHtml")),
                remote=bool(job.get("isRemote")) or looks_remote(location),
                salary_min=low,
                salary_max=high,
                employment_type=job.get("employmentType", ""),
                posted_at=parse_datetime(job.get("publishedAt")),
                board="Ashby",
            ))
        return out


class SmartRecruitersSource(_ATSSource):
    kind = "smartrecruiters"
    label = "SmartRecruiters"
    covers = "Company career pages hosted on SmartRecruiters"
    description = "Public postings for enterprises using SmartRecruiters (Visa, Bosch, IKEA...). No login needed."
    fields = [Field(COMPANIES_FIELD.name, COMPANIES_FIELD.label, "textarea", True, "e.g. the 'Visa' in jobs.smartrecruiters.com/Visa")]

    def fetch_company(self, company, client):
        data = self.get_json(client, f"https://api.smartrecruiters.com/v1/companies/{company}/postings", params={"limit": 100})
        out = []
        for job in data.get("content", []):
            loc = job.get("location") or {}
            location = loc.get("fullLocation") or ", ".join(x for x in (loc.get("city"), loc.get("region"), loc.get("country")) if x)
            out.append(JobPosting(
                external_id=f"{company}:{job['id']}",
                title=job.get("name", ""),
                company=(job.get("company") or {}).get("name") or company,
                location=location,
                url=f"https://jobs.smartrecruiters.com/{company}/{job['id']}",
                description=" ".join(
                    filter(None, [(job.get("function") or {}).get("label"), (job.get("industry") or {}).get("label")])
                ),
                remote=bool(loc.get("remote")) or looks_remote(location),
                employment_type=(job.get("typeOfEmployment") or {}).get("label", ""),
                posted_at=parse_datetime(job.get("releasedDate")),
                board="SmartRecruiters",
            ))
        return out

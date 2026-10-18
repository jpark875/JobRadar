from dataclasses import dataclass, field
from datetime import datetime

import httpx

USER_AGENT = "JobRadar/0.1 (+https://github.com/jpark875/JobRadar)"


class SourceError(RuntimeError):
    pass


@dataclass
class Field:
    name: str
    label: str
    type: str = "text"
    required: bool = False
    help: str = ""
    default: str = ""
    options: list[str] = field(default_factory=list)


@dataclass
class JobPosting:
    external_id: str
    title: str
    company: str = ""
    location: str = ""
    url: str = ""
    apply_url: str = ""
    description: str = ""
    remote: bool = False
    salary_min: int | None = None
    salary_max: int | None = None
    employment_type: str = ""
    posted_at: datetime | None = None
    board: str = ""


@dataclass
class SourceContext:
    keywords: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    remote_ok: bool = True
    max_age_days: int = 30


def split_list(value: str | None) -> list[str]:
    if not value:
        return []
    parts = value.replace("\n", ",").split(",")
    return [p.strip() for p in parts if p.strip()]


def looks_remote(*values: str) -> bool:
    joined = " ".join(v for v in values if v).lower()
    return any(word in joined for word in ("remote", "anywhere", "work from home", "wfh", "distributed"))


class BaseSource:
    kind: str = ""
    label: str = ""
    covers: str = ""
    description: str = ""
    fields: list[Field] = []

    def fetch(self, config: dict, ctx: SourceContext, client: httpx.Client) -> list[JobPosting]:
        raise NotImplementedError

    def queries(self, config: dict, ctx: SourceContext, limit: int = 3) -> list[str]:
        explicit = split_list(config.get("keywords"))
        return (explicit or ctx.keywords or [""])[:limit]

    def get_json(self, client: httpx.Client, url: str, **kwargs):
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json", **kwargs.pop("headers", {})}
        try:
            response = client.get(url, headers=headers, timeout=30, **kwargs)
        except httpx.HTTPError as exc:
            raise SourceError(f"Request to {url} failed: {exc}") from exc
        if response.status_code == 404:
            raise SourceError(f"Not found: {url}")
        if response.status_code in (401, 403):
            raise SourceError(f"Access denied by {httpx.URL(url).host}; check your credentials")
        if response.status_code == 429:
            raise SourceError(f"Rate limited by {httpx.URL(url).host}; try a longer sync interval")
        if response.status_code >= 400:
            raise SourceError(f"{httpx.URL(url).host} returned HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise SourceError(f"{httpx.URL(url).host} did not return JSON") from exc

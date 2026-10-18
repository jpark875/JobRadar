import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable

from .skills import extract_skills
from .text import normalize_key, tokenize

LEVELS = {"intern": 0, "entry": 1, "mid": 2, "senior": 3, "lead": 4}
LEVEL_PATTERNS = [
    (0, re.compile(r"\b(intern|internship|co-op|coop|summer analyst|summer associate)\b")),
    (4, re.compile(r"\b(principal|director|head of|vp|vice president|chief|distinguished)\b")),
    (3, re.compile(r"\b(senior|sr\.?|staff|lead|architect)\b")),
    (1, re.compile(r"\b(junior|jr\.?|entry[- ]level|new grad|graduate|early career|associate|trainee|apprentice|i)\b")),
    (2, re.compile(r"\b(mid[- ]level|ii|iii|intermediate)\b")),
]
_REQ_YEARS_RE = re.compile(r"(\d{1,2})\s*\+?\s*(?:-\s*\d{1,2}\s*)?(?:years|yrs)(?:\s+of)?(?:\s+\w+){0,3}\s+experience", re.I)
TITLE_SYNONYMS = {
    "developer": "engineer", "programmer": "engineer", "dev": "engineer", "swe": "engineer", "sde": "engineer",
    "backend": "back-end", "frontend": "front-end", "fullstack": "full-stack", "ml": "machine", "ai": "machine",
    "analytic": "analyst", "scientist": "science",
}
WEIGHTS = {"skills": 0.35, "title": 0.25, "text": 0.2, "level": 0.1, "location": 0.05, "recency": 0.05}


@dataclass
class Preferences:
    resume_text: str = ""
    resume_skills: list[str] = field(default_factory=list)
    years_experience: float = 0.0
    experience_level: str = "entry"
    target_titles: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    remote_ok: bool = True
    remote_only: bool = False
    min_salary: int = 0
    must_have_keywords: list[str] = field(default_factory=list)
    exclude_keywords: list[str] = field(default_factory=list)
    excluded_companies: list[str] = field(default_factory=list)
    max_age_days: int = 30

    @classmethod
    def from_profile(cls, profile) -> "Preferences":
        return cls(
            resume_text=profile.resume_text or "",
            resume_skills=list(profile.resume_skills or []),
            years_experience=profile.years_experience or 0.0,
            experience_level=profile.experience_level or "entry",
            target_titles=list(profile.target_titles or []),
            locations=list(profile.locations or []),
            remote_ok=profile.remote_ok,
            remote_only=profile.remote_only,
            min_salary=profile.min_salary or 0,
            must_have_keywords=list(profile.must_have_keywords or []),
            exclude_keywords=list(profile.exclude_keywords or []),
            excluded_companies=list(profile.excluded_companies or []),
            max_age_days=profile.max_age_days or 0,
        )


@dataclass
class JobView:
    title: str
    company: str = ""
    location: str = ""
    description: str = ""
    remote: bool = False
    salary_min: int | None = None
    salary_max: int | None = None
    posted_at: datetime | None = None
    first_seen_at: datetime | None = None

    @classmethod
    def from_job(cls, job) -> "JobView":
        return cls(
            title=job.title, company=job.company, location=job.location, description=job.description,
            remote=job.remote, salary_min=job.salary_min, salary_max=job.salary_max,
            posted_at=job.posted_at, first_seen_at=job.first_seen_at,
        )

    @property
    def full_text(self) -> str:
        return f"{self.title}\n{self.title}\n{self.description}"


@dataclass
class MatchResult:
    score: float
    filtered_reason: str | None
    details: dict


def detect_level(title: str, description: str = "") -> int | None:
    lowered = title.lower()
    for level, pattern in LEVEL_PATTERNS:
        if pattern.search(lowered):
            return level
    years = [int(y) for y in _REQ_YEARS_RE.findall(description or "") if int(y) < 25]
    if years:
        need = min(years)
        return 1 if need <= 2 else 2 if need <= 4 else 3
    return None


def required_years(description: str) -> int | None:
    years = [int(y) for y in _REQ_YEARS_RE.findall(description or "") if int(y) < 25]
    return min(years) if years else None


def _title_tokens(title: str) -> set[str]:
    text = re.sub(r"\b(back|front|full)\s+(end|stack)\b", r"\1-\2", title.lower())
    return {TITLE_SYNONYMS.get(t, t) for t in tokenize(text)}


def _contains_phrase(text: str, phrase: str) -> bool:
    phrase = normalize_key(phrase)
    return bool(phrase) and f" {phrase} " in f" {text} "


class Matcher:
    def __init__(self, prefs: Preferences, corpus: Iterable[str] = (), now: datetime | None = None):
        self.prefs = prefs
        self.now = now or datetime.now(timezone.utc).replace(tzinfo=None)
        self.resume_skills = set(prefs.resume_skills) or set(extract_skills(prefs.resume_text))
        self.doc_freq: Counter = Counter()
        self.doc_count = 0
        for doc in corpus:
            self.doc_freq.update(set(tokenize(doc)))
            self.doc_count += 1
        resume_tokens = tokenize(prefs.resume_text)
        self.doc_freq.update(set(resume_tokens))
        self.doc_count += 1
        self.resume_vector = self._vectorize(resume_tokens)
        self.target_titles = [_title_tokens(t) for t in prefs.target_titles if tokenize(t)]
        self.user_level = LEVELS.get(prefs.experience_level, 1)

    def _idf(self, token: str) -> float:
        return math.log((1 + self.doc_count) / (1 + self.doc_freq.get(token, 0))) + 1

    def _vectorize(self, tokens: list[str]) -> dict[str, float]:
        counts = Counter(tokens)
        vector = {t: (1 + math.log(c)) * self._idf(t) for t, c in counts.items()}
        norm = math.sqrt(sum(v * v for v in vector.values())) or 1.0
        return {t: v / norm for t, v in vector.items()}

    def _cosine(self, tokens: list[str]) -> float:
        if not self.resume_vector or not tokens:
            return 0.0
        vector = self._vectorize(tokens)
        return sum(w * self.resume_vector.get(t, 0.0) for t, w in vector.items())

    def hard_filter(self, job: JobView) -> str | None:
        p = self.prefs
        haystack = normalize_key(f"{job.title} {job.description}")
        title_key = normalize_key(job.title)
        company_key = normalize_key(job.company)
        for company in p.excluded_companies:
            if normalize_key(company) and normalize_key(company) == company_key:
                return f"Excluded company: {job.company}"
        for keyword in p.exclude_keywords:
            if _contains_phrase(title_key, keyword):
                return f"Title contains excluded keyword '{keyword}'"
        if p.must_have_keywords and not any(_contains_phrase(haystack, k) for k in p.must_have_keywords):
            return "Missing all must-have keywords"
        if p.remote_only and not job.remote:
            return "Not remote"
        seen = job.posted_at or job.first_seen_at
        if p.max_age_days and seen and (self.now - seen).days > p.max_age_days:
            return f"Older than {p.max_age_days} days"
        return None

    def _title_score(self, job: JobView) -> tuple[float | None, str | None]:
        if not self.target_titles:
            return None, None
        job_tokens = _title_tokens(job.title)
        if not job_tokens:
            return 0.0, None
        best, best_title = 0.0, None
        for target, raw in zip(self.target_titles, self.prefs.target_titles):
            overlap = len(target & job_tokens)
            score = overlap / len(target)
            if overlap == len(target):
                score = 1.0
            if score > best:
                best, best_title = score, raw
        return best, best_title

    def _level_score(self, job: JobView) -> tuple[float | None, str | None]:
        level = detect_level(job.title, job.description)
        reason = None
        if level is None:
            score = None
        else:
            diff = level - self.user_level
            if self.user_level == 0:
                score = 1.0 if level == 0 else 0.55 if level == 1 else 0.0
            else:
                score = {0: 1.0, -1: 0.7, 1: 0.45}.get(diff, 0.0 if diff > 0 else 0.3)
            names = {v: k for k, v in LEVELS.items()}
            if score < 0.5:
                reason = f"Looks like a {names[level]}-level role (you're {self.prefs.experience_level})"
        need = required_years(job.description)
        if need is not None and need > self.prefs.years_experience + 2:
            penalty = 0.5 if need > self.prefs.years_experience + 4 else 0.75
            score = (score if score is not None else 0.8) * penalty
            reason = f"Asks for {need}+ years (you have ~{self.prefs.years_experience:g})"
        return score, reason

    def _location_score(self, job: JobView) -> tuple[float, str | None]:
        p = self.prefs
        if job.remote:
            return (1.0, "Remote") if p.remote_ok else (0.3, "Remote role")
        if not p.locations:
            return 0.7, None
        loc = normalize_key(job.location)
        for wanted in p.locations:
            wanted_key = normalize_key(wanted)
            if wanted_key and (wanted_key in loc or wanted_key.split(" ")[0] in loc.split(" ")):
                return 1.0, f"In {wanted}"
        return 0.15, None

    def _recency_score(self, job: JobView) -> tuple[float, int | None]:
        stamp = job.posted_at or job.first_seen_at
        if not stamp:
            return 0.5, None
        days = max(0, (self.now - stamp).days)
        for limit, score in ((1, 1.0), (3, 0.85), (7, 0.65), (14, 0.45), (30, 0.25)):
            if days <= limit:
                return score, days
        return 0.1, days

    def score(self, job: JobView) -> MatchResult:
        filtered = self.hard_filter(job)
        text = job.full_text
        job_skills = extract_skills(text)
        matched = [s for s in job_skills if s in self.resume_skills]
        missing = [s for s in job_skills if s not in self.resume_skills]
        components: dict[str, float | None] = {}
        reasons: list[str] = []
        warnings: list[str] = []

        if job_skills and self.resume_skills:
            denominator = max(3, min(len(job_skills), 10))
            components["skills"] = min(1.0, len(matched) / denominator)
            if matched:
                reasons.append(f"Matches {len(matched)} of {len(job_skills)} listed skills")
        else:
            components["skills"] = None

        title_score, title_hit = self._title_score(job)
        components["title"] = title_score
        if title_hit and title_score and title_score >= 0.6:
            reasons.append(f"Title close to '{title_hit}'")

        if self.resume_vector and len(job.description) > 80:
            components["text"] = min(1.0, self._cosine(tokenize(text)) * 2.5)
        else:
            components["text"] = None

        level_score, level_reason = self._level_score(job)
        components["level"] = level_score
        if level_reason:
            warnings.append(level_reason)

        location_score, location_reason = self._location_score(job)
        components["location"] = location_score
        if location_reason:
            reasons.append(location_reason)

        recency_score, days = self._recency_score(job)
        components["recency"] = recency_score
        if days is not None and days <= 2:
            reasons.append("Posted today" if days == 0 else f"Posted {days} day{'s' if days > 1 else ''} ago")

        available = {k: v for k, v in components.items() if v is not None}
        weight_total = sum(WEIGHTS[k] for k in available)
        base = sum(WEIGHTS[k] * v for k, v in available.items()) / weight_total if weight_total else 0.0

        if not any(components[k] is not None for k in ("skills", "title", "text")):
            base *= 0.6
        if level_score is not None:
            base *= 0.55 + 0.45 * level_score

        salary_top = job.salary_max or job.salary_min
        if self.prefs.min_salary and salary_top:
            if salary_top < self.prefs.min_salary:
                base *= 0.7
                warnings.append(f"Pays up to ${salary_top:,}, below your ${self.prefs.min_salary:,} minimum")
            else:
                base = min(1.0, base + 0.03)
                reasons.append("Meets your salary floor")

        details = {
            "components": {k: (round(v, 3) if v is not None else None) for k, v in components.items()},
            "matched_skills": matched,
            "missing_skills": missing,
            "reasons": reasons,
            "warnings": warnings,
            "filtered_reason": filtered,
        }
        return MatchResult(score=round(base * 100, 1), filtered_reason=filtered, details=details)

import re
import string
from datetime import date

from .models import Answer, Job, Profile
from .text import tokenize


class _SafeDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def _join(items: list[str]) -> str:
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def template_context(profile: Profile, job: Job | None = None) -> dict:
    details = (job.score_details or {}) if job else {}
    matched = details.get("matched_skills") or []
    top = matched[:3] or (profile.resume_skills or [])[:3]
    first, _, last = (profile.full_name or "").partition(" ")
    return {
        "full_name": profile.full_name,
        "first_name": first,
        "last_name": last,
        "email": profile.email,
        "phone": profile.phone,
        "city": profile.city,
        "linkedin": profile.linkedin_url,
        "github": profile.github_url,
        "portfolio": profile.portfolio_url,
        "school": profile.school,
        "degree": profile.degree,
        "graduation_date": profile.graduation_date,
        "years_experience": f"{profile.years_experience:g}",
        "highlight": profile.resume_highlight,
        "top_skills": _join(top) or "my core skills",
        "matched_skills": _join(matched),
        "company": (job.company if job else "") or "your company",
        "title": job.title if job else "this",
        "location": job.location if job else "",
        "board": job.board if job else "",
        "today": date.today().strftime("%B %d, %Y"),
    }


def render(text: str, profile: Profile, job: Job | None = None) -> str:
    formatter = string.Formatter()
    try:
        rendered = formatter.vformat(text or "", (), _SafeDict(template_context(profile, job)))
    except (ValueError, IndexError):
        return text
    return re.sub(r"\n{3,}", "\n\n", rendered).strip()


def placeholders() -> list[str]:
    return sorted(template_context(Profile(full_name="", years_experience=0, resume_skills=[])).keys())


def best_answer(question: str, answers: list[Answer]) -> Answer | None:
    query = set(tokenize(question))
    if not query:
        return None
    best, best_score = None, 0.0
    for answer in answers:
        keys = set(tokenize(answer.question)) | set(tokenize(answer.keywords.replace(",", " ")))
        if not keys:
            continue
        overlap = len(query & keys)
        score = overlap / (len(query) ** 0.5 * len(keys) ** 0.5)
        if score > best_score:
            best, best_score = answer, score
    return best if best_score >= 0.25 else None

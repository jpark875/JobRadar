import re
from collections import Counter
from dataclasses import dataclass, field

from .skills import extract_skills
from .text import STOPWORDS, tokenize

ACTION_VERBS = set("""
accelerated achieved administered advised advocated analyzed architected arranged assembled assessed audited
authored automated balanced boosted budgeted built calculated championed clarified coached collaborated compiled
completed composed computed conceived conducted configured consolidated constructed consulted contributed controlled
converted coordinated created cultivated customized cut debugged decreased defined delivered deployed designed
detected developed devised diagnosed directed discovered doubled drafted drove edited educated eliminated enabled
engineered enhanced established evaluated executed expanded expedited facilitated finalized forecasted formulated
founded generated grew guided halved handled headed hired identified implemented improved increased influenced
initiated innovated inspected installed instituted instructed integrated interviewed introduced invented investigated
launched led leveraged maintained managed mapped maximized measured mentored merged migrated minimized modeled
modernized monitored motivated negotiated operated optimized orchestrated organized originated outperformed overhauled
oversaw partnered performed piloted pioneered planned prepared presented prioritized processed produced programmed
promoted proposed prototyped published raised ran rebuilt recommended reconciled recruited redesigned reduced
refactored refined reorganized replaced reported represented researched resolved restructured revamped reviewed
revised saved scaled scheduled secured shipped simplified solved spearheaded standardized started streamlined
strengthened structured supervised surpassed synthesized tested tracked trained transformed translated tripled
troubleshot unified upgraded validated visualized won wrote
""".split())
WEAK_OPENERS = [
    "responsible for", "worked on", "helped", "assisted", "duties included", "tasked with", "participated in",
    "involved in", "in charge of", "was part of", "handled various", "familiar with",
]
BUZZWORDS = [
    "team player", "hard worker", "hard-working", "hardworking", "detail-oriented", "detail oriented", "go-getter",
    "self-starter", "self starter", "synergy", "think outside the box", "results-driven", "results driven",
    "dynamic", "passionate", "motivated individual", "go-to person", "proven track record", "best of breed",
    "rockstar", "ninja", "guru", "out of the box", "fast learner", "quick learner", "people person",
]
SECTIONS = {
    "experience": r"(work |professional |relevant )?experience|employment( history)?|work history",
    "education": r"education|academic background",
    "skills": r"(technical |core )?skills|technologies|tools|competencies",
    "projects": r"(personal |academic |selected )?projects",
    "summary": r"summary|profile|objective|about me",
}
BULLET_RE = re.compile(r"^\s*([•●▪◦‣∙·\-–*➢►■□✓]|\(?\d{1,2}[.)])\s*")
QUANT_RE = re.compile(r"\d|%|\$|\bpercent\b|\b(one|two|three|four|five|six|seven|eight|nine|ten|dozens?|hundreds?|thousands?|millions?)\b", re.I)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PHONE_RE = re.compile(r"(\+?\d{1,2}[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")
LINK_RE = re.compile(r"linkedin\.com/in/|github\.com/|https?://", re.I)
LOCATION_RE = re.compile(r"\b[A-Z][a-zA-Z .]+,\s*(?:[A-Z]{2}|[A-Z][a-z]+)\b")
PRONOUN_RE = re.compile(r"\b(I|me|my|myself|we|our)\b")
DATE_STYLES = {
    "Mon YYYY": re.compile(r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(?:19|20)\d{2}\b", re.I),
    "MM/YYYY": re.compile(r"\b\d{1,2}/(?:19|20)\d{2}\b"),
}


@dataclass
class Check:
    key: str
    title: str
    weight: int
    score: float
    summary: str
    tips: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        return "pass" if self.score >= 0.85 else "warn" if self.score >= 0.5 else "fail"

    @property
    def points(self) -> float:
        return round(self.weight * self.score, 1)


@dataclass
class Report:
    score: int
    grade: str
    headline: str
    checks: list[Check]
    stats: dict
    skills: list[str]
    demand: list[dict] = field(default_factory=list)

    @property
    def fixes(self) -> list[Check]:
        return sorted((c for c in self.checks if c.status != "pass"), key=lambda c: c.weight * (1 - c.score), reverse=True)


@dataclass
class JobFit:
    score: int
    matched_skills: list[str]
    missing_skills: list[str]
    missing_keywords: list[str]
    title_found: bool
    tips: list[str]


def _grade(score: float) -> str:
    for cutoff, letter in ((93, "A"), (90, "A-"), (87, "B+"), (83, "B"), (80, "B-"), (77, "C+"), (73, "C"), (70, "C-"), (60, "D")):
        if score >= cutoff:
            return letter
    return "F"


def _clip(text: str, n: int = 110) -> str:
    text = text.strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def split_lines(text: str) -> list[str]:
    return [line.rstrip() for line in (text or "").replace("\r", "").splitlines() if line.strip()]


def find_sections(lines: list[str]) -> dict[str, int]:
    found = {}
    for index, line in enumerate(lines):
        stripped = line.strip().strip(":").strip()
        if len(stripped.split()) > 4 or len(stripped) > 40:
            continue
        for key, pattern in SECTIONS.items():
            if key not in found and re.fullmatch(pattern, stripped, re.I):
                found[key] = index
    return found


def find_bullets(lines: list[str], sections: dict[str, int]) -> list[str]:
    marked = [BULLET_RE.sub("", line).strip() for line in lines if BULLET_RE.match(line)]
    marked = [b for b in marked if len(b.split()) >= 3]
    if len(marked) >= 3:
        return marked
    starts = sorted(sections.items(), key=lambda kv: kv[1])
    ranges = []
    for i, (key, start) in enumerate(starts):
        end = starts[i + 1][1] if i + 1 < len(starts) else len(lines)
        if key in ("experience", "projects"):
            ranges.append((start + 1, end))
    if not ranges:
        ranges = [(0, len(lines))]
    return [
        lines[i].strip()
        for start, end in ranges
        for i in range(start, end)
        if len(lines[i].split()) >= 6 and not re.search(r"(19|20)\d{2}\s*[-–—]", lines[i])
    ]


def _is_action(word: str) -> bool:
    return word in ACTION_VERBS or (len(word) >= 5 and word.endswith("ed") and word not in {"needed", "required", "skilled", "detailed", "advanced", "experienced"})


def _first_word(bullet: str) -> str:
    match = re.match(r"[A-Za-z][A-Za-z-]*", bullet)
    return match.group(0).lower() if match else ""


def grade_resume(text: str, years_experience: float = 0.0, demand: list[dict] | None = None) -> Report:
    lines = split_lines(text)
    words = re.findall(r"\b\w[\w'+#.-]*\b", text or "")
    word_count = len(words)
    sections = find_sections(lines)
    bullets = find_bullets(lines, sections)
    skills = extract_skills(text)
    checks: list[Check] = []

    head = "\n".join(lines[:8])
    contact = {
        "email": bool(EMAIL_RE.search(text or "")),
        "phone": bool(PHONE_RE.search(head)) or bool(PHONE_RE.search(text or "")),
        "LinkedIn or portfolio link": bool(LINK_RE.search(text or "")),
        "city and state": bool(LOCATION_RE.search(head)),
    }
    missing_contact = [k for k, v in contact.items() if not v]
    checks.append(Check(
        "contact", "Contact details", 8, sum(contact.values()) / len(contact),
        "All contact details found." if not missing_contact else f"Missing: {', '.join(missing_contact)}.",
        [f"Add your {item} to the header." for item in missing_contact],
    ))

    required = ["experience", "education", "skills"]
    has_experience = "experience" in sections or "projects" in sections
    present = {"experience": has_experience, "education": "education" in sections, "skills": "skills" in sections}
    missing_sections = [k for k in required if not present[k]]
    checks.append(Check(
        "sections", "Standard sections", 10, sum(present.values()) / len(present),
        "Experience, Education and Skills sections all found." if not missing_sections
        else f"Couldn't find a clearly labeled {', '.join(missing_sections)} section.",
        ["Use plain section headings like \"Experience\", \"Education\" and \"Skills\" so applicant tracking systems file your details correctly."]
        if missing_sections else [],
    ))

    low, high = (300, 750) if years_experience < 5 else (450, 1100)
    if word_count < 200:
        length_score = word_count / 200 * 0.5
    elif word_count < low:
        length_score = 0.75
    elif word_count <= high:
        length_score = 1.0
    else:
        length_score = max(0.4, 1 - (word_count - high) / high)
    length_tips = []
    if word_count < low:
        length_tips.append("Add more detail on what you built and the results; aim for 3-5 bullets per role.")
    elif word_count > high:
        length_tips.append("Trim older or less relevant bullets. Recruiters skim; one page is ideal under 5 years of experience.")
    checks.append(Check(
        "length", "Length", 8, length_score,
        f"{word_count} words (target {low}-{high} for your experience level).", length_tips,
    ))

    if bullets:
        quantified = [b for b in bullets if QUANT_RE.search(b)]
        ratio = len(quantified) / len(bullets)
        checks.append(Check(
            "impact", "Quantified impact", 18, min(1.0, ratio / 0.5),
            f"{len(quantified)} of {len(bullets)} bullets include a number, percentage or dollar amount ({ratio:.0%}).",
            ["Aim for at least half your bullets to show measurable results: users, time saved, revenue, % improvement, scale."]
            if ratio < 0.5 else [],
            [_clip(b) for b in bullets if b not in quantified][:4],
        ))

        openers = [_first_word(b) for b in bullets]
        strong = [b for b, w in zip(bullets, openers) if _is_action(w)]
        weak = [b for b in bullets if any(b.lower().startswith(p) for p in WEAK_OPENERS)]
        ratio = len(strong) / len(bullets)
        score = min(1.0, ratio / 0.75) - min(0.3, len(weak) * 0.1)
        tips = []
        if weak:
            tips.append("Replace phrases like \"responsible for\" or \"helped with\" with what you actually did: built, led, reduced, launched.")
        if ratio < 0.75:
            tips.append("Start every bullet with a strong past-tense action verb.")
        checks.append(Check(
            "verbs", "Strong action verbs", 14, max(0.0, score),
            f"{len(strong)} of {len(bullets)} bullets start with a strong action verb"
            + (f"; {len(weak)} start with a weak phrase." if weak else "."),
            tips,
            [_clip(b) for b in (weak or [b for b, w in zip(bullets, openers) if not _is_action(w)])][:4],
        ))

        lengths = [len(b.split()) for b in bullets]
        long_bullets = [b for b, n in zip(bullets, lengths) if n > 32]
        short_bullets = [b for b, n in zip(bullets, lengths) if n < 6]
        ok = len(bullets) - len(long_bullets) - len(short_bullets)
        checks.append(Check(
            "bullets", "Bullet length", 6, ok / len(bullets),
            f"{ok} of {len(bullets)} bullets are a readable length (6-32 words).",
            (["Split long bullets in two, or cut the setup and lead with the result."] if long_bullets else [])
            + (["Expand very short bullets with what you did and the outcome."] if short_bullets else []),
            [_clip(b) for b in long_bullets + short_bullets][:3],
        ))

        counts = Counter(w for w in openers if w)
        repeated = [f"\"{w.capitalize()}\" x{n}" for w, n in counts.most_common() if n >= 3]
        checks.append(Check(
            "variety", "Verb variety", 4, 1.0 if not repeated else max(0.3, 1 - 0.25 * len(repeated)),
            "Good variety in how bullets begin." if not repeated else f"Repeated openers: {', '.join(repeated)}.",
            ["Vary your verbs: e.g. built, designed, launched, automated, scaled."] if repeated else [],
        ))
    else:
        checks.append(Check(
            "impact", "Bullet points", 38, 0.2,
            "Couldn't find bullet points under your experience.",
            ["Describe each role with 3-5 bullet points that start with an action verb and include a measurable result."],
        ))

    pronouns = [m.group(0) for m in PRONOUN_RE.finditer(text or "")]
    checks.append(Check(
        "pronouns", "No first-person pronouns", 5, 1.0 if not pronouns else max(0.2, 1 - len(pronouns) * 0.15),
        "No personal pronouns found." if not pronouns else f"Found {len(pronouns)} personal pronoun(s) like \"{pronouns[0]}\".",
        ["Drop \"I\", \"my\" and \"we\"; resume bullets are written in implied first person."] if pronouns else [],
        [_clip(line) for line in lines if PRONOUN_RE.search(line)][:3],
    ))

    lowered = (text or "").lower()
    cliches = [b for b in BUZZWORDS if re.search(r"\b" + re.escape(b) + r"\b", lowered)]
    checks.append(Check(
        "buzzwords", "No filler buzzwords", 5, 1.0 if not cliches else max(0.3, 1 - len(cliches) * 0.2),
        "No clichés found." if not cliches else f"Clichés: {', '.join(cliches)}.",
        ["Show these qualities through results instead of claiming them."] if cliches else [],
    ))

    if demand:
        top = demand[:15]
        have = [d for d in top if d["skill"] in skills]
        missing = [d["skill"] for d in top if d["skill"] not in skills]
        weight_total = sum(d["count"] for d in top) or 1
        coverage = sum(d["count"] for d in have) / weight_total
        checks.append(Check(
            "keywords", "Matches in-demand skills", 18, min(1.0, coverage / 0.6),
            f"Your resume mentions {len(have)} of the {len(top)} skills that come up most in jobs you're seeing.",
            [f"If you have experience with {', '.join(missing[:5])}, name it explicitly in your skills or bullets."] if missing else [],
        ))
    else:
        checks.append(Check(
            "keywords", "Hard skills", 18, min(1.0, len(skills) / 10),
            f"Found {len(skills)} recognizable hard skills.",
            ["List specific tools, languages and methods (e.g. SQL, Excel, Python, Figma) in a Skills section."] if len(skills) < 10 else [],
        ))

    odd = sum(1 for c in text or "" if ord(c) > 127 and c not in "•●▪◦‣∙·–—’‘“”…é✓➢►■□")
    odd_ratio = odd / max(1, len(text or ""))
    styles = [name for name, pattern in DATE_STYLES.items() if pattern.search(text or "")]
    ats_score, ats_tips = 1.0, []
    if odd_ratio > 0.01:
        ats_score -= 0.4
        ats_tips.append("Some characters didn't extract cleanly. Avoid icons, text boxes and unusual fonts.")
    if len(styles) > 1:
        ats_score -= 0.25
        ats_tips.append("Use one date format throughout (e.g. \"Jan 2024 - Present\").")
    if word_count and len(lines) / max(1, word_count) > 0.45:
        ats_score -= 0.25
        ats_tips.append("Text came out as many short fragments, which usually means a multi-column or table layout. A single-column layout parses most reliably.")
    checks.append(Check(
        "ats", "Readable by applicant tracking systems", 4, max(0.0, ats_score),
        "Text extracted cleanly." if not ats_tips else "Some formatting may trip up applicant tracking systems.", ats_tips,
    ))

    total_weight = sum(c.weight for c in checks)
    score = round(sum(c.weight * c.score for c in checks) / total_weight * 100)
    grade = _grade(score)
    fixes = sorted((c for c in checks if c.status != "pass"), key=lambda c: c.weight * (1 - c.score), reverse=True)
    if not fixes:
        headline = "Strong resume. Tailor keywords to each job and you're set."
    else:
        headline = "Biggest improvement: " + fixes[0].title.lower() + "."
    stats = {"words": word_count, "bullets": len(bullets), "skills": len(skills), "sections": sorted(sections)}
    return Report(score=score, grade=grade, headline=headline, checks=checks, stats=stats, skills=skills, demand=demand or [])


def _keywords(text: str, limit: int = 25) -> list[str]:
    counts = Counter(t for t in tokenize(text) if len(t) > 3 and t not in STOPWORDS and not t.isdigit())
    return [w for w, n in counts.most_common(limit * 2) if n >= 2][:limit]


def job_fit(resume_text: str, job_title: str, job_text: str) -> JobFit:
    resume_skills = set(extract_skills(resume_text))
    job_skills = extract_skills(f"{job_title}\n{job_text}")
    matched = [s for s in job_skills if s in resume_skills]
    missing = [s for s in job_skills if s not in resume_skills]
    resume_tokens = set(tokenize(resume_text))
    skill_words = {w for s in job_skills for w in tokenize(s)}
    keywords = [k for k in _keywords(job_text) if k not in skill_words]
    missing_keywords = [k for k in keywords if k not in resume_tokens][:12]
    keyword_cov = 1 - len(missing_keywords) / len(keywords) if keywords else 1.0
    skill_cov = len(matched) / len(job_skills) if job_skills else keyword_cov
    title_tokens = [t for t in tokenize(job_title) if t not in {"senior", "junior", "sr", "jr", "ii", "iii", "i"}]
    title_found = bool(title_tokens) and all(t in resume_tokens for t in title_tokens)
    score = round((0.6 * skill_cov + 0.25 * keyword_cov + 0.15 * title_found) * 100)
    tips = []
    if missing:
        tips.append(f"The posting asks for {', '.join(missing[:6])}. Where you have that experience, say so using the posting's wording.")
    if missing_keywords:
        tips.append(f"Terms the posting repeats that your resume doesn't use: {', '.join(missing_keywords[:8])}.")
    if not title_found and title_tokens:
        tips.append(f"Consider echoing the title \"{job_title}\" in your summary or a relevant role.")
    if not tips:
        tips.append("Your resume already covers this posting's main requirements.")
    return JobFit(score, matched, missing, missing_keywords, title_found, tips)

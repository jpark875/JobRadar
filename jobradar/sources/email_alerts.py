import email
import hashlib
import imaplib
import re
from datetime import datetime, timedelta, timezone
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime
from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

from ..text import clean, parse_money
from .base import BaseSource, Field, JobPosting, SourceContext, SourceError, looks_remote

BOARDS = {
    "Indeed": {
        "senders": ["indeed.com", "indeedemail.com"],
        "link": re.compile(r"indeed\.[a-z.]+/(?:rc/clk|viewjob|m/viewjob|pagead/clk|applystart|cmp/.+/jobs)|[?&]jk=", re.I),
        "id": [re.compile(r"[?&](?:jk|vjk)=([a-f0-9]+)", re.I)],
    },
    "LinkedIn": {
        "senders": ["linkedin.com"],
        "link": re.compile(r"linkedin\.com/(?:comm/)?jobs/view/", re.I),
        "id": [re.compile(r"jobs/view/(?:[^/?]*-)?(\d+)", re.I)],
    },
    "Handshake": {
        "senders": ["joinhandshake.com", "handshake.com"],
        "link": re.compile(r"joinhandshake\.com/(?:.*/)?(?:jobs|job-search|postings)/|handshake\.com/.*jobs?", re.I),
        "id": [re.compile(r"/(?:jobs|job-search|postings)/(\d+)", re.I)],
    },
    "Glassdoor": {
        "senders": ["glassdoor.com"],
        "link": re.compile(r"glassdoor\.[a-z.]+/(?:partner/joblisting\.htm|job-listing/|job/)", re.I),
        "id": [re.compile(r"[?&](?:jobListingId|jl)=(\d+)", re.I), re.compile(r"JV_[A-Z0-9_]*?(\d{6,})", re.I)],
    },
    "ZipRecruiter": {
        "senders": ["ziprecruiter.com"],
        "link": re.compile(r"ziprecruiter\.com/(?:k/|c/|jobs/|ekm/|job/)", re.I),
        "id": [re.compile(r"[?&](?:jid|lvk)=([\w-]+)", re.I)],
    },
    "Wellfound": {
        "senders": ["wellfound.com", "angel.co"],
        "link": re.compile(r"(?:wellfound\.com|angel\.co)/(?:jobs|l|company/.+/jobs)/", re.I),
        "id": [re.compile(r"/jobs/(\d+)", re.I)],
    },
    "Monster": {
        "senders": ["monster.com"],
        "link": re.compile(r"monster\.com/job-openings/", re.I),
        "id": [re.compile(r"job-openings/[^/]*?--([\w-]+)", re.I)],
    },
}

NON_TITLE = re.compile(
    r"^(apply|easy apply|apply now|view( all)?( jobs?| more| details| job)?|see (all|more)( jobs)?|save|unsubscribe|"
    r"manage|settings|learn more|search|new|promoted|actively recruiting|be an early applicant|sign in|"
    r"show more|more jobs|update|edit|help|privacy|terms|here|click here|jobs?|.*\balerts?\b.*)$",
    re.I,
)
SALARY_LINE = re.compile(r"\$\s?\d|\d+\s?k\b|/\s?(hr|hour|yr|year)\b|per (hour|year)", re.I)
NOISE_LINE = re.compile(
    r"^(easy apply|promoted|actively recruiting|new|just posted|responsive employer|urgently hiring|"
    r"\d+ (applicants?|connections?|alumni).*|be an early applicant|apply now|view job|\d+[hdw] ago|"
    r"posted .*|today|\d+ days? ago|full[- ]time|part[- ]time|internship|contract|temporary|on[- ]site|hybrid)$",
    re.I,
)


def board_for_sender(sender: str) -> str | None:
    domain = parseaddr(sender)[1].split("@")[-1].lower()
    for board, spec in BOARDS.items():
        if any(domain == s or domain.endswith("." + s) for s in spec["senders"]):
            return board
    return None


def _job_id(board: str, href: str) -> str | None:
    for pattern in BOARDS[board]["id"]:
        match = pattern.search(href)
        if match:
            return match.group(1)
    query = parse_qs(urlparse(href).query)
    for key in ("url", "u", "redirect", "dest", "target"):
        for value in query.get(key, []):
            for pattern in BOARDS[board]["id"]:
                match = pattern.search(value)
                if match:
                    return match.group(1)
    return None


def _is_title(text: str) -> bool:
    return 4 <= len(text) <= 140 and not NON_TITLE.match(text) and not SALARY_LINE.match(text) and any(c.isalpha() for c in text)


def _context_lines(anchor: Tag, title: str) -> list[str]:
    node = anchor
    for _ in range(6):
        parent = node.parent
        if parent is None or parent.name in ("body", "html", "[document]"):
            break
        text = parent.get_text("\n")
        lines = [clean(l) for l in text.splitlines() if clean(l)]
        if len(" ".join(lines)) > 600:
            break
        node = parent
        if len(lines) >= 3:
            break
    lines = [clean(l) for l in node.get_text("\n").splitlines() if clean(l)]
    if title in lines:
        lines = lines[lines.index(title) + 1:]
    return lines


def _split_company_location(lines: list[str]) -> tuple[str, str, list[str]]:
    useful = []
    for line in lines:
        if NOISE_LINE.match(line) or NON_TITLE.match(line):
            continue
        useful.append(line)
    company = location = ""
    salary_lines = [l for l in useful if SALARY_LINE.search(l)]
    rest = [l for l in useful if l not in salary_lines]
    if rest:
        first = rest[0]
        for separator in (" · ", " • ", " | ", " - ", " – "):
            if separator in first:
                company, location = [clean(x) for x in first.split(separator, 1)]
                break
        else:
            company = first
            if len(rest) > 1:
                location = rest[1].split(" · ")[0]
    return company, location, salary_lines


def _from_board_domain(board: str, href: str) -> bool:
    host = (urlparse(href).hostname or "").lower()
    return any(host == s or host.endswith("." + s) for s in BOARDS[board]["senders"])


def parse_alert_html(html: str, board: str, received: datetime | None = None) -> list[JobPosting]:
    postings = _parse_anchors(html, board, received, strict=True)
    return postings or _parse_anchors(html, board, received, strict=False)


def _parse_anchors(html: str, board: str, received: datetime | None, strict: bool) -> list[JobPosting]:
    soup = BeautifulSoup(html, "html.parser")
    spec = BOARDS[board]
    postings: dict[str, JobPosting] = {}
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if not href.startswith("http"):
            continue
        title = clean(anchor.get_text(" "))
        if not _is_title(title):
            continue
        if strict and not spec["link"].search(href) and not _job_id(board, href):
            continue
        if not strict and (not _from_board_domain(board, href) or re.search(r"unsubscribe|settings|preferences", href, re.I)):
            continue
        company, location, salary_lines = _split_company_location(_context_lines(anchor, title))
        if not strict and not company:
            continue
        if company.lower() == title.lower():
            company = ""
        job_id = _job_id(board, href)
        key = job_id or hashlib.sha1(f"{board}|{title}|{company}".lower().encode()).hexdigest()[:16]
        if key in postings:
            continue
        salary = parse_money(salary_lines[0]) if salary_lines else None
        if salary and salary < 200:
            salary = salary * 2080
        postings[key] = JobPosting(
            external_id=f"{board.lower()}:{key}",
            title=title,
            company=company,
            location=location,
            url=href,
            remote=looks_remote(location, title),
            salary_min=salary,
            posted_at=received,
            board=board,
        )
    return list(postings.values())


def _html_body(message: Message) -> str:
    html_part = text_part = None
    for part in message.walk() if message.is_multipart() else [message]:
        ctype = part.get_content_type()
        if ctype == "text/html" and html_part is None:
            html_part = part
        elif ctype == "text/plain" and text_part is None:
            text_part = part
    part = html_part or text_part
    if part is None:
        return ""
    payload = part.get_payload(decode=True) or b""
    body = payload.decode(part.get_content_charset() or "utf-8", errors="ignore")
    if part is text_part and html_part is None:
        body = re.sub(r"(https?://\S+)", r'<a href="\1">\1</a>', body)
    return body


def parse_message(raw: bytes | str, board: str | None = None) -> list[JobPosting]:
    message = email.message_from_bytes(raw) if isinstance(raw, bytes) else email.message_from_string(raw)
    board = board or board_for_sender(message.get("From", ""))
    if not board:
        return []
    received = None
    if message.get("Date"):
        try:
            received = parsedate_to_datetime(message["Date"])
            if received.tzinfo:
                received = received.astimezone(timezone.utc).replace(tzinfo=None)
        except (TypeError, ValueError):
            received = None
    return parse_alert_html(_html_body(message), board, received)


class EmailAlertsSource(BaseSource):
    kind = "email"
    label = "Email job alerts (Indeed, LinkedIn, Handshake, Glassdoor, ZipRecruiter...)"
    covers = "Job alert emails from Indeed, LinkedIn, Handshake, Glassdoor, ZipRecruiter, Wellfound and Monster"
    description = (
        "Turn on job alerts in each site, then connect the inbox they arrive in. JobRadar reads those alert emails "
        "over IMAP (read-only) and pulls out every posting. For Gmail, create an app password at "
        "myaccount.google.com/apppasswords."
    )
    fields = [
        Field("host", "IMAP server", "text", True, "imap.gmail.com, imap.mail.yahoo.com, imap.mail.me.com, imap.fastmail.com", "imap.gmail.com"),
        Field("port", "Port", "number", False, "", "993"),
        Field("username", "Email address", "text", True),
        Field("password", "App password", "password", True, "Use an app password, not your normal password."),
        Field("folder", "Folder", "text", False, "Gmail users can use [Gmail]/All Mail", "INBOX"),
        Field("lookback_days", "Look back (days)", "number", False, "", "7"),
    ]

    def fetch(self, config: dict, ctx: SourceContext, client: httpx.Client) -> list[JobPosting]:
        try:
            imap = imaplib.IMAP4_SSL(config.get("host") or "imap.gmail.com", int(config.get("port") or 993), timeout=30)
        except OSError as exc:
            raise SourceError(f"Could not reach IMAP server: {exc}") from exc
        try:
            try:
                imap.login(config.get("username", ""), config.get("password", ""))
            except imaplib.IMAP4.error as exc:
                raise SourceError(f"IMAP login failed: {exc}") from exc
            folder = config.get("folder") or "INBOX"
            status, _ = imap.select(f'"{folder}"', readonly=True)
            if status != "OK":
                raise SourceError(f"Could not open folder {folder}")
            since = (datetime.now() - timedelta(days=int(config.get("lookback_days") or 7))).strftime("%d-%b-%Y")
            postings: list[JobPosting] = []
            for board, spec in BOARDS.items():
                ids: list[bytes] = []
                for sender in spec["senders"]:
                    status, data = imap.search(None, "SINCE", since, "FROM", sender)
                    if status == "OK" and data and data[0]:
                        ids.extend(data[0].split())
                for msg_id in sorted(set(ids), key=int, reverse=True)[:100]:
                    status, data = imap.fetch(msg_id, "(BODY.PEEK[])")
                    if status != "OK":
                        continue
                    for item in data:
                        if isinstance(item, tuple):
                            postings.extend(parse_message(item[1], board))
            return postings
        finally:
            try:
                imap.logout()
            except Exception:
                pass

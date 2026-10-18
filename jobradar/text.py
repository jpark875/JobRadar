import hashlib
import html
import re
from datetime import datetime, timezone

from bs4 import BeautifulSoup

STOPWORDS = frozenset(
    """a about above after again against all also am an and any are as at be because been before being below
    between both but by can could did do does doing down during each etc few for from further had has have having
    he her here hers him his how i if in into is it its itself just me more most my no nor not now of off on once
    only or other our ours out over own per same she should so some such than that the their theirs them then there
    these they this those through to too under until up us very via was we well were what when where which while who
    whom why will with within without would you your yours able across ability work working team teams role job
    candidate candidates including include includes new using use used strong experience experienced year years
    must preferred required requirements responsibilities qualifications plus looking join help make etc company
    opportunity position please apply applicants equal employer status""".split()
)

BLOCK_TAGS = ["p", "div", "li", "ul", "ol", "tr", "table", "section", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre"]
_WORD_RE =re.compile(r"[a-z0-9][a-z0-9+#.\-]*[a-z0-9+#]|[a-z0-9]")
_SPACE_RE = re.compile(r"\s+")


def html_to_text(value: str | None) -> str:
    if not value:
        return ""
    if "&lt;" in value and "<" not in value:
        value = html.unescape(value)
    if "<" not in value:
        return _SPACE_RE.sub(" ", html.unescape(value)).strip()
    soup = BeautifulSoup(value, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    for tag in soup.find_all("br"):
        tag.replace_with("\n")
    for tag in soup.find_all(BLOCK_TAGS):
        tag.insert_before("\n")
        tag.append("\n")
    for tag in soup.find_all("li"):
        tag.insert(0, "• ")
    text = soup.get_text()
    lines = [_SPACE_RE.sub(" ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def clean(value: str | None) -> str:
    return _SPACE_RE.sub(" ", value or "").strip()


def stem(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


def tokenize(text: str, keep_stopwords: bool = False) -> list[str]:
    tokens = []
    for raw in _WORD_RE.findall((text or "").lower()):
        raw = raw.strip(".-")
        if not raw or (not keep_stopwords and raw in STOPWORDS):
            continue
        if raw.isdigit() and len(raw) != 4:
            continue
        tokens.append(stem(raw))
    return tokens


def normalize_key(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (value or "").lower()))


def fingerprint(title: str, company: str, location: str = "") -> str:
    city = normalize_key(location).split(" ")[0] if location else ""
    key = f"{normalize_key(title)}|{normalize_key(company)}|{city}"
    return hashlib.sha256(key.encode()).hexdigest()[:32]


def parse_datetime(value) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)):
        seconds = value / 1000 if value > 10_000_000_000 else value
        dt = datetime.fromtimestamp(seconds, tz=timezone.utc)
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%a, %d %b %Y %H:%M:%S %z", "%m/%d/%Y"):
                try:
                    dt = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
            else:
                return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def parse_money(value) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return int(value) if value > 0 else None
    match = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*([kK])?", str(value))
    if not match:
        return None
    number = float(match.group(1).replace(",", ""))
    if match.group(2):
        number *= 1000
    return int(number) if number > 0 else None


def annualize(amount: int | None, interval: str = "") -> int | None:
    if not amount:
        return None
    interval = (interval or "").lower()
    if interval.startswith(("hour", "ph")) or interval == "hourly":
        return int(amount * 2080)
    if interval.startswith(("month", "pm")):
        return int(amount * 12)
    if interval.startswith(("week", "pw")):
        return int(amount * 52)
    if amount < 200:
        return int(amount * 2080)
    return amount

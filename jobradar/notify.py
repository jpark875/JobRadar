import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from html import escape

import httpx

from .sources.base import Field


@dataclass
class AlertItem:
    title: str
    company: str
    location: str
    score: float
    url: str
    board: str = ""


CHANNEL_FIELDS: dict[str, tuple[str, list[Field]]] = {
    "ntfy": ("Phone push (ntfy)", [
        Field("server", "Server", "text", False, "Install the ntfy app and subscribe to the same topic.", "https://ntfy.sh"),
        Field("topic", "Topic", "text", True, "Pick something hard to guess, e.g. jobradar-7f3k2q"),
        Field("token", "Access token", "password", False, "Only needed for protected topics."),
    ]),
    "email": ("Email", [
        Field("smtp_host", "SMTP server", "text", True, "", "smtp.gmail.com"),
        Field("smtp_port", "Port", "number", False, "", "587"),
        Field("username", "Username", "text", True),
        Field("password", "App password", "password", True),
        Field("to", "Send to", "text", True),
    ]),
    "discord": ("Discord webhook", [Field("webhook_url", "Webhook URL", "password", True)]),
    "slack": ("Slack webhook", [Field("webhook_url", "Webhook URL", "password", True)]),
    "webhook": ("Custom webhook (JSON POST)", [Field("url", "URL", "text", True)]),
}


class NotifyError(RuntimeError):
    pass


def _plain(items: list[AlertItem]) -> str:
    return "\n".join(
        f"{round(i.score)}% · {i.title} @ {i.company or 'Unknown'}{' · ' + i.location if i.location else ''}\n{i.url}"
        for i in items
    )


def _html(heading: str, items: list[AlertItem]) -> str:
    rows = "".join(
        f'<tr><td style="padding:6px 10px;font-weight:600">{round(i.score)}%</td>'
        f'<td style="padding:6px 10px"><a href="{escape(i.url)}">{escape(i.title)}</a><br>'
        f'<span style="color:#555">{escape(i.company)} · {escape(i.location)} · {escape(i.board)}</span></td></tr>'
        for i in items
    )
    return f'<h2 style="font-family:sans-serif">{escape(heading)}</h2><table style="font-family:sans-serif">{rows}</table>'


def send(kind: str, config: dict, heading: str, items: list[AlertItem]) -> None:
    try:
        if kind == "ntfy":
            server = (config.get("server") or "https://ntfy.sh").rstrip("/")
            headers = {"Title": heading, "Tags": "briefcase"}
            if items and items[0].url:
                headers["Click"] = items[0].url
            if config.get("token"):
                headers["Authorization"] = f"Bearer {config['token']}"
            r = httpx.post(f"{server}/{config['topic']}", content=_plain(items[:8]).encode(), headers=headers, timeout=15)
            r.raise_for_status()
        elif kind == "discord":
            body = f"**{heading}**\n" + "\n".join(
                f"`{round(i.score)}%` [{i.title}]({i.url}) · {i.company}" for i in items[:15]
            )
            httpx.post(config["webhook_url"], json={"content": body[:1900]}, timeout=15).raise_for_status()
        elif kind == "slack":
            body = f"*{heading}*\n" + "\n".join(f"`{round(i.score)}%` <{i.url}|{i.title}> · {i.company}" for i in items[:20])
            httpx.post(config["webhook_url"], json={"text": body}, timeout=15).raise_for_status()
        elif kind == "webhook":
            payload = {"title": heading, "jobs": [i.__dict__ for i in items]}
            httpx.post(config["url"], json=payload, timeout=15).raise_for_status()
        elif kind == "email":
            message = EmailMessage()
            message["Subject"] = heading
            message["From"] = config["username"]
            message["To"] = config["to"]
            message.set_content(_plain(items))
            message.add_alternative(_html(heading, items), subtype="html")
            port = int(config.get("smtp_port") or 587)
            context = ssl.create_default_context()
            if port == 465:
                with smtplib.SMTP_SSL(config["smtp_host"], port, context=context, timeout=20) as smtp:
                    smtp.login(config["username"], config["password"])
                    smtp.send_message(message)
            else:
                with smtplib.SMTP(config["smtp_host"], port, timeout=20) as smtp:
                    smtp.starttls(context=context)
                    smtp.login(config["username"], config["password"])
                    smtp.send_message(message)
        else:
            raise NotifyError(f"Unknown channel type {kind}")
    except (httpx.HTTPError, smtplib.SMTPException, OSError, KeyError) as exc:
        raise NotifyError(str(exc) or exc.__class__.__name__) from exc

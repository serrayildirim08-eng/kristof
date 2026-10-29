"""Render the digest (HTML + plain text) and send it once per day over SMTP."""
import logging
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

log = logging.getLogger(__name__)
LABELS = {"high": "HIGH PRIORITY", "medium": "MEDIUM PRIORITY", "low": "LOW PRIORITY"}


def render(items: list[dict], status: dict, date_str: str, root: str, subject: str) -> tuple[str, str]:
    env = Environment(loader=FileSystemLoader(Path(root) / "templates"), autoescape=select_autoescape(["html"]))
    html = env.get_template("digest.html").render(items=items, status=status, date=date_str, subject=subject)
    lines = ["DAILY LEGAL INTELLIGENCE", f"Date: {date_str}", ""]
    for level in ("high", "medium", "low"):
        group = [i for i in items if i["priority"] == level]
        if not group:
            continue
        lines += [LABELS[level], ""]
        for it in group:
            lines += [f"* {it['title']}", f"  {it.get('authority') or '-'} | {it.get('published_at') or '?'} | {it.get('instrument','')}",
                      f"  {it.get('development','')}"]
            lines += [f"  - {c}" for c in it.get("key_changes", [])]
            if level != "low" and it.get("implications"):
                lines.append(f"  Implications: {it['implications']}")
            lines += [f"  {it['url']}", ""]
    if not items:
        lines += ["No relevant developments found in this period.", ""]
    lines += ["MONITORING STATUS", f"* Sources checked: {status['sources_checked']}", f"* Documents discovered: {status['docs_found']}",
              f"* Relevant documents: {status['docs_relevant']}",
              f"* Failed sources: {', '.join(status['failed_sources']) if status['failed_sources'] else 'none'}",
              f"* AI calls / est. cost: {status['ai_calls']} / ${status['ai_cost_usd']:.4f}"]
    return html, "\n".join(lines)


def send(subject: str, html: str, text: str, recipients: list[str]) -> str:
    host, port = os.getenv("SMTP_HOST"), int(os.getenv("SMTP_PORT", "587"))
    user, password, sender = os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD"), os.getenv("EMAIL_FROM") or os.getenv("SMTP_USER")
    if not (host and user and password and sender):
        raise RuntimeError("SMTP settings missing in .env (SMTP_HOST, SMTP_USER, SMTP_PASSWORD, EMAIL_FROM)")
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, sender, ", ".join(recipients)
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    with smtplib.SMTP(host, port, timeout=30) as s:
        s.starttls()
        s.login(user, password)
        s.send_message(msg)
    return msg["Message-ID"] or ""

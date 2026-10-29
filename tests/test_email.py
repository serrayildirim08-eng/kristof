from services import email as mailer

STATUS = {"sources_checked": 2, "docs_found": 10, "docs_relevant": 1, "failed_sources": ["X"], "ai_calls": 1, "ai_cost_usd": 0.0012}


def _item(prio="high"):
    return {"document_id": 1, "title": "Wet betaalbare huur <b>", "url": "https://zoek.officielebekendmakingen.nl/stb-2026-1.html",
            "published_at": "2026-10-28", "authority": "Ministerie", "priority": prio, "instrument": "binding_legislation",
            "development": "Dev", "key_changes": ["a", "b"], "implications": "Imp", "effective_date": "2027-01-01", "insufficient_text": False}


def test_render_sections_and_escaping(settings):
    html, text = mailer.render([_item("high"), _item("medium")], STATUS, "2026-10-29", settings["root"], "S")
    assert "HIGH PRIORITY" in html and "MEDIUM PRIORITY" in html and "LOW PRIORITY" not in html
    assert "&lt;b&gt;" in html and "Failed sources: X" in html
    assert text.startswith("DAILY LEGAL INTELLIGENCE") and "Implications: Imp" in text


def test_render_empty(settings):
    html, text = mailer.render([], STATUS, "2026-10-29", settings["root"], "S")
    assert "No relevant developments" in html and "No relevant developments" in text


def test_send_requires_env(monkeypatch):
    for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "EMAIL_FROM"):
        monkeypatch.delenv(k, raising=False)
    import pytest
    with pytest.raises(RuntimeError):
        mailer.send("s", "<p/>", "t", ["a@b"])

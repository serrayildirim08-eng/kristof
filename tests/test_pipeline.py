"""End-to-end over the pipeline with a stubbed collector and LLM: no network."""
import json

import main
from collectors.base import RawDocument
from services.summarizer import Usage
from storage import database as db


class StubCollector:
    def __init__(self, cfg, http):
        pass

    def collect(self, since, limit):
        return [RawDocument("stb-1", "Wijziging Wet betaalbare huur: huurprijs", "https://x/1", "2026-10-28", "Wet", "Min", "NL", "2026-10-28",
                            content="huurder verhuurder huurprijs huurcommissie " * 5),
                RawDocument("stb-2", "Visserijbesluit", "https://x/2", "2026-10-28", "AMvB", "Min", "NL", "2026-10-28", content="vis")]


class StubLLM:
    calls = 0

    def complete(self, system, user):
        StubLLM.calls += 1
        return {"relevant": True, "priority": "high", "development": "D", "key_changes": [], "implications": "I"}, Usage(500, 100)


def test_pipeline_dedupes_and_caches(conn, settings, monkeypatch):
    monkeypatch.setattr(main, "build_collector", lambda cfg, http: StubCollector(cfg, http))
    src = {"name": "stub", "url": "http://s", "jurisdiction": "NL", "topics": ["rental"], "method": "sru"}
    found, changed = main.collect_source(conn, None, src, settings)
    assert (found, changed) == (2, 2)
    assert main.collect_source(conn, None, src, settings) == (2, 0)  # second run: unchanged, no re-download
    tri = main.triage_new(conn, settings)
    assert tri["relevant"] == 1 and tri["rejected"] == 1
    stats = main.analyze_pending(conn, settings, StubLLM(), dry_run=False)
    assert stats["ai_calls"] == 1 and StubLLM.calls == 1
    items = main.build_digest_items(conn)
    assert len(items) == 1 and items[0]["priority"] == "high"
    # re-analysis hits the cache, no new LLM call
    db.set_document_status(conn, items[0]["document_id"], "relevant")
    stats = main.analyze_pending(conn, settings, StubLLM(), dry_run=False)
    assert stats["cache_hits"] == 1 and StubLLM.calls == 1
    # digest tracking prevents re-sending the same doc
    db.record_delivery(conn, "2026-10-29", ["a@b"], [(items[0]["document_id"], "high")], "m")
    assert main.build_digest_items(conn) == []
    log = conn.execute("SELECT COUNT(*) c FROM analyses").fetchone()["c"]
    assert log == 1 and json.loads(conn.execute("SELECT result_json FROM analyses").fetchone()["result_json"])["relevant"]

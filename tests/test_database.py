from storage import database as db


def test_upsert_document_new_unchanged_updated(conn, source_id):
    d = {"external_id": "stb-1", "title": "T", "url": "http://u", "content": "a"}
    doc_id, state = db.upsert_document(conn, source_id, d)
    assert state == "new"
    assert db.upsert_document(conn, source_id, d) == (doc_id, "unchanged")
    d["content"] = "b"
    assert db.upsert_document(conn, source_id, d) == (doc_id, "updated")
    versions = conn.execute("SELECT COUNT(*) c FROM document_versions WHERE document_id=?", (doc_id,)).fetchone()["c"]
    assert versions == 2
    assert db.find_document(conn, source_id, "stb-1")["status"] == "new"


def test_failed_check_does_not_advance_success(conn, source_id):
    db.mark_source_checked(conn, source_id, ok=True)
    first = db.get_source(conn, source_id)["last_success_at"]
    db.mark_source_checked(conn, source_id, ok=False, error="boom")
    s = db.get_source(conn, source_id)
    assert s["last_success_at"] == first and s["last_error"] == "boom"


def test_analysis_cache_and_spend(conn):
    db.save_analysis(conn, "h", 1, {"relevant": True, "priority": "high"}, "m", 100, 50, 0.01)
    assert db.get_analysis(conn, "h", 1)["priority"] == "high"
    assert db.get_analysis(conn, "h", 2) is None
    assert abs(db.ai_spend_today(conn) - 0.01) < 1e-9


def test_delivery_dedupe(conn, source_id):
    doc_id, _ = db.upsert_document(conn, source_id, {"external_id": "x", "title": "T", "url": "http://u", "content": "c"})
    db.record_delivery(conn, "2026-10-29", ["a@b"], [(doc_id, "high")], "mid")
    assert db.delivery_for(conn, "2026-10-29")["doc_count"] == 1
    assert db.delivery_for(conn, "2026-10-30") is None


def test_unchanged_content_still_records_version_key(conn, source_id):
    d = {"external_id": "r1", "title": "T", "url": "http://u", "content": "a", "version_key": None}
    db.upsert_document(conn, source_id, d)
    d["version_key"] = "guid-1"
    assert db.upsert_document(conn, source_id, d)[1] == "unchanged"
    assert db.find_document(conn, source_id, "r1")["version_key"] == "guid-1"

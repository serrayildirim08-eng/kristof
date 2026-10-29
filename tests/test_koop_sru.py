from datetime import datetime
from pathlib import Path

from collectors.koop_sru import build_query, parse_records


def test_parse_real_record():
    total, docs = parse_records((Path(__file__).parent / "fixtures" / "sru_staatsblad.xml").read_text())
    assert total >= 1 and len(docs) == 1
    d = docs[0]
    assert d.external_id == "stb-2026-338"
    assert d.title.startswith("Besluit van 19 oktober 2026")
    assert d.published_at == "2026-10-28" and d.version_key == "2026-10-28"
    assert d.doc_type == "AMvB" and d.authority.startswith("Ministerie")
    assert d.url == "https://zoek.officielebekendmakingen.nl/stb-2026-338.html"
    assert d.content_url.endswith("/html/stb-2026-338.html")
    assert d.extra["publication"] == "Staatsblad"


def test_build_query():
    q = build_query(["Staatsblad", "Staatscourant"], datetime(2026, 10, 27), ["huur"])
    assert q == '(w.publicatienaam=="Staatsblad" OR w.publicatienaam=="Staatscourant") AND dt.modified>=2026-10-27 AND (cql.textAndIndexes="huur")'


def test_diagnostic_raises():
    import pytest
    bad = '<r xmlns:diag="http://docs.oasis-open.org/ns/search-ws/diagnostic"><diag:message>bad</diag:message></r>'
    with pytest.raises(ValueError):
        parse_records(bad)

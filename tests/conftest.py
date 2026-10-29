import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from settings import load_settings  # noqa: E402
from storage import database as db  # noqa: E402


@pytest.fixture
def settings():
    return load_settings()


@pytest.fixture
def conn(tmp_path):
    c = db.connect(str(tmp_path / "t.sqlite3"))
    yield c
    c.close()


@pytest.fixture
def source_id(conn):
    return db.upsert_source(conn, {"name": "t", "url": "http://x", "jurisdiction": "NL", "topics": ["rental"], "method": "sru"})

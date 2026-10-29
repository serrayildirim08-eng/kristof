"""SQLite persistence. All state lives here so restarts lose nothing."""
import hashlib
import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
  id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, url TEXT, jurisdiction TEXT, topics TEXT,
  method TEXT, enabled INTEGER DEFAULT 1, last_success_at TEXT, last_checked_at TEXT, last_error TEXT);
CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES sources(id),
  external_id TEXT NOT NULL, title TEXT NOT NULL, url TEXT NOT NULL, published_at TEXT,
  doc_type TEXT, jurisdiction TEXT, authority TEXT, version_key TEXT,
  content_hash TEXT, content TEXT, status TEXT NOT NULL DEFAULT 'new', score REAL DEFAULT 0,
  matched_topics TEXT, excerpt TEXT, first_seen_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(source_id, external_id));
CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status);
CREATE INDEX IF NOT EXISTS idx_documents_hash ON documents(content_hash);
CREATE TABLE IF NOT EXISTS document_versions (
  id INTEGER PRIMARY KEY, document_id INTEGER NOT NULL REFERENCES documents(id),
  content_hash TEXT NOT NULL, version_key TEXT, seen_at TEXT NOT NULL,
  UNIQUE(document_id, content_hash));
CREATE TABLE IF NOT EXISTS analyses (
  id INTEGER PRIMARY KEY, content_hash TEXT NOT NULL, profile_version INTEGER NOT NULL,
  relevant INTEGER NOT NULL, priority TEXT, result_json TEXT NOT NULL, model TEXT,
  input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0, cost_usd REAL DEFAULT 0,
  created_at TEXT NOT NULL, UNIQUE(content_hash, profile_version));
CREATE TABLE IF NOT EXISTS execution_logs (
  id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT, status TEXT,
  sources_checked INTEGER DEFAULT 0, docs_found INTEGER DEFAULT 0, docs_relevant INTEGER DEFAULT 0,
  failed_sources TEXT, ai_calls INTEGER DEFAULT 0, ai_cost_usd REAL DEFAULT 0, notes TEXT);
CREATE TABLE IF NOT EXISTS email_deliveries (
  id INTEGER PRIMARY KEY, digest_date TEXT UNIQUE NOT NULL, sent_at TEXT NOT NULL,
  recipients TEXT NOT NULL, doc_count INTEGER DEFAULT 0, message_id TEXT);
CREATE TABLE IF NOT EXISTS digest_items (
  id INTEGER PRIMARY KEY, delivery_id INTEGER NOT NULL REFERENCES email_deliveries(id),
  document_id INTEGER NOT NULL REFERENCES documents(id), priority TEXT,
  UNIQUE(delivery_id, document_id));
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def connect(path: str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    return conn


# ---- sources -------------------------------------------------------------
def upsert_source(conn, cfg: dict) -> int:
    conn.execute(
        """INSERT INTO sources(name,url,jurisdiction,topics,method,enabled) VALUES(?,?,?,?,?,1)
           ON CONFLICT(name) DO UPDATE SET url=excluded.url, jurisdiction=excluded.jurisdiction,
           topics=excluded.topics, method=excluded.method, enabled=1""",
        (cfg["name"], cfg.get("url"), cfg.get("jurisdiction"), json.dumps(cfg.get("topics", [])), cfg["method"]),
    )
    conn.commit()
    return conn.execute("SELECT id FROM sources WHERE name=?", (cfg["name"],)).fetchone()["id"]


def get_source(conn, source_id: int) -> sqlite3.Row:
    return conn.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()


def mark_source_checked(conn, source_id: int, ok: bool, error: str | None = None):
    ts = now_iso()
    if ok:
        conn.execute("UPDATE sources SET last_checked_at=?, last_success_at=?, last_error=NULL WHERE id=?", (ts, ts, source_id))
    else:  # failed check must NOT advance last_success_at
        conn.execute("UPDATE sources SET last_checked_at=?, last_error=? WHERE id=?", (ts, (error or "")[:500], source_id))
    conn.commit()


# ---- documents -----------------------------------------------------------
def find_document(conn, source_id: int, external_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM documents WHERE source_id=? AND external_id=?", (source_id, external_id)).fetchone()


def upsert_document(conn, source_id: int, d: dict) -> tuple[int, str]:
    """Insert or update. Returns (document_id, 'new' | 'updated' | 'unchanged')."""
    ts = now_iso()
    chash = content_hash(d.get("content") or "")
    existing = find_document(conn, source_id, d["external_id"])
    if existing is None:
        cur = conn.execute(
            """INSERT INTO documents(source_id,external_id,title,url,published_at,doc_type,jurisdiction,authority,
               version_key,content_hash,content,status,first_seen_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,'new',?,?)""",
            (source_id, d["external_id"], d["title"], d["url"], d.get("published_at"), d.get("doc_type"),
             d.get("jurisdiction"), d.get("authority"), d.get("version_key"), chash, d.get("content"), ts, ts),
        )
        doc_id = cur.lastrowid
        conn.execute("INSERT OR IGNORE INTO document_versions(document_id,content_hash,version_key,seen_at) VALUES(?,?,?,?)",
                     (doc_id, chash, d.get("version_key"), ts))
        conn.commit()
        return doc_id, "new"
    if existing["content_hash"] == chash:
        return existing["id"], "unchanged"
    conn.execute(
        """UPDATE documents SET title=?, url=?, published_at=?, doc_type=?, authority=?, version_key=?,
           content_hash=?, content=?, status='new', score=0, matched_topics=NULL, excerpt=NULL, updated_at=? WHERE id=?""",
        (d["title"], d["url"], d.get("published_at"), d.get("doc_type"), d.get("authority"), d.get("version_key"),
         chash, d.get("content"), ts, existing["id"]),
    )
    conn.execute("INSERT OR IGNORE INTO document_versions(document_id,content_hash,version_key,seen_at) VALUES(?,?,?,?)",
                 (existing["id"], chash, d.get("version_key"), ts))
    conn.commit()
    return existing["id"], "updated"


def set_document_triage(conn, doc_id: int, status: str, score: float, matched_topics: list[str], excerpt: str | None):
    conn.execute("UPDATE documents SET status=?, score=?, matched_topics=?, excerpt=?, updated_at=? WHERE id=?",
                 (status, score, json.dumps(matched_topics), excerpt, now_iso(), doc_id))
    conn.commit()


def set_document_status(conn, doc_id: int, status: str):
    conn.execute("UPDATE documents SET status=?, updated_at=? WHERE id=?", (status, now_iso(), doc_id))
    conn.commit()


def documents_by_status(conn, statuses: list[str]) -> list[sqlite3.Row]:
    q = ",".join("?" * len(statuses))
    return conn.execute(f"SELECT * FROM documents WHERE status IN ({q}) ORDER BY score DESC, published_at DESC", statuses).fetchall()


# ---- analyses (AI cache) -------------------------------------------------
def get_analysis(conn, chash: str, profile_version: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM analyses WHERE content_hash=? AND profile_version=?", (chash, profile_version)).fetchone()


def save_analysis(conn, chash: str, profile_version: int, result: dict, model: str, in_tok: int, out_tok: int, cost: float):
    conn.execute(
        """INSERT INTO analyses(content_hash,profile_version,relevant,priority,result_json,model,input_tokens,output_tokens,cost_usd,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(content_hash,profile_version) DO UPDATE SET
           relevant=excluded.relevant, priority=excluded.priority, result_json=excluded.result_json, model=excluded.model,
           input_tokens=excluded.input_tokens, output_tokens=excluded.output_tokens, cost_usd=excluded.cost_usd""",
        (chash, profile_version, 1 if result.get("relevant") else 0, result.get("priority"), json.dumps(result, ensure_ascii=False),
         model, in_tok, out_tok, cost, now_iso()),
    )
    conn.commit()


def ai_spend_today(conn, day: date | None = None) -> float:
    day = (day or datetime.now(timezone.utc).date()).isoformat()
    row = conn.execute("SELECT COALESCE(SUM(cost_usd),0) AS c FROM analyses WHERE substr(created_at,1,10)=?", (day,)).fetchone()
    return float(row["c"])


# ---- execution logs ------------------------------------------------------
def start_execution(conn) -> int:
    cur = conn.execute("INSERT INTO execution_logs(started_at,status) VALUES(?, 'running')", (now_iso(),))
    conn.commit()
    return cur.lastrowid


def finish_execution(conn, exec_id: int, status: str, **fields):
    cols = ", ".join(f"{k}=?" for k in fields)
    vals = [json.dumps(v) if isinstance(v, (list, dict)) else v for v in fields.values()]
    conn.execute(f"UPDATE execution_logs SET finished_at=?, status=?{', ' + cols if cols else ''} WHERE id=?",
                 [now_iso(), status, *vals, exec_id])
    conn.commit()


# ---- email ---------------------------------------------------------------
def delivery_for(conn, digest_date: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM email_deliveries WHERE digest_date=?", (digest_date,)).fetchone()


def record_delivery(conn, digest_date: str, recipients: list[str], items: list[tuple[int, str]], message_id: str | None) -> int:
    cur = conn.execute("INSERT INTO email_deliveries(digest_date,sent_at,recipients,doc_count,message_id) VALUES(?,?,?,?,?)",
                       (digest_date, now_iso(), json.dumps(recipients), len(items), message_id))
    did = cur.lastrowid
    conn.executemany("INSERT OR IGNORE INTO digest_items(delivery_id,document_id,priority) VALUES(?,?,?)",
                     [(did, doc_id, prio) for doc_id, prio in items])
    conn.commit()
    return did


def undigested_analyzed(conn) -> list[sqlite3.Row]:
    """Relevant, analyzed documents not yet included in any digest."""
    return conn.execute(
        """SELECT d.*, a.result_json, a.priority FROM documents d
           JOIN analyses a ON a.content_hash=d.content_hash
           WHERE d.status='analyzed' AND a.relevant=1
             AND NOT EXISTS (SELECT 1 FROM digest_items di WHERE di.document_id=d.id)
           ORDER BY CASE a.priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, d.published_at DESC"""
    ).fetchall()

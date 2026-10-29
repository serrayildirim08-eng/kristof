"""Legal monitor entrypoint.
  python main.py run [--dry-run] [--no-email]   # one execution now
  python main.py schedule                       # block, run daily at settings.schedule.time
"""
import argparse
import fcntl
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from collectors import build_collector
from services import email as mailer
from services import summarizer
from services.relevance import triage
from services.scraper import Http, html_to_text
from settings import load_settings, load_sources
from storage import database as db

log = logging.getLogger("legal_monitor")


def collect_source(conn, http, src_cfg: dict, settings: dict) -> tuple[int, int]:
    """Returns (found, new_or_updated). Raises on source failure (caller logs; other sources continue)."""
    sid = db.upsert_source(conn, src_cfg)
    since = datetime.now(timezone.utc) - timedelta(hours=settings["collection"]["window_hours"])
    collector = build_collector(src_cfg, http)
    raw_docs = collector.collect(since, settings["collection"]["max_records_per_source"])
    changed = 0
    for rd in raw_docs:
        existing = db.find_document(conn, sid, rd.external_id)
        if existing and existing["version_key"] == rd.version_key and rd.version_key:
            continue  # unchanged per source metadata: no re-download
        content = rd.content
        if content is None and rd.content_url:
            try:
                content = html_to_text(http.get(rd.content_url).text, settings["collection"]["max_content_chars"])
            except Exception as e:  # noqa: BLE001
                log.warning("content fetch failed for %s: %s", rd.external_id, e)
                content = rd.extra.get("summary") or ""
        d = {"external_id": rd.external_id, "title": rd.title, "url": rd.url, "published_at": rd.published_at,
             "doc_type": rd.doc_type, "jurisdiction": rd.jurisdiction or src_cfg.get("jurisdiction"),
             "authority": rd.authority, "version_key": rd.version_key, "content": content or ""}
        _, state = db.upsert_document(conn, sid, d)
        if state != "unchanged":
            changed += 1
    db.mark_source_checked(conn, sid, ok=True)
    return len(raw_docs), changed


def triage_new(conn, settings: dict) -> dict:
    counts = {"rejected": 0, "ambiguous": 0, "relevant": 0}
    for row in db.documents_by_status(conn, ["new"]):
        t = triage(dict(row), settings)
        db.set_document_triage(conn, row["id"], t.status, t.score, t.topics, t.excerpt)
        counts[t.status] += 1
    return counts


def analyze_pending(conn, settings: dict, client, dry_run: bool) -> dict:
    """Stage C: relevant + ambiguous + previously deferred docs, highest score first, until the daily budget runs out."""
    llm = settings["llm"]
    budget = summarizer.Budget(llm["daily_budget_usd"], db.ai_spend_today(conn), llm["price_input_per_m"],
                               llm["price_output_per_m"], llm["chars_per_token"])
    stats = {"ai_calls": 0, "ai_cost_usd": 0.0, "cache_hits": 0, "ai_budget_hit": False, "pending_ai": 0, "ai_errors": 0}
    pv = settings["profile_version"]
    for row in db.documents_by_status(conn, ["relevant", "ambiguous", "pending_ai"]):
        cached = db.get_analysis(conn, row["content_hash"], pv)
        if cached:
            stats["cache_hits"] += 1
            db.set_document_status(conn, row["id"], "analyzed")
            continue
        if dry_run:
            stats["pending_ai"] += 1
            continue
        try:
            result, usage = summarizer.analyze(dict(row), row["excerpt"] or row["title"], settings, client, budget)
        except summarizer.BudgetExceeded:
            stats["ai_budget_hit"] = True
            stats["pending_ai"] += 1
            db.set_document_status(conn, row["id"], "pending_ai")
            continue
        except Exception as e:  # noqa: BLE001
            log.error("LLM analysis failed for doc %s: %s", row["id"], e)
            stats["ai_errors"] += 1
            db.set_document_status(conn, row["id"], "pending_ai")
            continue
        db.save_analysis(conn, row["content_hash"], pv, result, llm["model"], usage.input_tokens, usage.output_tokens, usage.cost_usd)
        db.set_document_status(conn, row["id"], "analyzed")
        stats["ai_calls"] += 1
        stats["ai_cost_usd"] += usage.cost_usd
    return stats


def build_digest_items(conn) -> list[dict]:
    items = []
    for row in db.undigested_analyzed(conn):
        r = json.loads(row["result_json"])
        items.append({"document_id": row["id"], "title": row["title"], "url": row["url"], "published_at": row["published_at"],
                      "authority": r.get("authority") or row["authority"], "priority": r.get("priority") or "low",
                      "instrument": r.get("instrument") or "other", "development": r.get("development") or "",
                      "key_changes": r.get("key_changes") or [], "implications": r.get("implications") or "",
                      "effective_date": r.get("effective_date"), "insufficient_text": bool(r.get("insufficient_text"))})
    return items


def run_once(dry_run: bool = False, no_email: bool = False) -> int:
    settings = load_settings()
    conn = db.connect(settings["db_path"])
    exec_id = db.start_execution(conn)
    http = Http()
    status = {"sources_checked": 0, "docs_found": 0, "docs_relevant": 0, "failed_sources": []}
    try:
        for src in load_sources():
            status["sources_checked"] += 1
            try:
                found, _ = collect_source(conn, http, src, settings)
                status["docs_found"] += found
                log.info("source %s: %d docs", src["name"], found)
            except Exception as e:  # noqa: BLE001 - one failing source must not stop the rest
                log.error("source %s failed: %s", src["name"], e)
                status["failed_sources"].append(src["name"])
                sid = db.upsert_source(conn, src)
                db.mark_source_checked(conn, sid, ok=False, error=str(e))
        tri = triage_new(conn, settings)
        log.info("triage: %s", tri)
        client = summarizer.GroqClient(settings["llm"], http) if not dry_run else None
        ai = analyze_pending(conn, settings, client, dry_run)
        status.update(ai)
        items = build_digest_items(conn)
        status["docs_relevant"] = len(items)
        today = datetime.now(ZoneInfo(settings["timezone"])).date().isoformat()
        subject = f"{settings['email']['subject_prefix']} — {today}"
        html, text = mailer.render(items, status, today, settings["root"], subject)
        if dry_run:
            print(text)
        elif no_email:
            log.info("email suppressed (--no-email); %d items retained for next digest", len(items))
        elif db.delivery_for(conn, today):
            log.info("digest for %s already sent; skipping", today)
        elif not items and not settings["email"]["send_empty_digest"]:
            log.info("no relevant items and send_empty_digest=false; not sending")
        else:
            mid = mailer.send(subject, html, text, settings["email"]["recipients"])
            db.record_delivery(conn, today, settings["email"]["recipients"], [(i["document_id"], i["priority"]) for i in items], mid)
            log.info("digest sent to %s (%d items)", settings["email"]["recipients"], len(items))
        db.finish_execution(conn, exec_id, "ok", sources_checked=status["sources_checked"], docs_found=status["docs_found"],
                            docs_relevant=status["docs_relevant"], failed_sources=status["failed_sources"],
                            ai_calls=status.get("ai_calls", 0), ai_cost_usd=status.get("ai_cost_usd", 0.0), notes=json.dumps(tri))
        return 0
    except Exception as e:  # noqa: BLE001
        log.exception("execution failed")
        db.finish_execution(conn, exec_id, "failed", notes=str(e)[:500])
        return 1
    finally:
        http.close()
        conn.close()


def locked_run(**kw) -> int:
    lock_path = Path(load_settings()["db_path"]).parent / "run.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log.warning("another execution is running; skipping")
            return 2
        return run_once(**kw)


def main(argv=None):
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--dry-run", action="store_true", help="no LLM calls, no email; print the text digest")
    r.add_argument("--no-email", action="store_true")
    sub.add_parser("schedule")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if a.cmd == "run":
        return locked_run(dry_run=a.dry_run, no_email=a.no_email)
    from apscheduler.schedulers.blocking import BlockingScheduler
    s = load_settings()
    hh, mm = s["schedule"]["time"].split(":")
    sched = BlockingScheduler(timezone=s["timezone"])
    sched.add_job(locked_run, "cron", hour=int(hh), minute=int(mm), misfire_grace_time=3600, coalesce=True, max_instances=1)
    log.info("scheduled daily at %s %s", s["schedule"]["time"], s["timezone"])
    sched.start()


if __name__ == "__main__":
    sys.exit(main())

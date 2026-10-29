# kristof — legal monitor

Daily monitor for Dutch rental law (huurrecht) and corporate law (ondernemingsrecht). Collects new official
publications, filters them with deterministic rules first, sends only ambiguous or relevant excerpts to a
cheap LLM (Groq), and emails one HTML digest per day. SQLite holds all state; it survives restarts.

## Pipeline

collect (per source, isolated failures) → dedupe by `(source, external_id)` + content hash → Stage A rules
(date window, jurisdiction, excluded doc types/titles/keywords) → Stage B keyword score (title ×3, body capped)
→ Stage C one LLM call per doc that both classifies and summarizes (cached by content hash + profile version,
daily USD budget, deferred docs kept as `pending_ai`) → one digest per day (`email_deliveries` prevents repeats).

## Sources (config/sources.yaml)

| Source | Method | Verified 2026-10-29 |
|---|---|---|
| KOOP SRU: Staatsblad + Staatscourant | `sru` (repository.overheid.nl, CQL `w.publicatienaam`, `dt.modified`) | yes |
| Internetconsultatie new consultations | `rss` (feed has no dates; dedupe by guid) | yes |

Add a source: new entry in `sources.yaml` + a collector class in `collectors/` with `method = "<name>"` that returns
`RawDocument`s. Register it in `collectors/base.py`. Candidates already verified as HTTP-reachable: Rechtspraak
Open Data (`data.rechtspraak.nl/uitspraken/zoeken`, `subject=…#civielRecht_ondernemingsrecht`), Tweede Kamer OData,
ondernemersplein.overheid.nl/wetswijzigingen (HTML). Huurcommissie and rijksoverheid.nl news are client-rendered
and would need Playwright.

## Run

```bash
/opt/homebrew/bin/python3.14 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env            # fill GROQ_API_KEY, SMTP_*
.venv/bin/python main.py run --dry-run   # no LLM, no email, prints text digest
.venv/bin/python main.py run             # real run (idempotent per day)
.venv/bin/python main.py schedule        # daily at settings.schedule.time (APScheduler), or use cron:
# 30 7 * * * cd /path/kristof && .venv/bin/python main.py run >> data/cron.log 2>&1
.venv/bin/pytest -q
```

A file lock in `data/run.lock` prevents overlapping executions.

## Tuning (config/settings.yaml)

- `topics[].keywords`, `exclude_keywords`, `exclude_doc_types`, `exclude_title_patterns`: bump `profile_version`
  after changing these so cached analyses are redone.
- `relevance.ambiguous_min_score` / `relevant_min_score`: on 2026-10-29 data (139 docs over 48h) the defaults gave
  134 rejected / 4 ambiguous / 1 relevant.
- `llm.daily_budget_usd`, `price_*_per_m`: prices are config, verify against Groq's current list.
- `email.send_empty_digest`: false by default.

## Cost tracking

`analyses` stores input/output tokens and estimated USD per call; `execution_logs` stores per-run totals.

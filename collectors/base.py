"""Collector contract. Each source gets its own collector; the pipeline never knows site details."""
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class RawDocument:
    external_id: str
    title: str
    url: str
    published_at: str | None          # ISO date "YYYY-MM-DD" or None
    doc_type: str | None = None       # e.g. AMvB, Wet, judgment, proposal
    authority: str | None = None
    jurisdiction: str | None = None
    version_key: str | None = None    # changes when the source says the doc changed (e.g. modified date)
    content: str | None = None        # extracted text, if the listing already carries it
    content_url: str | None = None    # where to fetch text from (fetched only for new/changed docs)
    extra: dict = field(default_factory=dict)


class BaseCollector:
    method = "base"

    def __init__(self, cfg: dict, http):
        self.cfg = cfg
        self.params = cfg.get("params", {}) or {}
        self.http = http

    def collect(self, since: datetime, limit: int) -> list[RawDocument]:
        raise NotImplementedError


def build_collector(cfg: dict, http) -> BaseCollector:
    from collectors.koop_sru import KoopSruCollector
    from collectors.rss import RssCollector

    registry = {c.method: c for c in (KoopSruCollector, RssCollector)}
    try:
        return registry[cfg["method"]](cfg, http)
    except KeyError:
        raise ValueError(f"unknown collector method '{cfg.get('method')}' for source {cfg.get('name')}")

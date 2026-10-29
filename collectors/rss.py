"""Generic RSS/Atom connector (feedparser). Text is fetched from the item link for new items."""
from datetime import datetime, timezone
from time import mktime

import feedparser

from collectors.base import BaseCollector, RawDocument


class RssCollector(BaseCollector):
    method = "rss"

    def collect(self, since: datetime, limit: int) -> list[RawDocument]:
        resp = self.http.get(self.cfg["url"])
        feed = feedparser.parse(resp.text)
        if feed.bozo and not feed.entries:
            raise ValueError(f"feed unparseable: {feed.bozo_exception}")
        out = []
        for e in feed.entries[:limit]:
            link = e.get("link")
            if not link:
                continue
            published = None
            t = e.get("published_parsed") or e.get("updated_parsed")
            if t:
                published = datetime.fromtimestamp(mktime(t), tz=timezone.utc).date().isoformat()
            if published and datetime.fromisoformat(published).replace(tzinfo=timezone.utc) < since.replace(hour=0, minute=0):
                continue
            summary = e.get("summary") or ""
            out.append(RawDocument(
                external_id=e.get("id") or link, title=" ".join((e.get("title") or link).split()), url=link,
                published_at=published, doc_type=self.params.get("doc_type", "news"),
                authority=self.params.get("authority"), version_key=published or (e.get("id") or link),
                content=None, content_url=link, extra={"summary": summary},
            ))
        return out

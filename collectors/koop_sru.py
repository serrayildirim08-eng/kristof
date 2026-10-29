"""KOOP SRU 2.0 connector: Staatsblad / Staatscourant via repository.overheid.nl.
Verified 2026-10-29: index w.publicatienaam, dt.modified, cql.textAndIndexes; records carry gzd:itemUrl manifestation="html".
"""
import xml.etree.ElementTree as ET
from datetime import datetime

from collectors.base import BaseCollector, RawDocument

NS = {
    "sru": "http://docs.oasis-open.org/ns/search-ws/sruResponse",
    "dcterms": "http://purl.org/dc/terms/",
    "gzd": "http://standaarden.overheid.nl/sru",
    "ow": "http://standaarden.overheid.nl/wetgeving/",
}
PAGE = 50


def build_query(publications: list[str], since: datetime, text_terms: list[str] | None) -> str:
    pubs = " OR ".join(f'w.publicatienaam=="{p}"' for p in publications)
    q = f"({pubs}) AND dt.modified>={since.date().isoformat()}"
    if text_terms:
        terms = " OR ".join(f'cql.textAndIndexes="{t}"' for t in text_terms)
        q += f" AND ({terms})"
    return q


def parse_records(xml_text: str) -> tuple[int, list[RawDocument]]:
    root = ET.fromstring(xml_text)
    diag = root.find(".//{http://docs.oasis-open.org/ns/search-ws/diagnostic}message")
    if diag is not None:
        raise ValueError(f"SRU diagnostic: {diag.text}")
    total = int(root.findtext("sru:numberOfRecords", default="0", namespaces=NS))
    docs = []
    for rec in root.findall(".//sru:record", NS):
        ident = rec.findtext(".//dcterms:identifier", namespaces=NS)
        title = rec.findtext(".//dcterms:title", namespaces=NS)
        if not ident or not title:
            continue
        html_url = next((u.text for u in rec.findall(".//gzd:itemUrl", NS) if u.get("manifestation") == "html"), None)
        pref = rec.findtext(".//gzd:preferredUrl", namespaces=NS)
        docs.append(RawDocument(
            external_id=ident,
            title=" ".join(title.split()),
            url=pref or html_url or f"https://zoek.officielebekendmakingen.nl/{ident}.html",
            published_at=rec.findtext(".//dcterms:available", namespaces=NS) or rec.findtext(".//dcterms:issued", namespaces=NS),
            doc_type=rec.findtext(".//dcterms:type", namespaces=NS),
            authority=rec.findtext(".//dcterms:creator", namespaces=NS),
            version_key=rec.findtext(".//dcterms:modified", namespaces=NS),
            content_url=html_url,
            extra={"publication": rec.findtext(".//ow:publicatienaam", namespaces=NS),
                   "subject": rec.findtext(".//dcterms:subject", namespaces=NS)},
        ))
    return total, docs


class KoopSruCollector(BaseCollector):
    method = "sru"

    def collect(self, since: datetime, limit: int) -> list[RawDocument]:
        query = build_query(self.params.get("publications", ["Staatsblad"]), since, self.params.get("text_terms"))
        out, start = [], 1
        while len(out) < limit:
            resp = self.http.get(self.cfg["url"], params={
                "operation": "searchRetrieve", "version": "2.0", "query": query,
                "maximumRecords": min(PAGE, limit - len(out)), "startRecord": start,
                "sortBy": "dt.modified/sort.descending",
            })
            total, docs = parse_records(resp.text)
            out.extend(docs)
            start += PAGE
            if not docs or start > total:
                break
        return out

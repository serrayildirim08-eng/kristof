"""Stage A (deterministic rules) + Stage B (local keyword scoring). No AI here.
Outcome: 'rejected' | 'ambiguous' (needs LLM) | 'relevant' (confident), plus score, topics, excerpt.
"""
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta


@dataclass
class Triage:
    status: str
    score: float
    topics: list[str]
    excerpt: str | None
    reason: str


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return " ".join(s.lower().split())


def _count(kw: str, text: str) -> int:
    return len(re.findall(r"(?<![a-z0-9])" + re.escape(kw) + r"(?![a-z0-9])", text))


def keyword_hits(text: str, keywords: list[str]) -> dict[str, int]:
    t = _norm(text)
    return {kw: n for kw in keywords if (n := _count(_norm(kw), t))}


def make_excerpt(content: str, keywords: list[str], max_chars: int) -> str:
    """Windows around keyword hits, so the LLM sees the relevant passages only."""
    if not content:
        return ""
    low = _norm(content)
    pieces, used = [], 0
    for kw in keywords:
        for m in re.finditer(r"(?<![a-z0-9])" + re.escape(_norm(kw)) + r"(?![a-z0-9])", low):
            a, b = max(0, m.start() - 250), min(len(low), m.end() + 350)
            if any(a < pb and b > pa for pa, pb in pieces):
                continue
            pieces.append((a, b))
            used += b - a
            if used >= max_chars:
                break
        if used >= max_chars:
            break
    if not pieces:
        return content[:max_chars]
    pieces.sort()
    norm_src = unicodedata.normalize("NFKD", content).encode("ascii", "ignore").decode()
    # _norm collapsed whitespace; rebuild on the same collapsed string so offsets line up
    collapsed = " ".join(norm_src.split())
    return "\n…\n".join(collapsed[a:b] for a, b in pieces)[:max_chars]


def triage(doc: dict, settings: dict, today: date | None = None) -> Triage:
    today = today or date.today()
    rel = settings["relevance"]
    window_days = max(2, settings["collection"]["window_hours"] // 24 + 1)

    # Stage A: deterministic gates
    if settings.get("jurisdictions") and doc.get("jurisdiction") and doc["jurisdiction"] not in settings["jurisdictions"]:
        return Triage("rejected", 0, [], None, "jurisdiction")
    if doc.get("published_at"):
        try:
            pub = date.fromisoformat(doc["published_at"][:10])
        except ValueError:
            return Triage("rejected", 0, [], None, "invalid date")
        if pub < today - timedelta(days=window_days) or pub > today + timedelta(days=1):
            return Triage("rejected", 0, [], None, "outside window")
    title, body = doc.get("title") or "", doc.get("content") or ""
    if doc.get("doc_type") and _norm(doc["doc_type"]) in {_norm(t) for t in settings.get("exclude_doc_types", [])}:
        return Triage("rejected", 0, [], None, "excluded doc type")
    if any(re.search(p, title, re.IGNORECASE) for p in settings.get("exclude_title_patterns", [])):
        return Triage("rejected", 0, [], None, "excluded title pattern")
    if keyword_hits(title, settings.get("exclude_keywords", [])) or keyword_hits(body[:2000], settings.get("exclude_keywords", [])):
        return Triage("rejected", 0, [], None, "excluded topic")

    # Stage B: keyword scoring (title weighted, body capped)
    score, topics, all_kws = 0.0, [], []
    for topic in settings["topics"]:
        th = keyword_hits(title, topic["keywords"])
        bh = keyword_hits(body, topic["keywords"])
        ts = sum(th.values()) * rel["title_weight"] + min(sum(bh.values()), rel["body_cap"])
        if ts > 0:
            topics.append(topic["key"])
            all_kws.extend(set(th) | set(bh))
        score += ts
    if score < rel["ambiguous_min_score"]:
        return Triage("rejected", score, topics, None, "no keyword signal")
    excerpt = make_excerpt(body, all_kws, rel["excerpt_chars"]) if body else title
    status = "relevant" if score >= rel["relevant_min_score"] else "ambiguous"
    return Triage(status, score, topics, excerpt, "keyword score")

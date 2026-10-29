"""Stage C: one LLM call per document that both classifies and summarizes. Cached by (content_hash, profile_version).
Provider: Groq via its OpenAI-compatible chat endpoint (plain httpx, no SDK)."""
import json
import logging
import os
from dataclasses import dataclass

from services.scraper import Http

log = logging.getLogger(__name__)

SYSTEM = (
    "You are a Dutch legal analyst. Decide if the document is relevant to the monitored topics and, if so, summarize it. "
    "Use ONLY the provided text. Never invent obligations, dates, citations or conclusions; use null when unknown. "
    "Reply with JSON only."
)
SCHEMA = (
    '{"relevant":bool,"priority":"high"|"medium"|"low","instrument":"binding_legislation"|"proposal"|"judgment"|"guidance"|"other",'
    '"authority":str|null,"development":str,"key_changes":[str],"implications":str,"effective_date":str|null,'
    '"insufficient_text":bool}'
)


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


class Budget:
    """Daily AI spend ceiling. The harness decides; the LLM never sees this."""

    def __init__(self, limit_usd: float, spent_usd: float, price_in: float, price_out: float, chars_per_token: float):
        self.limit, self.spent = limit_usd, spent_usd
        self.price_in, self.price_out, self.cpt = price_in / 1e6, price_out / 1e6, chars_per_token

    def estimate(self, prompt_chars: int, max_out: int) -> float:
        return (prompt_chars / self.cpt) * self.price_in + max_out * self.price_out

    def allows(self, prompt_chars: int, max_out: int) -> bool:
        return self.spent + self.estimate(prompt_chars, max_out) <= self.limit

    def charge(self, in_tok: int, out_tok: int) -> float:
        cost = in_tok * self.price_in + out_tok * self.price_out
        self.spent += cost
        return cost


class BudgetExceeded(Exception):
    pass


class GroqClient:
    def __init__(self, cfg: dict, http: Http | None = None, api_key: str | None = None):
        self.cfg = cfg
        self.key = api_key or os.getenv("GROQ_API_KEY")
        self.http = http or Http(timeout=60)

    def complete(self, system: str, user: str) -> tuple[dict, Usage]:
        if not self.key:
            raise RuntimeError("GROQ_API_KEY missing")
        body = {
            "model": self.cfg["model"], "temperature": self.cfg.get("temperature", 0),
            "max_tokens": self.cfg["max_output_tokens"], "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        last = None
        for attempt in range(3):
            try:
                r = self.http.client.post(f"{self.cfg['base_url']}/chat/completions", json=body,
                                          headers={"Authorization": f"Bearer {self.key}"})
                r.raise_for_status()
                data = r.json()
                u = data.get("usage", {})
                return (json.loads(data["choices"][0]["message"]["content"]),
                        Usage(u.get("prompt_tokens", 0), u.get("completion_tokens", 0)))
            except Exception as e:  # noqa: BLE001 - retry any transport/parse failure, bounded
                last = e
                log.warning("LLM call failed (attempt %d): %s", attempt + 1, type(e).__name__)
        raise RuntimeError(f"LLM failed after 3 attempts: {last}")


def build_user_prompt(doc: dict, topics: list[dict], excerpt: str) -> str:
    topic_lines = "; ".join(f"{t['name']}" for t in topics)
    return (
        f"Topics: {topic_lines}\nSchema: {SCHEMA}\n"
        f"Title: {doc['title']}\nAuthority: {doc.get('authority') or 'unknown'}\nType: {doc.get('doc_type') or 'unknown'}\n"
        f"Published: {doc.get('published_at') or 'unknown'}\nText:\n{excerpt}"
    )


def analyze(doc: dict, excerpt: str, settings: dict, client: GroqClient, budget: Budget) -> tuple[dict, Usage]:
    prompt = build_user_prompt(doc, settings["topics"], excerpt)
    if not budget.allows(len(prompt) + len(SYSTEM), settings["llm"]["max_output_tokens"]):
        raise BudgetExceeded
    result, usage = client.complete(SYSTEM, prompt)
    usage.cost_usd = budget.charge(usage.input_tokens, usage.output_tokens)
    result.setdefault("relevant", False)
    result.setdefault("priority", "low")
    result.setdefault("key_changes", [])
    return result, usage

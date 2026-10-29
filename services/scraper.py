"""HTTP with bounded retry (max 3, exponential backoff + jitter) and HTML -> text extraction."""
import logging
import random
import time

import httpx
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)
RETRY_STATUS = {429, 500, 502, 503, 504}


class Http:
    def __init__(self, user_agent: str = "legal-monitor/0.1", timeout: float = 30.0, retries: int = 3):
        self.client = httpx.Client(headers={"User-Agent": user_agent}, timeout=timeout, follow_redirects=True)
        self.retries = retries

    def get(self, url: str, params: dict | None = None) -> httpx.Response:
        last = None
        for attempt in range(self.retries):
            try:
                r = self.client.get(url, params=params)
                if r.status_code in RETRY_STATUS:
                    raise httpx.HTTPStatusError(f"status {r.status_code}", request=r.request, response=r)
                r.raise_for_status()
                return r
            except (httpx.HTTPError, httpx.HTTPStatusError) as e:
                last = e
                if attempt == self.retries - 1:
                    break
                wait = (2 ** attempt) + random.uniform(0, 0.5)
                log.warning("GET %s failed (%s), retry in %.1fs", url, type(e).__name__, wait)
                time.sleep(wait)
        raise RuntimeError(f"GET failed after {self.retries} attempts: {url}: {last}")

    def close(self):
        self.client.close()


def html_to_text(html: str, max_chars: int = 60000) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "noscript", "svg"]):
        tag.decompose()
    root = soup.find("main") or soup.find("article") or soup.body or soup
    text = root.get_text("\n")
    lines = [" ".join(l.split()) for l in text.splitlines()]
    text = "\n".join(l for l in lines if l)
    return text[:max_chars]

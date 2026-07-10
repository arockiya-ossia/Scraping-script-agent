"""Wrapper around the Firecrawl API — an escalation tool, never the default
fetch path. Playwright (`fetch_url`) and httpx (`probe_endpoint`) remain
primary; Firecrawl is only reached for when they've demonstrably failed:

- `investigate` re-run after a docker_execute HTTP_FORBIDDEN — retry the
  fetch through Firecrawl before giving up (CLAUDE.md §12's adversarial
  bot-protected domain case).
- Bounded Playwright interactions still found zero job DOM nodes — one
  Firecrawl Actions attempt with a richer interaction sequence.
- The careers URL resolves to a PDF — route to Firecrawl's native PDF
  parsing instead of failing outright.

Firecrawl's `/v1/scrape` response doesn't report per-call credit cost, so
credit spend is measured directly: snapshot the account's
`remaining_credits` (via `/v1/team/credit-usage`) before and after each
call and take the real delta — not an estimate. This is the scarcest,
least-linear-cost resource in the system (unlike LLM tokens, credits are a
hard-capped paid balance), so cost_report.json tracks it explicitly.
"""

from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from config import settings

FIRECRAWL_BASE_URL = "https://api.firecrawl.dev/v1"


@dataclass
class FirecrawlResult:
    success: bool
    html: Optional[str] = None
    markdown: Optional[str] = None
    links: list[str] = field(default_factory=list)
    status_code: Optional[int] = None
    error: Optional[str] = None
    credits_used: Optional[int] = None


class FirecrawlClient:
    def __init__(self, api_key: Optional[str] = None):
        key = api_key or settings.firecrawl_api_key
        if not key:
            raise RuntimeError("FIRECRAWL_API_KEY not configured — Firecrawl escalation unavailable")
        self.api_key = key
        self._client = httpx.Client(
            base_url=FIRECRAWL_BASE_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            timeout=120.0,
        )
        self.total_credits_used = 0
        self.total_calls = 0

    def _remaining_credits(self) -> Optional[int]:
        try:
            resp = self._client.get("/team/credit-usage")
            resp.raise_for_status()
            return resp.json()["data"]["remaining_credits"]
        except Exception:
            return None

    def scrape(
        self,
        url: str,
        formats: Optional[list[str]] = None,
        actions: Optional[list[dict[str, Any]]] = None,
        wait_for_ms: Optional[int] = None,
    ) -> FirecrawlResult:
        payload: dict[str, Any] = {"url": url, "formats": formats or ["html", "markdown", "links"]}
        if actions:
            payload["actions"] = actions
        if wait_for_ms:
            payload["waitFor"] = wait_for_ms

        self.total_calls += 1
        before = self._remaining_credits()

        try:
            resp = self._client.post("/scrape", json=payload)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            return FirecrawlResult(success=False, error=str(exc))
        finally:
            after = self._remaining_credits()
            if before is not None and after is not None:
                delta = max(before - after, 0)
                self.total_credits_used += delta

        page = data.get("data", {}) or {}
        metadata = page.get("metadata", {}) or {}
        return FirecrawlResult(
            success=bool(data.get("success")),
            html=page.get("html"),
            markdown=page.get("markdown"),
            links=page.get("links", []) or [],
            status_code=metadata.get("statusCode"),
            credits_used=(before - after) if (before is not None and after is not None) else None,
        )

    def close(self) -> None:
        self._client.close()


_client_singleton: Optional[FirecrawlClient] = None


def get_firecrawl_client() -> FirecrawlClient:
    """Lazy singleton — never constructed (and never requires the API key)
    unless an escalation path actually needs it.
    """
    global _client_singleton
    if _client_singleton is None:
        _client_singleton = FirecrawlClient()
    return _client_singleton


def firecrawl_credits_used() -> int:
    """Read-only accessor for finalize.py's cost report — 0 if Firecrawl
    was never invoked this run.
    """
    return _client_singleton.total_credits_used if _client_singleton else 0

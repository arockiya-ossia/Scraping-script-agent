"""Playwright wrapper that renders a page and captures its XHR/fetch network
log — this is how hidden JSON/GraphQL APIs get discovered without ever
hardcoding a platform name (CLAUDE.md §3).
"""

from dataclasses import dataclass, field
from typing import Any

from playwright.sync_api import sync_playwright


@dataclass
class FetchResult:
    url: str
    status: int
    html: str
    network_requests: list[dict[str, Any]] = field(default_factory=list)


def fetch_url(url: str, wait_ms: int = 3000, capture_network: bool = True) -> FetchResult:
    captured: list[dict[str, Any]] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()

        if capture_network:
            def on_response(response):
                req = response.request
                if req.resource_type in ("xhr", "fetch"):
                    try:
                        body = response.text()
                    except Exception:
                        body = None
                    captured.append(
                        {
                            "url": response.url,
                            "method": req.method,
                            "status": response.status,
                            "body": body,
                        }
                    )

            page.on("response", on_response)

        response = page.goto(url, wait_until="networkidle")
        page.wait_for_timeout(wait_ms)
        html = page.content()
        status = response.status if response else 0

        browser.close()

    return FetchResult(url=url, status=status, html=html, network_requests=captured)

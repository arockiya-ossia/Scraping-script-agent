"""Playwright wrapper that renders a page and captures its XHR/fetch network
log — this is how hidden JSON/GraphQL APIs get discovered without ever
hardcoding a platform name (CLAUDE.md §3).

Captures both sides of each XHR/fetch call (request method/headers/body and
response status/content-type/body) so investigate.py can replay a mutated
version of an observed POST/GraphQL request, not just GET query params.

Optionally performs a bounded set of *generic* browser interactions
(click/scroll/fill/select) when the passive page load didn't reveal a job
listing — some ATS portals (an interactive candidate-search widget, not any
one company) require an explicit "Search"/"View openings" click, or a blank
search submission, before the real job-listing XHR fires. These are
horizontal UI patterns across countless career portals (CLAUDE.md §2 #2),
never a per-domain selector, and are hard-capped so a stuck page can't run
away with the sandbox's time budget.
"""

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from playwright.sync_api import sync_playwright

# Generic verbs that trigger a job-listing search/reveal across many career
# portals — never a company-specific label.
CLICK_TARGET_MARKERS = (
    "search jobs",
    "view all jobs",
    "view openings",
    "view jobs",
    "see all jobs",
    "browse jobs",
    "find jobs",
    "explore opportunities",
    "load more",
    "show more",
)
SEARCH_INPUT_MARKERS = ("search", "keyword", "job title", "find a job")


@dataclass
class FetchResult:
    url: str
    status: int
    html: str
    network_requests: list[dict[str, Any]] = field(default_factory=list)
    interactions: list[dict[str, Any]] = field(default_factory=list)


class _Budget:
    def __init__(self, max_interactions: int, max_scrolls: int, max_page_time: float, started: float):
        self.interactions = max_interactions
        self.scrolls = max_scrolls
        self.deadline = started + max_page_time

    def time_left(self) -> float:
        return self.deadline - time.monotonic()


def _element_label(el) -> str:
    try:
        text = (el.inner_text() or "").strip().lower()
    except Exception:
        text = ""
    if text:
        return text
    try:
        return (el.get_attribute("aria-label") or "").strip().lower()
    except Exception:
        return ""


def _try_click_reveal_button(page, budget: _Budget) -> Optional[dict]:
    if budget.interactions <= 0 or budget.time_left() <= 0:
        return None
    for selector in ("button", "a", "[role=button]"):
        try:
            elements = page.query_selector_all(selector)
        except Exception:
            continue
        for el in elements:
            label = _element_label(el)
            if not label or not any(marker in label for marker in CLICK_TARGET_MARKERS):
                continue
            try:
                el.click(timeout=3000)
                page.wait_for_timeout(1500)
                budget.interactions -= 1
                return {"action": "click", "target": label}
            except Exception:
                continue
    return None


def _try_search_submit(page, budget: _Budget) -> Optional[dict]:
    if budget.interactions <= 0 or budget.time_left() <= 0:
        return None
    try:
        inputs = page.query_selector_all("input[type=text], input[type=search], input:not([type])")
    except Exception:
        return None
    for el in inputs:
        try:
            hint = " ".join(
                (el.get_attribute(attr) or "") for attr in ("placeholder", "name", "aria-label")
            ).lower()
        except Exception:
            continue
        if not any(marker in hint for marker in SEARCH_INPUT_MARKERS):
            continue
        try:
            el.click(timeout=2000)
            el.press("Enter")
            page.wait_for_timeout(1500)
            budget.interactions -= 1
            return {"action": "fill_and_enter", "target": hint.strip() or "(unnamed search input)"}
        except Exception:
            continue
    return None


def _try_scroll(page, budget: _Budget) -> Optional[dict]:
    if budget.scrolls <= 0 or budget.time_left() <= 0:
        return None
    try:
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(1200)
        budget.scrolls -= 1
        return {"action": "scroll"}
    except Exception:
        return None


def _run_interactions(page, budget: _Budget) -> list[dict]:
    log: list[dict] = []
    clicked = _try_click_reveal_button(page, budget)
    if clicked:
        log.append(clicked)
    submitted = _try_search_submit(page, budget)
    if submitted:
        log.append(submitted)
    while budget.scrolls > 0 and budget.time_left() > 0:
        scrolled = _try_scroll(page, budget)
        if not scrolled:
            break
        log.append(scrolled)
    return log


def fetch_url(
    url: str,
    wait_ms: int = 3000,
    capture_network: bool = True,
    interact: bool = False,
    max_interactions: int = 10,
    max_scrolls: int = 5,
    max_page_time: float = 60.0,
) -> FetchResult:
    captured: list[dict[str, Any]] = []
    interactions: list[dict[str, Any]] = []
    started = time.monotonic()
    html = ""
    status = 0

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()

            if capture_network:
                def on_response(response):
                    req = response.request
                    if req.resource_type in ("xhr", "fetch"):
                        try:
                            body = response.text()
                        except Exception:
                            body = None
                        try:
                            request_headers = dict(req.headers)
                        except Exception:
                            request_headers = {}
                        try:
                            request_body = req.post_data
                        except Exception:
                            request_body = None
                        try:
                            response_content_type = response.headers.get("content-type")
                        except Exception:
                            response_content_type = None
                        captured.append(
                            {
                                "url": response.url,
                                "method": req.method,
                                "status": response.status,
                                "body": body,  # response body — kept for backward compat
                                "request_headers": request_headers,
                                "request_body": request_body,
                                "response_content_type": response_content_type,
                            }
                        )

                page.on("response", on_response)

            # A navigation failure (timeout, DNS error, connection refused,
            # aborted by the target) must degrade to an honest empty-ish
            # result — not raise. Without this, a single slow/unresponsive
            # domain crashes the entire graph run (an unhandled exception
            # here propagates straight through investigate() and the
            # @traced decorator), directly contradicting the system's own
            # "honest failure, never a crash" design goal. Demonstrated
            # live: a real domain hit exactly this — `Page.goto: Timeout
            # 30000ms exceeded` — with no calling code prepared for it.
            try:
                response = page.goto(url, wait_until="networkidle", timeout=int(max_page_time * 1000))
                status = response.status if response else 0
                page.wait_for_timeout(wait_ms)

                if interact:
                    budget = _Budget(max_interactions, max_scrolls, max_page_time, started)
                    interactions = _run_interactions(page, budget)
            except Exception:
                status = 0

            try:
                html = page.content()
            except Exception:
                html = ""
        finally:
            # Always close the browser, even if navigation/interaction
            # raised above — otherwise a hung Chromium process leaks for
            # every failed fetch, not just successful ones.
            browser.close()

    return FetchResult(url=url, status=status, html=html, network_requests=captured, interactions=interactions)

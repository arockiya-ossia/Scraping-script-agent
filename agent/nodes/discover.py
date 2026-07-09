"""Domain -> candidate careers URL, via search + common-path probing
(CLAUDE.md §7.1). No LLM call — this is cheap, deterministic reconnaissance;
`investigate` is where interpretation happens.
"""

from typing import Optional
from urllib.parse import urljoin

from lxml import html as lxml_html

from agent.nodes import traced
from agent.state import AgentState
from agent.tools.probe_endpoint import ProbeResult, probe_endpoint
from agent.tools.search_web import search_web

COMMON_PATHS = [
    "/careers",
    "/jobs",
    "/en/careers",
    "/company/careers",
    "/about/careers",
    "/careers.html",
    "/jobs.html",
    "/careers/jobs",
]

# Horizontal pattern, not per-domain logic (CLAUDE.md §2 #2): many "careers"
# pages are marketing pages that just link out to a separate ATS domain.
# Following that link is a generic step applicable to any company; it's
# never trusted as the final answer without investigate.py's empirical
# confirmation afterward.
ATS_DOMAIN_MARKERS = (
    "greenhouse.io",
    "lever.co",
    "myworkdayjobs.com",
    "smartrecruiters.com",
    "icims.com",
    "workable.com",
    "ashbyhq.com",
    "jobvite.com",
    "bamboohr.com",
)


def _looks_like_careers_url(url: str, domain: str) -> bool:
    if domain not in url:
        return False
    lowered = url.lower()
    return "career" in lowered or "job" in lowered


def _probe(url: str) -> Optional[ProbeResult]:
    try:
        result = probe_endpoint(url, method="GET", timeout=10.0)
        return result if result.status < 400 else None
    except Exception:
        return None


def _find_ats_link(html_text: str, base_url: str) -> Optional[str]:
    try:
        tree = lxml_html.fromstring(html_text)
    except Exception:
        return None
    for href in tree.xpath("//a/@href"):
        if any(marker in href for marker in ATS_DOMAIN_MARKERS):
            return href if href.startswith("http") else urljoin(base_url, href)
    return None


@traced
def discover(state: AgentState) -> AgentState:
    domain = state["domain"]
    evidence = state["evidence"]

    candidates: list[str] = []
    try:
        results = search_web(f"{domain} careers jobs")
        candidates.extend(
            r["link"] for r in results if r.get("link") and _looks_like_careers_url(r["link"], domain)
        )
    except Exception:
        pass  # search failure isn't fatal — fall back to common-path probing

    candidates.extend(f"https://{domain}{path}" for path in COMMON_PATHS)

    chosen = None
    for url in candidates:
        result = _probe(url)
        if result is None:
            continue
        chosen = url
        ats_link = _find_ats_link(result.text_body, url)
        if ats_link:
            chosen = ats_link
        break

    evidence.careers_url = chosen or (candidates[0] if candidates else f"https://{domain}")
    return state

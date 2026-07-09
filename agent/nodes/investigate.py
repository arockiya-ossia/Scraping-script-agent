"""Careers URL -> InvestigationEvidence (CLAUDE.md §7.1).

Empirical gathering (fetch_url network capture + probe_endpoint pagination/
filter tests) is deterministic Python. The LLM's job is to interpret the
gathered evidence into a source_type classification and reported_total_count
— the things a model reads better than a hand-written heuristic — while
pagination_param_confirmed is only ever set from an actual probe_endpoint
diff, never from the LLM's say-so (CLAUDE.md §6.2).
"""

import json
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from lxml import html as lxml_html

from agent.llm.client import llm_client
from agent.llm.codeformat import extract_json
from agent.models.evidence import SourceType
from agent.nodes import traced
from agent.state import AgentState
from agent.tools.fetch_url import fetch_url
from agent.tools.probe_endpoint import probe_endpoint

PROMPT_PATH = Path(__file__).resolve().parent.parent / "llm" / "prompts" / "investigate.md"

JOB_LIST_KEYS = ("title", "jobtitle", "position", "role", "postingtitle")
JOB_ID_KEYS = ("id", "jobid", "requisitionid", "reqid", "postingid")

# Generic ATS response-shape hints — a horizontal pattern used across
# thousands of companies (CLAUDE.md §2 #2 allows this as a *hint*). Never
# trusted directly: every fact still goes through probe_endpoint below
# before being written into evidence.
ATS_HOST_HINTS = {
    "greenhouse.io": "Greenhouse job board API, typically paginated via `page`.",
    "myworkdayjobs.com": "Workday CXS API — usually POST with a JSON body, pagination via `offset`/`limit` in the body, not query params.",
    "lever.co": "Lever postings API — usually returns the full list in one call, no pagination.",
    "smartrecruiters.com": "SmartRecruiters API — pagination via `offset`/`limit`.",
    "icims.com": "iCIMS — often server-rendered search results pages.",
}


def _looks_like_job_list(payload: Any) -> Optional[list]:
    """Heuristic: does this JSON payload contain a list of job-shaped dicts?"""
    candidates: list = []
    if isinstance(payload, list):
        candidates = payload
    elif isinstance(payload, dict):
        for value in payload.values():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                candidates = value
                break
    if not candidates or not isinstance(candidates[0], dict):
        return None
    keys = {k.lower() for k in candidates[0].keys()}
    if any(k in keys for k in JOB_LIST_KEYS) or any(k in keys for k in JOB_ID_KEYS):
        return candidates
    return None


def _find_json_api_candidates(network_requests: list[dict]) -> list[dict]:
    found = []
    for req in network_requests:
        body = req.get("body")
        if not body:
            continue
        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, TypeError):
            continue
        jobs = _looks_like_job_list(payload)
        if jobs:
            found.append(
                {
                    "url": req["url"],
                    "method": req.get("method", "GET"),
                    "job_count": len(jobs),
                    "sample": jobs[0],
                }
            )
    return found


def _url_with_params(url: str, overrides: dict) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    for k, v in overrides.items():
        query[k] = [str(v)]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def _try_pagination_params(url: str) -> dict:
    """Empirically test common pagination param names — confirmed only if
    the result set actually changes, never guessed (CLAUDE.md §6.2).
    """
    try:
        base = probe_endpoint(url)
        base_jobs = _looks_like_job_list(base.json_body) or []
    except Exception:
        return {"confirmed": False, "mechanism": None, "param": None}

    if not base_jobs:
        return {"confirmed": False, "mechanism": None, "param": None}

    trials = [
        ("page", "page number", {"page": 2}),
        ("offset", "offset/limit", {"offset": len(base_jobs), "limit": len(base_jobs)}),
        ("start", "offset/limit", {"start": len(base_jobs)}),
    ]
    for param, mechanism, overrides in trials:
        try:
            probed = probe_endpoint(_url_with_params(url, overrides))
            probed_jobs = _looks_like_job_list(probed.json_body) or []
        except Exception:
            continue
        base_ids = {json.dumps(j, sort_keys=True, default=str) for j in base_jobs}
        probed_ids = {json.dumps(j, sort_keys=True, default=str) for j in probed_jobs}
        if probed_jobs and base_ids != probed_ids:
            return {"confirmed": True, "mechanism": mechanism, "param": param}

    return {"confirmed": False, "mechanism": None, "param": None}


def _try_india_filter(url: str) -> Optional[str]:
    for param, value in [("country", "IN"), ("location", "India"), ("country", "India")]:
        try:
            probed = probe_endpoint(_url_with_params(url, {param: value}))
            jobs = _looks_like_job_list(probed.json_body)
            if jobs:
                return f"query param {param}={value}"
        except Exception:
            continue
    return None


JOB_LINK_MARKERS = ("job", "career", "position", "requisition", "opening", "vacancy")


def _extract_job_links(html_text: str) -> set:
    """Generic heuristic for SSR job-listing pages: anchors whose href looks
    job-posting-shaped. Not a per-domain selector — the same marker list
    applies to any site (CLAUDE.md §2 #2 allows horizontal patterns).
    """
    try:
        tree = lxml_html.fromstring(html_text)
    except Exception:
        return set()
    hrefs = tree.xpath("//a/@href")
    return {h for h in hrefs if any(marker in h.lower() for marker in JOB_LINK_MARKERS)}


def _try_ssr_pagination(url: str, base_links: set) -> dict:
    """Empirically test common HTML pagination query params. A page-2 fetch
    that returns a *different* link set confirms multi-page pagination; one
    that returns an *empty* set confirms all jobs already fit on one page —
    both are legitimate, empirically-confirmed outcomes.
    """
    if not base_links:
        return {"confirmed": False, "mechanism": None, "param": None}
    for param in ("page", "p", "pg"):
        try:
            probed = probe_endpoint(_url_with_params(url, {param: 2}))
            probed_links = _extract_job_links(probed.text_body)
        except Exception:
            continue
        if probed_links and probed_links != base_links:
            return {"confirmed": True, "mechanism": "page number", "param": param}
        if not probed_links:
            return {"confirmed": True, "mechanism": "single_page", "param": None}
    return {"confirmed": False, "mechanism": None, "param": None}


def _try_ssr_india_filter(url: str, base_links: set) -> Optional[str]:
    if not base_links:
        return None
    for param, value in [("country", "IN"), ("location", "India"), ("country", "India")]:
        try:
            probed = probe_endpoint(_url_with_params(url, {param: value}))
            probed_links = _extract_job_links(probed.text_body)
        except Exception:
            continue
        if probed_links and probed_links != base_links:
            return f"query param {param}={value}"
    return None


@traced
def investigate(state: AgentState) -> AgentState:
    evidence = state["evidence"]
    careers_url = evidence.careers_url
    domain = state["domain"]

    fetch_result = fetch_url(careers_url)
    api_candidates = _find_json_api_candidates(fetch_result.network_requests)

    findings: dict[str, Any] = {"careers_url": careers_url, "html_length": len(fetch_result.html)}

    get_candidates = [c for c in api_candidates if c["method"].upper() == "GET"]

    if get_candidates:
        best = max(get_candidates, key=lambda c: c["job_count"])
        pagination = _try_pagination_params(best["url"])
        india_filter = _try_india_filter(best["url"])
        findings["api_candidate"] = {
            "url": best["url"],
            "job_count_sample": best["job_count"],
            "pagination": pagination,
            "india_filter_mechanism": india_filter,
            "sample_record": best["sample"],
        }
        evidence.source_type = SourceType.REST_API
        evidence.pagination_param_confirmed = pagination["confirmed"]
        evidence.pagination_mechanism = pagination["mechanism"]
        evidence.india_filter_mechanism = india_filter or "client_side_fallback"
    elif api_candidates:
        # Found a JSON job API, but only via POST/GraphQL — our query-param
        # probing can't safely replay a POST body, so pagination stays
        # unconfirmed rather than guessed. Evidence gate will loop back to
        # investigate again, or the run honestly exhausts its budget.
        findings["api_candidate_unconfirmed"] = api_candidates[0]
        evidence.source_type = SourceType.GRAPHQL if "graphql" in api_candidates[0]["url"].lower() else SourceType.REST_API
        evidence.pagination_param_confirmed = False
    else:
        job_links = _extract_job_links(fetch_result.html)
        if job_links:
            pagination = _try_ssr_pagination(careers_url, job_links)
            india_filter = _try_ssr_india_filter(careers_url, job_links)
            findings["ssr_job_link_count"] = len(job_links)
            findings["ssr_pagination"] = pagination
            evidence.source_type = SourceType.SSR_HTML
            evidence.pagination_param_confirmed = pagination["confirmed"]
            evidence.pagination_mechanism = pagination["mechanism"]
            evidence.india_filter_mechanism = india_filter or "client_side_fallback"
        else:
            # No JSON API, no job-shaped links in the raw HTML — genuinely a
            # JS-only SPA with no discoverable API from this pass.
            evidence.source_type = SourceType.SPA_NO_API
            evidence.pagination_param_confirmed = False
            evidence.india_filter_mechanism = "client_side_fallback"

    host = urlparse(careers_url).netloc
    findings["ats_hint"] = next((v for k, v in ATS_HOST_HINTS.items() if k in host), None)

    prompt_template = PROMPT_PATH.read_text(encoding="utf-8")
    prompt = prompt_template.format(careers_url=careers_url, domain=domain)
    prompt += (
        "\n\n## Empirical findings\n```json\n"
        + json.dumps(findings, indent=2, default=str)
        + "\n```\n\nReturn ONLY a JSON object with keys: "
        "source_type (one of ssr_html/embedded_json/rest_api/graphql/spa_no_api/unknown), "
        "reported_total_count (int or null), evidence_notes (string)."
    )

    try:
        response = llm_client.complete(prompt, temperature=0.0)
        parsed = extract_json(response.content)
        if parsed:
            source_type_str = parsed.get("source_type")
            if source_type_str in SourceType._value2member_map_:
                evidence.source_type = SourceType(source_type_str)
            if parsed.get("reported_total_count") is not None:
                try:
                    evidence.reported_total_count = int(parsed["reported_total_count"])
                except (TypeError, ValueError):
                    pass
            evidence.evidence_notes = str(parsed.get("evidence_notes", ""))
    except Exception as exc:
        evidence.evidence_notes = f"LLM interpretation failed: {exc}"

    return state

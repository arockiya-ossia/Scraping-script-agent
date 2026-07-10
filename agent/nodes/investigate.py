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
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

from lxml import html as lxml_html

from agent.llm.client import llm_client
from agent.llm.codeformat import extract_json
from agent.models.evidence import SourceType
from agent.models.network import CapturedRequest, parse_captured_request
from agent.nodes import traced
from agent.state import AgentState
from agent.tools.fetch_url import fetch_url
from agent.tools.probe_endpoint import probe_endpoint
from agent.trace.sink import trace_sink
from config import settings

PROMPT_PATH = Path(__file__).resolve().parent.parent / "llm" / "prompts" / "investigate.md"

JOB_LIST_KEYS = ("title", "jobtitle", "position", "role", "postingtitle")
JOB_ID_KEYS = ("id", "jobid", "requisitionid", "reqid", "postingid")

# Common third-party analytics/consent/tracking domains — never job-listing
# APIs, but their JSON payloads occasionally trip the job-shape heuristic
# below (e.g. a cookie-consent config with a "title"-keyed list). Horizontal
# denylist, not per-company logic (CLAUDE.md §2 #2).
NON_JOB_API_HOST_MARKERS = (
    "onetrust.com",
    "google-analytics.com",
    "doubleclick.net",
    "segment.com",
    "segment.io",
    "clarity.ms",
    "factors.ai",
    "zoominfo.com",
    "linkedin.com",
    "podscribe.com",
    "liadm.com",
    "contentsquare.net",
    "adsrvr.org",
    "framer.com",
    "zi-scripts.com",
    "piwik.pro",
)

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


def _job_shaped_list(candidates: Any) -> Optional[list]:
    if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
        return None
    keys = {k.lower() for k in candidates[0].keys()}
    if any(k in keys for k in JOB_LIST_KEYS) or any(k in keys for k in JOB_ID_KEYS):
        return candidates
    # Relay-style edges: [{"node": {...}}] — unwrap to the actual job dicts.
    if "node" in keys and isinstance(candidates[0].get("node"), dict):
        node_keys = {k.lower() for k in candidates[0]["node"].keys()}
        if any(k in node_keys for k in JOB_LIST_KEYS) or any(k in node_keys for k in JOB_ID_KEYS):
            return [item["node"] for item in candidates if isinstance(item.get("node"), dict)]
    return None


def _looks_like_job_list(payload: Any, _depth: int = 0, _max_depth: int = 4) -> Optional[list]:
    """Heuristic: does this JSON payload contain a list of job-shaped dicts?
    Recurses a bounded depth so GraphQL's typical nesting (data -> field ->
    edges -> [{node: {...}}]) is found, not just REST's flatter shapes.
    """
    if _depth > _max_depth:
        return None
    direct = _job_shaped_list(payload)
    if direct is not None:
        return direct
    if isinstance(payload, dict):
        for value in payload.values():
            found = _looks_like_job_list(value, _depth + 1, _max_depth)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = _looks_like_job_list(item, _depth + 1, _max_depth)
            if found is not None:
                return found
    return None


def _find_json_api_candidates(network_requests: list[dict]) -> list[dict]:
    """Returns CapturedRequest-wrapped candidates for every XHR/fetch call
    whose response looks like a job listing, regardless of GET/POST/GraphQL
    — the method-specific handling happens later in investigate().
    """
    found = []
    for req in network_requests:
        if any(marker in req.get("url", "") for marker in NON_JOB_API_HOST_MARKERS):
            continue
        if not req.get("body"):
            continue
        captured = parse_captured_request(req)
        jobs = _looks_like_job_list(captured.response_json)
        if jobs:
            found.append({"captured": captured, "job_count": len(jobs), "sample": jobs[0]})
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


# --- POST JSON / GraphQL pagination + filter probing -----------------------
#
# Same empirical-confirmation principle as the GET/query-param path above,
# just applied to a request body instead of a URL. Evidence-driven: these
# key-name guesses are generic across countless APIs (offset/limit, page,
# Relay cursors), never tied to one platform (CLAUDE.md §2 #2) — Workday's
# CXS API happens to fall out of the generic offset/limit case, it isn't
# special-cased.

OFFSET_KEYS = {"offset", "skip", "start"}
PAGE_KEYS = {"page", "pagenumber", "pageindex", "pagenum"}
LIMIT_KEYS = {"limit", "pagesize", "take", "size", "count", "first", "perpage", "per_page"}
CURSOR_VAR_KEYS = {"after"}
CURSOR_RESPONSE_KEYS = {"endcursor", "nextcursor", "cursor", "next_cursor"}
INDIA_FILTER_KEY_GUESSES = [
    ("country", "IN"),
    ("country", "India"),
    ("location", "India"),
    ("countryCode", "IN"),
    ("region", "India"),
]


def _find_key_recursive(obj: Any, target_keys: set[str], _depth: int = 0, _max_depth: int = 6) -> Any:
    """Walk a nested dict/list looking for the first key whose name (lower-
    cased) is in target_keys — used to pull a Relay cursor out of a response
    without knowing where the API nests its pageInfo object.
    """
    if _depth > _max_depth:
        return None
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.lower() in target_keys:
                return v
        for v in obj.values():
            found = _find_key_recursive(v, target_keys, _depth + 1, _max_depth)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_key_recursive(item, target_keys, _depth + 1, _max_depth)
            if found is not None:
                return found
    return None


def _mutate_pagination_body(body: dict) -> Optional[tuple[dict, str]]:
    """Advance a POST JSON body's pagination key by one page. Returns
    (mutated_body, mechanism_label) or None if no recognizable key exists.
    """
    limit_val = next((v for k, v in body.items() if k.lower() in LIMIT_KEYS and isinstance(v, (int, float))), None)
    for k, v in body.items():
        if k.lower() in OFFSET_KEYS and isinstance(v, (int, float)):
            return {**body, k: v + (limit_val or 20)}, "offset/limit"
    for k, v in body.items():
        if k.lower() in PAGE_KEYS and isinstance(v, (int, float)):
            return {**body, k: v + 1}, "page number"
    return None


def _mutate_graphql_variables(variables: dict, response_json: Any) -> Optional[tuple[dict, str]]:
    """Same idea as `_mutate_pagination_body`, plus Relay cursor-based
    pagination: if the observed variables include an `after` cursor, pull
    the real `endCursor` out of the observed response and advance to it —
    never fabricate a cursor value.
    """
    for k in variables:
        if k.lower() in CURSOR_VAR_KEYS:
            cursor = _find_key_recursive(response_json, CURSOR_RESPONSE_KEYS)
            if cursor:
                return {**variables, k: cursor}, "cursor"
            return None
    mutated = _mutate_pagination_body(variables)
    return mutated


def _try_json_body_pagination(captured: CapturedRequest) -> dict:
    if captured.json_body is None:
        return {"confirmed": False, "mechanism": None, "param": None}

    base_jobs = _looks_like_job_list(captured.response_json) or []
    if not base_jobs:
        return {"confirmed": False, "mechanism": None, "param": None}

    if captured.is_graphql:
        variables = captured.json_body.get("variables") or {}
        mutation = _mutate_graphql_variables(variables, captured.response_json)
        if mutation is None:
            return {"confirmed": False, "mechanism": None, "param": None}
        mutated_vars, mechanism = mutation
        mutated_body = {**captured.json_body, "variables": mutated_vars}
        param_label = "variables"
    else:
        mutation = _mutate_pagination_body(captured.json_body)
        if mutation is None:
            return {"confirmed": False, "mechanism": None, "param": None}
        mutated_body, mechanism = mutation
        param_label = "body"

    try:
        probed = probe_endpoint(captured.url, method=captured.method, json_body=mutated_body, headers=captured.headers)
        probed_jobs = _looks_like_job_list(probed.json_body) or []
    except Exception:
        return {"confirmed": False, "mechanism": None, "param": None}

    base_ids = {json.dumps(j, sort_keys=True, default=str) for j in base_jobs}
    probed_ids = {json.dumps(j, sort_keys=True, default=str) for j in probed_jobs}
    if probed_jobs and base_ids != probed_ids:
        return {"confirmed": True, "mechanism": mechanism, "param": param_label}
    if not probed_jobs:
        return {"confirmed": True, "mechanism": "single_page", "param": None}
    return {"confirmed": False, "mechanism": None, "param": None}


def _try_json_body_india_filter(captured: CapturedRequest) -> Optional[str]:
    if captured.json_body is None:
        return None
    base_jobs = _looks_like_job_list(captured.response_json) or []
    base_ids = {json.dumps(j, sort_keys=True, default=str) for j in base_jobs}

    target_key = "variables" if captured.is_graphql else None
    target = (captured.json_body.get("variables") or {}) if captured.is_graphql else captured.json_body

    for key, value in INDIA_FILTER_KEY_GUESSES:
        mutated_target = {**target, key: value}
        mutated_body = {**captured.json_body, target_key: mutated_target} if target_key else mutated_target
        try:
            probed = probe_endpoint(captured.url, method=captured.method, json_body=mutated_body, headers=captured.headers)
            probed_jobs = _looks_like_job_list(probed.json_body)
        except Exception:
            continue
        if probed_jobs is None:
            continue
        probed_ids = {json.dumps(j, sort_keys=True, default=str) for j in probed_jobs}
        if probed_ids != base_ids:
            return f"body param {key}={value}"
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


STRIP_TAGS = ("script", "style", "svg", "noscript", "link", "meta")


def _clean_html_sample(html_text: str, max_chars: int = 6000) -> str:
    """Strip script/style/etc noise so a real HTML sample fits in an LLM
    prompt — this is what lets generate_script write CSS selectors against
    actual markup instead of guessing from training-data memory of what a
    given ATS 'usually' looks like.
    """
    try:
        tree = lxml_html.fromstring(html_text)
    except Exception:
        return html_text[:max_chars]
    for el in tree.xpath(" | ".join(f"//{tag}" for tag in STRIP_TAGS)):
        parent = el.getparent()
        if parent is not None:
            parent.remove(el)
    try:
        cleaned = lxml_html.tostring(tree, pretty_print=True, encoding="unicode")
    except Exception:
        cleaned = html_text
    return cleaned[:max_chars]


def _sample_job_link_html(html_text: str, max_items: int = 5, max_chars: int = 6000) -> str:
    """Serialize the actual matched job-link anchors (plus one level of
    parent context) rather than a blind head-of-page truncation — a large
    page's job listing is often far below the fold, so truncating from the
    top can hand the LLM zero real job markup and force it to hallucinate a
    URL/selector pattern from training-data memory instead.
    """
    try:
        tree = lxml_html.fromstring(html_text)
    except Exception:
        return html_text[:max_chars]
    anchors = tree.xpath("//a[@href]")
    matched = [a for a in anchors if any(marker in (a.get("href") or "").lower() for marker in JOB_LINK_MARKERS)]
    snippets = []
    for a in matched[:max_items]:
        container = a.getparent() if a.getparent() is not None else a
        try:
            snippets.append(lxml_html.tostring(container, pretty_print=True, encoding="unicode"))
        except Exception:
            continue
    combined = "\n\n---\n\n".join(snippets)
    return combined[:max_chars] if combined else _clean_html_sample(html_text, max_chars)


def _evidence_confidence(evidence) -> float:
    """A heuristic confidence score for the trace deliverable — NOT used
    for any routing decision (evidence.is_sufficient() remains the sole,
    deterministic gate per CLAUDE.md §6.2). Purely descriptive.
    """
    if not evidence.pagination_param_confirmed:
        return 0.3
    if evidence.india_filter_mechanism and evidence.india_filter_mechanism != "client_side_fallback":
        return 0.9
    return 0.6


def _write_evidence_sample(domain: str, sample_text: str) -> str:
    artifacts_dir = settings.artifacts_dir / domain
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    sample_path = artifacts_dir / "evidence_sample.txt"
    sample_path.write_text(sample_text or "(no concrete sample captured)", encoding="utf-8")
    return str(sample_path)


def _classify_candidates(fetch_result, careers_url: str, domain: str, run_id: str, findings: dict, evidence) -> Optional[str]:
    """Attempts REST/GraphQL/SSR classification against one fetch_result.
    Mutates `evidence` and `findings` in place. Returns the sample_text on
    success, or None if nothing job-shaped was found at all — the caller
    can then decide whether an interactive retry is worth attempting before
    settling for SPA_NO_API.
    """
    api_candidates = _find_json_api_candidates(fetch_result.network_requests)
    findings["html_length"] = len(fetch_result.html)

    # Pick the single best candidate by job count *across* GET and POST/
    # GraphQL together — a GET request that happens to return one or two
    # job-shaped-looking metadata records (e.g. a page's "About Us" sidebar
    # content with a "title" key) must not out-rank a POST endpoint that
    # actually returned the real 20-job listing.
    best_overall = max(api_candidates, key=lambda c: c["job_count"]) if api_candidates else None
    sample_text = ""

    if best_overall and best_overall["captured"].method == "GET":
        best = best_overall
        best_url = best["captured"].url
        trace_sink.emit(
            domain, run_id, type="tool_call", node="investigate", tool="probe_endpoint",
            input={"url": best_url, "purpose": "pagination + india-filter probing"},
        )
        pagination = _try_pagination_params(best_url)
        india_filter = _try_india_filter(best_url)
        trace_sink.emit(
            domain, run_id, type="tool_result", node="investigate", tool="probe_endpoint",
            pagination=pagination, india_filter_mechanism=india_filter,
        )
        findings["api_candidate"] = {
            "url": best_url,
            "job_count_sample": best["job_count"],
            "pagination": pagination,
            "india_filter_mechanism": india_filter,
            "sample_record": best["sample"],
        }
        evidence.source_type = SourceType.REST_API
        evidence.pagination_param_confirmed = pagination["confirmed"]
        evidence.pagination_mechanism = pagination["mechanism"]
        evidence.india_filter_mechanism = india_filter or "client_side_fallback"
        sample_text = (
            f"Real API endpoint: {best_url}\n\n"
            "Sample JSON record actually returned by this endpoint "
            "(write jmespath/dict access against THIS shape, not a guessed one):\n"
            + json.dumps(best["sample"], indent=2, default=str)
        )
        trace_sink.emit(
            domain, run_id, type="decision", node="investigate", action="classify_rest_api",
            rationale=f"A GET request to {best_url} returned a JSON array of {best['job_count']} job-shaped records.",
        )
        return sample_text
    elif best_overall:
        # A JSON job API, but reached via POST or GraphQL — mutate the
        # observed body's pagination-shaped keys (or Relay cursor) and
        # replay it, same empirical-confirmation principle as the GET path,
        # just against a body instead of a URL (CLAUDE.md §6.2).
        best = best_overall
        captured: CapturedRequest = best["captured"]
        trace_sink.emit(
            domain, run_id, type="tool_call", node="investigate", tool="probe_endpoint",
            input={
                "url": captured.url, "method": captured.method,
                "purpose": "POST/GraphQL pagination + india-filter probing",
                "observed_body": captured.json_body,
            },
        )
        pagination = _try_json_body_pagination(captured)
        india_filter = _try_json_body_india_filter(captured)
        trace_sink.emit(
            domain, run_id, type="tool_result", node="investigate", tool="probe_endpoint",
            pagination=pagination, india_filter_mechanism=india_filter,
        )
        findings["api_candidate"] = {
            "url": captured.url,
            "method": captured.method,
            "is_graphql": captured.is_graphql,
            "job_count_sample": best["job_count"],
            "pagination": pagination,
            "india_filter_mechanism": india_filter,
            "sample_record": best["sample"],
        }
        evidence.source_type = SourceType.GRAPHQL if captured.is_graphql else SourceType.REST_API
        evidence.pagination_param_confirmed = pagination["confirmed"]
        evidence.pagination_mechanism = pagination["mechanism"]
        evidence.india_filter_mechanism = india_filter or "client_side_fallback"
        sample_text = (
            f"Real {'GraphQL' if captured.is_graphql else 'POST'} API endpoint: {captured.url}\n\n"
            f"Observed request body:\n{json.dumps(captured.json_body, indent=2, default=str)}\n\n"
            "Sample JSON record actually returned by this endpoint "
            "(write jmespath/dict access against THIS shape, not a guessed one):\n"
            + json.dumps(best["sample"], indent=2, default=str)
        )
        trace_sink.emit(
            domain, run_id, type="decision", node="investigate",
            action="classify_graphql_api" if captured.is_graphql else "classify_post_api",
            rationale=(
                f"A {captured.method} request to {captured.url} returned a JSON array of "
                f"{best['job_count']} job-shaped records; pagination "
                f"{'confirmed via ' + pagination['mechanism'] if pagination['confirmed'] else 'could not be confirmed'} "
                "by mutating the observed body."
            ),
        )
        return sample_text
    else:
        job_links = _extract_job_links(fetch_result.html)
        if job_links:
            trace_sink.emit(
                domain, run_id, type="tool_call", node="investigate", tool="probe_endpoint",
                input={"url": careers_url, "purpose": "SSR pagination + india-filter probing"},
            )
            pagination = _try_ssr_pagination(careers_url, job_links)
            india_filter = _try_ssr_india_filter(careers_url, job_links)
            trace_sink.emit(
                domain, run_id, type="tool_result", node="investigate", tool="probe_endpoint",
                pagination=pagination, india_filter_mechanism=india_filter,
            )
            findings["ssr_job_link_count"] = len(job_links)
            findings["ssr_pagination"] = pagination
            evidence.source_type = SourceType.SSR_HTML
            evidence.pagination_param_confirmed = pagination["confirmed"]
            evidence.pagination_mechanism = pagination["mechanism"]
            evidence.india_filter_mechanism = india_filter or "client_side_fallback"

            listing_sample = _sample_job_link_html(fetch_result.html)
            sample_text = (
                "Real job-link anchor elements found on the listing page — the actual "
                "href pattern and surrounding markup to write selectors against:\n" + listing_sample
            )

            try:
                detail_url = urljoin(careers_url, next(iter(job_links)))
                detail_probe = probe_endpoint(detail_url)
                detail_clean = _clean_html_sample(detail_probe.text_body)
                sample_text += "\n\nCleaned real sample job-detail-page HTML:\n" + detail_clean
            except Exception:
                pass

            trace_sink.emit(
                domain, run_id, type="decision", node="investigate", action="classify_ssr_html",
                rationale=f"No JSON API found, but {len(job_links)} job-shaped anchor links were in the raw HTML.",
            )
            return sample_text
        return None


@traced
def investigate(state: AgentState) -> AgentState:
    evidence = state["evidence"]
    careers_url = evidence.careers_url
    domain = state["domain"]
    run_id = state.get("run_id", "run")

    trace_sink.emit(domain, run_id, type="tool_call", node="investigate", tool="fetch_url", input={"url": careers_url})
    fetch_result = fetch_url(careers_url)
    html_artifact = trace_sink.save_artifact(domain, "page.html", fetch_result.html)
    trace_sink.emit(
        domain, run_id, type="tool_result", node="investigate", tool="fetch_url",
        status=fetch_result.status, network_request_count=len(fetch_result.network_requests),
        artifact_ref=html_artifact,
    )

    findings: dict[str, Any] = {"careers_url": careers_url}
    sample_text = _classify_candidates(fetch_result, careers_url, domain, run_id, findings, evidence)

    if not evidence.is_sufficient():
        # Passive load didn't produce enough evidence — either nothing
        # job-shaped was found, or something was found but pagination
        # couldn't be confirmed (e.g. only nav-junk links matched the
        # job-keyword heuristic). Try a bounded set of generic browser
        # interactions (search/view-jobs click, blank search + Enter,
        # scroll) before giving up. This is the swissre.com-style gap: some
        # ATS portals only fire the real job-listing XHR after an explicit
        # user action.
        trace_sink.emit(
            domain, run_id, type="decision", node="investigate", action="attempt_browser_interactions",
            rationale=(
                f"Evidence still insufficient after passive load (source_type="
                f"{evidence.source_type.value if evidence.source_type else None}, "
                f"pagination_confirmed={evidence.pagination_param_confirmed})."
            ),
        )
        trace_sink.emit(
            domain, run_id, type="tool_call", node="investigate", tool="fetch_url",
            input={"url": careers_url, "interact": True},
        )
        interactive_result = fetch_url(careers_url, interact=True)
        interactive_artifact = trace_sink.save_artifact(domain, "page_interactive.html", interactive_result.html)
        trace_sink.emit(
            domain, run_id, type="tool_result", node="investigate", tool="fetch_url",
            status=interactive_result.status, network_request_count=len(interactive_result.network_requests),
            interactions=interactive_result.interactions, artifact_ref=interactive_artifact,
        )
        fetch_result = interactive_result
        findings["interactions_attempted"] = interactive_result.interactions
        retried_sample_text = _classify_candidates(fetch_result, careers_url, domain, run_id, findings, evidence)
        if retried_sample_text is not None:
            sample_text = retried_sample_text

        if sample_text is None:
            evidence.source_type = SourceType.SPA_NO_API
            evidence.pagination_param_confirmed = False
            evidence.india_filter_mechanism = "client_side_fallback"
            trace_sink.emit(
                domain, run_id, type="decision", node="investigate", action="classify_spa_no_api",
                rationale=(
                    "No JSON API and no job-shaped links found even after "
                    f"{len(interactive_result.interactions)} bounded browser interaction(s)."
                ),
            )

    state["evidence_sample_path"] = _write_evidence_sample(domain, sample_text or "")

    host = urlparse(careers_url).netloc
    findings["ats_hint"] = next((v for k, v in ATS_HOST_HINTS.items() if k in host), None)

    prompt_template = PROMPT_PATH.read_text(encoding="utf-8")
    prompt = prompt_template.format(careers_url=careers_url, domain=domain)
    prompt += (
        "\n\n## Empirical findings\n```json\n"
        + json.dumps(findings, indent=2, default=str)
        + "\n```\n\nReturn ONLY a JSON object (no other text) with keys: "
        "source_type (one of ssr_html/embedded_json/rest_api/graphql/spa_no_api/unknown), "
        "reported_total_count (int or null), evidence_notes (string, your reasoning)."
    )

    trace_sink.emit(domain, run_id, type="tool_call", node="investigate", tool="llm_client.complete", input={"findings": findings})
    try:
        response = llm_client.complete(prompt, temperature=0.0)
        trace_sink.emit(
            domain, run_id, type="tool_result", node="investigate", tool="llm_client.complete",
            tokens_prompt=response.tokens_prompt, tokens_completion=response.tokens_completion,
        )
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
        trace_sink.emit(domain, run_id, type="tool_result", node="investigate", tool="llm_client.complete", error=str(exc))

    trace_sink.emit(
        domain, run_id, type="evidence", node="investigate",
        source_type=evidence.source_type.value if evidence.source_type else None,
        endpoint=careers_url,
        pagination_mechanism=evidence.pagination_mechanism,
        pagination_param_confirmed=evidence.pagination_param_confirmed,
        india_filter_mechanism=evidence.india_filter_mechanism,
        reported_total_count=evidence.reported_total_count,
        sufficient=evidence.is_sufficient(),
        confidence=_evidence_confidence(evidence),
        notes=evidence.evidence_notes,
    )

    return state

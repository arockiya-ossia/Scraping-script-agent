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
from agent.memory.store import compute_site_signature, memory_store, url_structure_shape
from agent.models.evidence import SourceType
from agent.models.network import CapturedRequest, parse_captured_request
from agent.models.validation import FailureCategory
from agent.nodes import traced
from agent.nodes.discover import ATS_DOMAIN_MARKERS, _find_ats_link
from agent.state import AgentState
from agent.tools.fetch_url import fetch_url
from agent.tools.firecrawl_client import get_firecrawl_client
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


def _try_pagination_params(url: str, hint_param: Optional[str] = None) -> dict:
    """Empirically test common pagination param names — confirmed only if
    the result set actually changes, never guessed (CLAUDE.md §6.2).

    `hint_param` (from cross-run memory, §12 stretch goal) just reorders
    which trial runs first — a prior guess to check first, never a
    substitute for the empirical diff below. A stale/wrong hint simply
    misses and the rest of the trial list runs exactly as before.
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
    if hint_param:
        trials.sort(key=lambda t: t[0] != hint_param)
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


# Generic navigational/site-furniture words that show up in category and
# hub-page slugs ("UK-Career-Site", "Job-List") as often as in real job
# titles' surrounding text — a slug built almost entirely out of these
# isn't a job title even if it clears the word-count bar.
GENERIC_SLUG_WORDS = {
    "site", "career", "careers", "job", "jobs", "search", "list", "home",
    "about", "contact", "apply", "index", "hub", "portal", "center", "centre",
    "landing", "page", "overview", "programme", "program", "starters", "all",
}


def _looks_like_job_slug(href: str, min_words: int = 3) -> bool:
    """A real job posting's URL slug is almost always a multi-word,
    hyphenated job title ("senior-software-engineer") or a GUID (which also
    splits into several hyphen-separated segments) — unlike a navigation/
    category link's short slug ("emea", "search-jobs", "graduates"). Sites
    that reuse the same URL pattern for both job postings and category
    pages (e.g. Swiss Re's "/go/{slug}/{id}/") can't be told apart by
    keyword or word-count alone: "UK-Career-Site" is 3 words but still not
    a job title, since two of those words are generic site furniture — a
    real job title has at least 2 specific, non-generic words.
    """
    path = urlparse(href).path.rstrip("/")
    segments = [s for s in path.split("/") if s]
    if not segments:
        return False
    # the ID is often its own trailing numeric segment — look at the slug
    # segment before it, not the bare number.
    slug = segments[-2] if segments[-1].isdigit() and len(segments) >= 2 else segments[-1]
    words = [w for w in slug.replace("_", "-").split("-") if w]

    # Some platforms (Greenhouse: "/{company}/jobs/{id}") don't encode a
    # descriptive title in the URL at all — the segment before the ID is
    # just the fixed, generic path literal "jobs", identical on every
    # posting. There's no distinguishing text to judge there, so a lone
    # generic word is accepted by default rather than rejected — unlike
    # Swiss Re's "/go/{slug}/{id}/", where that segment actually varies per
    # link and is what encodes real vs. navigational content.
    if len(words) == 1 and words[0].lower() in GENERIC_SLUG_WORDS:
        return True

    if len(words) < min_words:
        return False
    non_generic = [w for w in words if w.lower() not in GENERIC_SLUG_WORDS]
    return len(non_generic) >= 2


def _extract_job_links(html_text: str) -> set:
    """Generic heuristic for SSR job-listing pages: anchors whose href looks
    job-posting-shaped. Not a per-domain selector — the same marker list
    and slug-shape rule apply to any site (CLAUDE.md §2 #2 allows
    horizontal patterns). Requires BOTH a marker keyword match AND a
    job-title-shaped slug — the marker alone lets navigation/category pages
    through when their own slug happens to contain a job-related word.
    """
    try:
        tree = lxml_html.fromstring(html_text)
    except Exception:
        return set()
    hrefs = tree.xpath("//a/@href")
    return {
        h for h in hrefs
        if any(marker in h.lower() for marker in JOB_LINK_MARKERS) and _looks_like_job_slug(h)
    }


def _try_ssr_pagination(url: str, base_links: set, hint_param: Optional[str] = None) -> dict:
    """Empirically test common HTML pagination query params. A page-2 fetch
    that returns a *different* link set confirms multi-page pagination; one
    that returns an *empty* set confirms all jobs already fit on one page.
    A third, equally valid outcome: the same *non-empty, unchanged* set on
    every param variant — many single-listing ATS pages (e.g. Lever) simply
    ignore unrecognized query params and always return the full listing,
    rather than erroring or emptying out.

    `hint_param` (cross-run memory, §12) just reorders the trial list.
    """
    if not base_links:
        return {"confirmed": False, "mechanism": None, "param": None}
    any_success = False
    always_same = True
    param_order = ("page", "p", "pg")
    if hint_param in param_order:
        param_order = (hint_param,) + tuple(p for p in param_order if p != hint_param)
    for param in param_order:
        try:
            probed = probe_endpoint(_url_with_params(url, {param: 2}))
            probed_links = _extract_job_links(probed.text_body)
        except Exception:
            continue
        any_success = True
        if probed_links and probed_links != base_links:
            return {"confirmed": True, "mechanism": "page number", "param": param}
        if not probed_links:
            return {"confirmed": True, "mechanism": "single_page", "param": None}
        if probed_links != base_links:
            always_same = False
    if any_success and always_same:
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
    matched = [
        a for a in anchors
        if any(marker in (a.get("href") or "").lower() for marker in JOB_LINK_MARKERS)
        and _looks_like_job_slug(a.get("href") or "")
    ]
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


def _fetch_via_firecrawl(url: str, domain: str, run_id: str):
    """Escalation fetch — Playwright already failed (HTTP_FORBIDDEN) or is
    about to be given up on (zero DOM nodes after bounded interactions).
    Returns a fetch_url.FetchResult-shaped object so _classify_candidates
    can use it uniformly; network_requests is empty since Firecrawl doesn't
    expose XHR capture — this path degrades to SSR/link classification.
    """
    from agent.tools.fetch_url import FetchResult

    trace_sink.emit(domain, run_id, type="tool_call", node="investigate", tool="firecrawl.scrape", input={"url": url})
    try:
        client = get_firecrawl_client()
        result = client.scrape(url, formats=["html", "links"])
    except Exception as exc:
        trace_sink.emit(domain, run_id, type="tool_result", node="investigate", tool="firecrawl.scrape", error=str(exc))
        return FetchResult(url=url, status=0, html="", network_requests=[])

    artifact = trace_sink.save_artifact(domain, "page_firecrawl.html", result.html or "")
    trace_sink.emit(
        domain, run_id, type="tool_result", node="investigate", tool="firecrawl.scrape",
        success=result.success, status=result.status_code, credits_used=result.credits_used,
        artifact_ref=artifact,
    )
    status = result.status_code or (200 if result.success else 0)
    return FetchResult(url=url, status=status, html=result.html or "", network_requests=[])


def _try_firecrawl_actions(url: str, domain: str, run_id: str):
    """One richer-interaction attempt via Firecrawl Actions after
    fetch_url's bounded Playwright interactions still found zero job DOM
    nodes — a longer, more deliberate click/scroll sequence than the
    generic budget-capped Playwright pass allows.
    """
    from agent.tools.fetch_url import FetchResult

    actions = [
        {"type": "wait", "milliseconds": 2000},
        {"type": "click", "selector": "button, a"},
        {"type": "wait", "milliseconds": 1500},
        {"type": "scroll", "direction": "down"},
        {"type": "wait", "milliseconds": 1000},
        {"type": "scroll", "direction": "down"},
        {"type": "wait", "milliseconds": 1000},
    ]
    trace_sink.emit(
        domain, run_id, type="tool_call", node="investigate", tool="firecrawl.scrape",
        input={"url": url, "actions": actions, "purpose": "zero-DOM-nodes escalation"},
    )
    try:
        client = get_firecrawl_client()
        result = client.scrape(url, formats=["html", "links"], actions=actions)
    except Exception as exc:
        trace_sink.emit(domain, run_id, type="tool_result", node="investigate", tool="firecrawl.scrape", error=str(exc))
        return FetchResult(url=url, status=0, html="", network_requests=[])

    artifact = trace_sink.save_artifact(domain, "page_firecrawl_actions.html", result.html or "")
    trace_sink.emit(
        domain, run_id, type="tool_result", node="investigate", tool="firecrawl.scrape",
        success=result.success, status=result.status_code, credits_used=result.credits_used,
        artifact_ref=artifact,
    )
    status = result.status_code or (200 if result.success else 0)
    return FetchResult(url=url, status=status, html=result.html or "", network_requests=[])


def _classify_pdf_source(url: str, domain: str, run_id: str, findings: dict, evidence) -> Optional[str]:
    """The careers URL is a PDF — route to Firecrawl's native PDF parsing
    rather than failing outright. A PDF is structurally a single static
    document (not a paginated listing), so `pagination_mechanism=single_page`
    here is a deterministic fact about what a PDF *is*, not a guess —
    `pagination_param_confirmed` still only ever reflects something actually
    established, never invented (CLAUDE.md §6.2).
    """
    trace_sink.emit(
        domain, run_id, type="tool_call", node="investigate", tool="firecrawl.scrape",
        input={"url": url, "purpose": "PDF parsing"},
    )
    try:
        client = get_firecrawl_client()
        result = client.scrape(url, formats=["markdown"])
    except Exception as exc:
        trace_sink.emit(domain, run_id, type="tool_result", node="investigate", tool="firecrawl.scrape", error=str(exc))
        return None

    artifact = trace_sink.save_artifact(domain, "pdf_extracted.md", result.markdown or "")
    trace_sink.emit(
        domain, run_id, type="tool_result", node="investigate", tool="firecrawl.scrape",
        success=result.success, credits_used=result.credits_used, artifact_ref=artifact,
    )

    if not result.success or not result.markdown or len(result.markdown.strip()) < 100:
        return None

    findings["pdf_extracted_chars"] = len(result.markdown)
    evidence.source_type = SourceType.SSR_HTML
    evidence.pagination_param_confirmed = True
    evidence.pagination_mechanism = "single_page"
    evidence.india_filter_mechanism = "client_side_fallback"
    return (
        "PDF SOURCE — the careers page is a PDF document, extracted to text via Firecrawl "
        "during investigation only. The generated scraper must fetch this PDF URL directly "
        "at runtime and parse it with `pypdf` (`PdfReader(BytesIO(resp.content))`, then "
        "`.pages[i].extract_text()`) — never call Firecrawl from the generated script itself, "
        "no browser, no regex. Extracted text sample:\n\n" + result.markdown[:6000]
    )


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
        ats_hint = next((v for k, v in ATS_HOST_HINTS.items() if k in urlparse(careers_url).netloc), None)
        signature = compute_site_signature(ats_hint, best["sample"])
        hint_param = memory_store.suggest_pagination_param(signature)
        if hint_param:
            trace_sink.emit(
                domain, run_id, type="decision", node="investigate", action="use_memory_hint",
                rationale=f"Cross-run memory suggests trying pagination param '{hint_param}' first for this site signature.",
            )
        trace_sink.emit(
            domain, run_id, type="tool_call", node="investigate", tool="probe_endpoint",
            input={"url": best_url, "purpose": "pagination + india-filter probing"},
        )
        pagination = _try_pagination_params(best_url, hint_param=hint_param)
        india_filter = _try_india_filter(best_url)
        findings["site_signature"] = signature
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
            # Playwright executes JavaScript, so fetch_result.html can
            # contain client-rendered content (e.g. a career-site embed
            # widget) that the generated scraper — plain `requests`/`httpx`,
            # no JS engine, per the sandbox's library list — could never
            # replicate. Only trust SSR_HTML if the same job-shaped links
            # actually survive a plain, JS-free HTTP fetch of the same URL.
            try:
                plain = probe_endpoint(careers_url)
                plain_links = _extract_job_links(plain.text_body)
            except Exception:
                plain_links = set()

            if not (job_links & plain_links):
                trace_sink.emit(
                    domain, run_id, type="decision", node="investigate", action="reject_js_rendered_ssr",
                    rationale=(
                        f"{len(job_links)} job-shaped link(s) appeared only after JavaScript "
                        "execution — none survive a plain HTTP fetch of the same URL, so a "
                        "requests-based scraper could never see them."
                    ),
                )
                return None

            ats_hint = next((v for k, v in ATS_HOST_HINTS.items() if k in urlparse(careers_url).netloc), None)
            signature = compute_site_signature(ats_hint, url_structure_shape(job_links))
            hint_param = memory_store.suggest_pagination_param(signature)
            if hint_param:
                trace_sink.emit(
                    domain, run_id, type="decision", node="investigate", action="use_memory_hint",
                    rationale=f"Cross-run memory suggests trying pagination param '{hint_param}' first for this site signature.",
                )
            trace_sink.emit(
                domain, run_id, type="tool_call", node="investigate", tool="probe_endpoint",
                input={"url": careers_url, "purpose": "SSR pagination + india-filter probing"},
            )
            pagination = _try_ssr_pagination(careers_url, job_links, hint_param=hint_param)
            india_filter = _try_ssr_india_filter(careers_url, job_links)
            findings["site_signature"] = signature
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
    findings: dict[str, Any] = {"careers_url": careers_url}

    # Escalation: the careers URL is a PDF, not a web page — route to
    # Firecrawl's native PDF parsing instead of failing outright. Checked
    # before anything else since Playwright/lxml have nothing useful to do
    # with a PDF response.
    try:
        head_probe = probe_endpoint(careers_url, method="HEAD", timeout=15.0)
        content_type = (head_probe.content_type or "").lower()
    except Exception:
        content_type = ""

    if "application/pdf" in content_type:
        trace_sink.emit(
            domain, run_id, type="decision", node="investigate", action="route_to_firecrawl_pdf",
            rationale=f"{careers_url} resolves to a PDF (content-type={content_type}).",
        )
        sample_text = _classify_pdf_source(careers_url, domain, run_id, findings, evidence)
        state["evidence_sample_path"] = _write_evidence_sample(domain, sample_text or "")
        return _finish_investigation(state, evidence, findings, careers_url, domain, run_id)

    # Escalation: the previous docker_execute run was blocked with
    # HTTP_FORBIDDEN — retry the primary fetch through Firecrawl (which has
    # its own proxy/stealth handling) before giving up on this domain.
    prior_report = state.get("validation_report")
    retry_after_forbidden = bool(prior_report and prior_report.failure_category == FailureCategory.HTTP_FORBIDDEN)

    if retry_after_forbidden:
        trace_sink.emit(
            domain, run_id, type="decision", node="investigate", action="retry_via_firecrawl_after_403",
            rationale="Previous scraper run was blocked with HTTP_FORBIDDEN — retrying via Firecrawl before giving up.",
        )
        fetch_result = _fetch_via_firecrawl(careers_url, domain, run_id)
    else:
        trace_sink.emit(domain, run_id, type="tool_call", node="investigate", tool="fetch_url", input={"url": careers_url})
        fetch_result = fetch_url(careers_url)
        html_artifact = trace_sink.save_artifact(domain, "page.html", fetch_result.html)
        trace_sink.emit(
            domain, run_id, type="tool_result", node="investigate", tool="fetch_url",
            status=fetch_result.status, network_request_count=len(fetch_result.network_requests),
            artifact_ref=html_artifact,
        )

    sample_text = _classify_candidates(fetch_result, careers_url, domain, run_id, findings, evidence)

    # discover.py's ATS-link check uses a plain HTTP probe (no JS), so it
    # misses a link that only renders after JavaScript execution (e.g. a
    # career-site embed widget) — but investigate.py already has that
    # JS-rendered HTML in hand via Playwright. Follow up to 2 ATS-link hops
    # (marketing page -> a specific job posting -> that posting's "back to
    # all openings" link, which is often shorter/more general than the
    # first link found) before trying anything more expensive.
    #
    # Only when careers_url is NOT already an ATS-hosted page: once we're
    # on job-boards.greenhouse.io/{company}, that page's own footer/nav
    # links (privacy policy, sign-in, regional marketing pages) ALSO live
    # on greenhouse.io and would otherwise match the same marker — hopping
    # to those derails a working job board into the ATS vendor's own
    # corporate site instead of trusting the classification already done.
    already_on_ats_host = any(marker in urlparse(careers_url).netloc for marker in ATS_DOMAIN_MARKERS)
    for _ in range(2 if not already_on_ats_host else 0):
        if evidence.is_sufficient():
            break
        ats_link = _find_ats_link(fetch_result.html, careers_url)
        if not ats_link or ats_link == careers_url:
            break
        trace_sink.emit(
            domain, run_id, type="decision", node="investigate", action="follow_ats_link_from_rendered_html",
            rationale=(
                f"Found a recognized ATS link ({ats_link}) in the JS-rendered page that "
                "discover.py's plain-HTTP probe couldn't have seen."
            ),
        )
        careers_url = ats_link
        evidence.careers_url = ats_link
        trace_sink.emit(domain, run_id, type="tool_call", node="investigate", tool="fetch_url", input={"url": careers_url})
        fetch_result = fetch_url(careers_url)
        ats_artifact = trace_sink.save_artifact(domain, "page_ats.html", fetch_result.html)
        trace_sink.emit(
            domain, run_id, type="tool_result", node="investigate", tool="fetch_url",
            status=fetch_result.status, network_request_count=len(fetch_result.network_requests),
            artifact_ref=ats_artifact,
        )
        findings["careers_url"] = careers_url
        retried_sample_text = _classify_candidates(fetch_result, careers_url, domain, run_id, findings, evidence)
        if retried_sample_text is not None:
            sample_text = retried_sample_text

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

        firecrawl_this_pass = False
        if sample_text is None and not state.get("firecrawl_actions_attempted"):
            # Escalation: bounded Playwright interactions still found zero
            # job DOM nodes — one Firecrawl Actions attempt with a richer,
            # more deliberate interaction sequence before giving up. Capped
            # to once per run (not once per retry) — evidence_check's
            # insufficient-evidence loop re-invokes investigate() from
            # scratch, and the page structure doesn't change between
            # retries, so repeating this would just re-burn a real, paid
            # Firecrawl credit for the same answer every time.
            trace_sink.emit(
                domain, run_id, type="decision", node="investigate", action="attempt_firecrawl_actions",
                rationale="Playwright interactions found zero job DOM nodes — escalating to Firecrawl Actions.",
            )
            firecrawl_this_pass = True
            state["firecrawl_actions_attempted"] = True
            firecrawl_result = _try_firecrawl_actions(careers_url, domain, run_id)
            fetch_result = firecrawl_result
            firecrawl_sample_text = _classify_candidates(fetch_result, careers_url, domain, run_id, findings, evidence)
            if firecrawl_sample_text is not None:
                sample_text = firecrawl_sample_text

        if sample_text is None:
            evidence.source_type = SourceType.SPA_NO_API
            evidence.pagination_param_confirmed = False
            evidence.india_filter_mechanism = "client_side_fallback"
            trace_sink.emit(
                domain, run_id, type="decision", node="investigate", action="classify_spa_no_api",
                rationale=(
                    "No JSON API and no job-shaped links found even after "
                    f"{len(interactive_result.interactions)} bounded browser interaction(s)"
                    + (" and a Firecrawl Actions escalation." if firecrawl_this_pass else " (Firecrawl already exhausted on an earlier attempt this run).")
                ),
            )

    state["evidence_sample_path"] = _write_evidence_sample(domain, sample_text or "")
    return _finish_investigation(state, evidence, findings, careers_url, domain, run_id)


def _finish_investigation(state: AgentState, evidence, findings: dict, careers_url: str, domain: str, run_id: str) -> AgentState:
    """Shared tail: LLM interpretation of gathered findings + final evidence
    trace event. Called both from the PDF early-return path and the normal
    empirical-investigation path.
    """
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

    # Cross-run memory (CLAUDE.md §12): only recorded once evidence is
    # actually sufficient — i.e. pagination_param_confirmed came from a
    # real probe diff, not a guess — so a future run against a different
    # domain on the same platform gets a head start, never a shortcut
    # around empirical confirmation.
    signature = findings.get("site_signature")
    if signature and evidence.is_sufficient():
        api_candidate = findings.get("api_candidate")
        ssr_pagination = findings.get("ssr_pagination")
        if isinstance(api_candidate, dict):
            pagination_param = (api_candidate.get("pagination") or {}).get("param")
        elif isinstance(ssr_pagination, dict):
            pagination_param = ssr_pagination.get("param")
        else:
            pagination_param = None
        memory_store.record(
            signature,
            source_type=evidence.source_type.value if evidence.source_type else None,
            pagination_mechanism=evidence.pagination_mechanism,
            pagination_param=pagination_param,
            india_filter_mechanism=evidence.india_filter_mechanism,
            ats_hint=findings.get("ats_hint"),
        )
        trace_sink.emit(
            domain, run_id, type="decision", node="investigate", action="record_memory",
            rationale=f"Recorded learned pattern for site signature {signature}.",
        )

    # Stagnation detection: if evidence is still insufficient AND every
    # discriminating fact is byte-identical to the *previous* investigate()
    # call's result, this attempt learned nothing new — evidence_check's
    # retry loop would otherwise keep re-confirming the same negative
    # result on every remaining budget slot, spending real LLM tokens for
    # zero new information (observed live: 7 near-identical calls on one
    # domain before the budget ran out).
    fingerprint = "|".join(
        str(x)
        for x in (
            evidence.source_type.value if evidence.source_type else None,
            careers_url,
            evidence.pagination_param_confirmed,
            evidence.pagination_mechanism,
            evidence.india_filter_mechanism,
            evidence.reported_total_count,
        )
    )
    if not evidence.is_sufficient() and fingerprint == state.get("investigation_fingerprint"):
        state["investigation_stagnant"] = True
        trace_sink.emit(
            domain, run_id, type="decision", node="investigate", action="detected_stagnation",
            rationale="This attempt produced the exact same evidence as the previous one — no new information to gain from retrying further.",
        )
    else:
        state["investigation_stagnant"] = False
    state["investigation_fingerprint"] = fingerprint

    return state

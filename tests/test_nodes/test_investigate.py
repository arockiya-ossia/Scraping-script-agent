import json

from agent.models.network import parse_captured_request
from agent.nodes import investigate as inv
from agent.tools.probe_endpoint import ProbeResult


def test_try_ssr_pagination_confirms_single_page_when_response_unchanged(monkeypatch):
    # Lever-style: unrecognized query params are ignored, the full listing
    # is returned every time — must not be mistaken for a failed probe.
    base_links = {
        "https://jobs.lever.co/paytm/senior-backend-engineer",
        "https://jobs.lever.co/paytm/product-marketing-manager",
    }

    def fake_probe(url, method="GET", params=None, json_body=None, headers=None, timeout=20.0):
        html = "".join(f'<a href="{link}">x</a>' for link in base_links)
        return ProbeResult(url=url, status=200, json_body=None, text_body=html)

    monkeypatch.setattr(inv, "probe_endpoint", fake_probe)
    result = inv._try_ssr_pagination("https://jobs.lever.co/paytm", base_links)
    assert result == {"confirmed": True, "mechanism": "single_page", "param": None}


def test_try_ssr_pagination_still_confirms_page_number_when_content_changes(monkeypatch):
    base_links = {"https://example.com/jobs/senior-backend-engineer"}

    def fake_probe(url, method="GET", params=None, json_body=None, headers=None, timeout=20.0):
        html = '<a href="https://example.com/jobs/product-marketing-manager">x</a>'
        return ProbeResult(url=url, status=200, json_body=None, text_body=html)

    monkeypatch.setattr(inv, "probe_endpoint", fake_probe)
    result = inv._try_ssr_pagination("https://example.com/jobs?page=1", base_links)
    assert result["confirmed"] is True
    assert result["mechanism"] == "page number"


def test_looks_like_job_slug_accepts_real_job_urls():
    real_job_urls = [
        "https://jobs.lever.co/paytm/e5145511-6cbd-4168-a5ad-24bc925487db",
        "https://jobs.smartrecruiters.com/Freshworks/744000130041939-si-partner-manager-germany",
        "https://f22labs.zohorecruit.in/jobs/Careers/65449000002129032/Senior-Project-Manager",
        # Greenhouse encodes no descriptive slug at all — the segment
        # before the bare numeric ID is just the fixed literal "jobs" on
        # every posting, nothing to discriminate on, so it's accepted.
        "https://job-boards.greenhouse.io/razorpaysoftwareprivatelimited/jobs/4708904005",
    ]
    for url in real_job_urls:
        assert inv._looks_like_job_slug(url) is True, url


def test_looks_like_job_slug_rejects_nav_category_urls():
    # Swiss Re reuses the same /go/{slug}/{id}/ URL pattern for both real
    # job postings and navigation/category pages — some of these even
    # contain a job-related keyword ("Job-List", "Search-Jobs") and would
    # slip past a pure keyword check.
    nav_urls = [
        "https://careers.swissre.com/go/EMEA/2744201/",
        "https://careers.swissre.com/go/Job-List/5272601/",
        "https://careers.swissre.com/go/Search-Jobs/2744601/",
        "https://careers.swissre.com/go/Careerstarters/2744101/",
    ]
    for url in nav_urls:
        assert inv._looks_like_job_slug(url) is False, url


def test_extract_job_links_excludes_nav_pages_sharing_job_keyword():
    html = (
        '<a href="https://careers.swissre.com/go/Job-List/5272601/">All Jobs</a>'
        '<a href="https://careers.swissre.com/go/Search-Jobs/2744601/">Search jobs</a>'
        '<a href="https://careers.swissre.com/go/Senior-Actuary-Analyst/9988001/">Senior Actuary Analyst</a>'
    )
    links = inv._extract_job_links(html)
    assert links == {"https://careers.swissre.com/go/Senior-Actuary-Analyst/9988001/"}


def test_looks_like_job_list_flat():
    payload = {"jobs": [{"id": 1, "title": "Engineer"}, {"id": 2, "title": "Manager"}]}
    result = inv._looks_like_job_list(payload)
    assert result == payload["jobs"]


def test_looks_like_job_list_unwraps_relay_edges():
    payload = {
        "data": {
            "jobPostings": {
                "edges": [
                    {"node": {"id": "1", "title": "Engineer"}},
                    {"node": {"id": "2", "title": "Manager"}},
                ],
                "pageInfo": {"endCursor": "abc", "hasNextPage": True},
            }
        }
    }
    result = inv._looks_like_job_list(payload)
    assert result == [{"id": "1", "title": "Engineer"}, {"id": "2", "title": "Manager"}]


def test_looks_like_job_list_returns_none_for_non_job_data():
    assert inv._looks_like_job_list({"facets": {"shifttype": [{"name": "x", "count": 1}]}}) is None


def test_find_key_recursive_finds_nested_cursor():
    payload = {"data": {"jobs": {"pageInfo": {"endCursor": "xyz789", "hasNextPage": True}}}}
    assert inv._find_key_recursive(payload, {"endcursor"}) == "xyz789"


def test_find_key_recursive_returns_none_when_absent():
    assert inv._find_key_recursive({"a": {"b": 1}}, {"endcursor"}) is None


def test_mutate_pagination_body_offset():
    mutation = inv._mutate_pagination_body({"offset": 0, "limit": 20})
    assert mutation == ({"offset": 20, "limit": 20}, "offset/limit")


def test_mutate_pagination_body_page():
    mutation = inv._mutate_pagination_body({"page": 1, "pageSize": 10})
    assert mutation == ({"page": 2, "pageSize": 10}, "page number")


def test_mutate_pagination_body_no_recognizable_key():
    assert inv._mutate_pagination_body({"query": "engineer"}) is None


def test_mutate_graphql_variables_cursor_uses_real_response_cursor():
    variables = {"first": 20, "after": None}
    response = {"data": {"jobs": {"pageInfo": {"endCursor": "cursor123"}}}}
    mutation = inv._mutate_graphql_variables(variables, response)
    assert mutation == ({"first": 20, "after": "cursor123"}, "cursor")


def test_mutate_graphql_variables_cursor_key_present_but_no_cursor_in_response_returns_none():
    # Must never fabricate a cursor value.
    variables = {"first": 20, "after": None}
    response = {"data": {"jobs": []}}
    assert inv._mutate_graphql_variables(variables, response) is None


def test_mutate_graphql_variables_falls_back_to_offset():
    variables = {"offset": 0, "limit": 20}
    assert inv._mutate_graphql_variables(variables, {}) == ({"offset": 20, "limit": 20}, "offset/limit")


def _make_captured(url, method, request_body, response_body):
    raw = {"url": url, "method": method, "status": 200, "request_body": json.dumps(request_body), "body": json.dumps(response_body)}
    return parse_captured_request(raw)


def test_try_json_body_pagination_confirms_post_offset_limit(monkeypatch):
    url = "https://example.com/api/search"
    captured = _make_captured(
        url, "POST",
        {"offset": 0, "limit": 2},
        {"jobs": [{"id": 1, "title": "A"}, {"id": 2, "title": "B"}]},
    )

    def fake_probe(probe_url, method="GET", params=None, json_body=None, headers=None, timeout=20.0):
        assert json_body == {"offset": 2, "limit": 2}
        payload = {"jobs": [{"id": 3, "title": "C"}, {"id": 4, "title": "D"}]}
        return ProbeResult(url=probe_url, status=200, json_body=payload, text_body=json.dumps(payload))

    monkeypatch.setattr(inv, "probe_endpoint", fake_probe)
    result = inv._try_json_body_pagination(captured)
    assert result == {"confirmed": True, "mechanism": "offset/limit", "param": "body"}


def test_try_json_body_pagination_graphql_cursor(monkeypatch):
    url = "https://example.com/graphql"
    request_body = {"query": "query Jobs($first:Int,$after:String){jobs(first:$first,after:$after){edges{node{id title}} pageInfo{endCursor}}}", "variables": {"first": 2, "after": None}}
    response_body = {
        "data": {
            "jobs": {
                "edges": [{"node": {"id": "1", "title": "A"}}, {"node": {"id": "2", "title": "B"}}],
                "pageInfo": {"endCursor": "cursor-abc"},
            }
        }
    }
    captured = _make_captured(url, "POST", request_body, response_body)
    assert captured.is_graphql is True

    def fake_probe(probe_url, method="GET", params=None, json_body=None, headers=None, timeout=20.0):
        assert json_body["variables"] == {"first": 2, "after": "cursor-abc"}
        payload = {"data": {"jobs": {"edges": [{"node": {"id": "3", "title": "C"}}], "pageInfo": {"endCursor": None}}}}
        return ProbeResult(url=probe_url, status=200, json_body=payload, text_body=json.dumps(payload))

    monkeypatch.setattr(inv, "probe_endpoint", fake_probe)
    result = inv._try_json_body_pagination(captured)
    assert result == {"confirmed": True, "mechanism": "cursor", "param": "variables"}


def test_try_json_body_india_filter_finds_working_param(monkeypatch):
    url = "https://example.com/api/search"
    captured = _make_captured(
        url, "POST",
        {"offset": 0, "limit": 20},
        {"jobs": [{"id": 1, "title": "A"}, {"id": 2, "title": "B"}]},
    )

    def fake_probe(probe_url, method="GET", params=None, json_body=None, headers=None, timeout=20.0):
        if json_body.get("country") == "IN":
            payload = {"jobs": [{"id": 1, "title": "A"}]}
        else:
            payload = {"jobs": [{"id": 1, "title": "A"}, {"id": 2, "title": "B"}]}
        return ProbeResult(url=probe_url, status=200, json_body=payload, text_body=json.dumps(payload))

    monkeypatch.setattr(inv, "probe_endpoint", fake_probe)
    result = inv._try_json_body_india_filter(captured)
    assert result == "body param country=IN"


def test_ats_hop_skipped_when_already_on_an_ats_hosted_page(monkeypatch):
    """discover.py already resolved the careers URL to a real ATS job board
    (e.g. job-boards.greenhouse.io/{company}) — that page's own footer/nav
    links (privacy policy, sign-in, regional marketing pages) also live on
    greenhouse.io and would otherwise match the same ATS marker, hopping a
    working job board into the ATS vendor's own corporate site instead of
    trusting the classification already done on the real page.
    """
    from agent.models.evidence import InvestigationEvidence
    from agent.tools.fetch_url import FetchResult

    ats_link_calls = []

    def fake_fetch_url(url, wait_ms=3000, capture_network=True, interact=False, **kwargs):
        return FetchResult(url=url, status=200, html="<html>a real job board page</html>", network_requests=[], interactions=[])

    def fake_probe(url, method="GET", params=None, json_body=None, headers=None, timeout=20.0):
        return ProbeResult(url=url, status=200, json_body=None, text_body="<html></html>", content_type="text/html")

    def fake_find_ats_link(html_text, base_url):
        ats_link_calls.append(base_url)
        return "https://my.greenhouse.io/users/sign_in"  # a real ATS-vendor link, but the WRONG one

    class FakeLLMResponse:
        content = '{"source_type": "unknown", "reported_total_count": null, "evidence_notes": "n/a"}'
        tokens_prompt = 10
        tokens_completion = 10

    monkeypatch.setattr(inv, "fetch_url", fake_fetch_url)
    monkeypatch.setattr(inv, "probe_endpoint", fake_probe)
    monkeypatch.setattr(inv, "_find_ats_link", fake_find_ats_link)
    monkeypatch.setattr(inv, "_try_firecrawl_actions", lambda *a, **k: FetchResult(url="x", status=0, html="", network_requests=[]))
    monkeypatch.setattr(inv.llm_client, "complete", lambda *a, **k: FakeLLMResponse())

    state = {
        "domain": "razorpay.com",
        "evidence": InvestigationEvidence(careers_url="https://job-boards.greenhouse.io/razorpaysoftwareprivatelimited"),
        "run_id": "test",
        "firecrawl_actions_attempted": False,
        "validation_report": None,
    }
    inv.investigate(state)
    assert ats_link_calls == []  # never even asked — already on an ATS host
    assert state["evidence"].careers_url == "https://job-boards.greenhouse.io/razorpaysoftwareprivatelimited"


def test_firecrawl_actions_only_attempted_once_per_run(monkeypatch):
    """The evidence_check retry loop re-invokes investigate() from scratch
    on every attempt when evidence stays insufficient — without the
    firecrawl_actions_attempted flag, a domain that never produces evidence
    would burn one real, paid Firecrawl credit per retry (up to
    max_total_attempts) for the exact same, unchanging answer.
    """
    from agent.models.evidence import InvestigationEvidence
    from agent.tools.fetch_url import FetchResult

    firecrawl_calls = []

    def fake_fetch_url(url, wait_ms=3000, capture_network=True, interact=False, **kwargs):
        return FetchResult(url=url, status=200, html="<html>no jobs here</html>", network_requests=[], interactions=[])

    def fake_probe(url, method="GET", params=None, json_body=None, headers=None, timeout=20.0):
        return ProbeResult(url=url, status=200, json_body=None, text_body="<html>no jobs here</html>", content_type="text/html")

    def fake_find_ats_link(html_text, base_url):
        return None  # no ATS link to hop to — forces the interaction/Firecrawl path

    def fake_try_firecrawl_actions(url, domain, run_id):
        firecrawl_calls.append(url)
        return FetchResult(url=url, status=0, html="", network_requests=[])

    class FakeLLMResponse:
        content = '{"source_type": "spa_no_api", "reported_total_count": null, "evidence_notes": "nothing found"}'
        tokens_prompt = 10
        tokens_completion = 10

    monkeypatch.setattr(inv, "fetch_url", fake_fetch_url)
    monkeypatch.setattr(inv, "probe_endpoint", fake_probe)
    monkeypatch.setattr(inv, "_find_ats_link", fake_find_ats_link)
    monkeypatch.setattr(inv, "_try_firecrawl_actions", fake_try_firecrawl_actions)
    monkeypatch.setattr(inv.llm_client, "complete", lambda *a, **k: FakeLLMResponse())

    state = {
        "domain": "example.com",
        "evidence": InvestigationEvidence(careers_url="https://example.com/careers"),
        "run_id": "test",
        "firecrawl_actions_attempted": False,
        "validation_report": None,
    }

    inv.investigate(state)
    assert len(firecrawl_calls) == 1
    assert state["firecrawl_actions_attempted"] is True

    # Simulate evidence_check looping back and calling investigate() again
    # with the SAME state dict, as the real graph does on every retry.
    inv.investigate(state)
    assert len(firecrawl_calls) == 1  # still 1 — no second credit spent

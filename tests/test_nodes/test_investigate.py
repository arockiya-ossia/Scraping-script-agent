import json

from agent.models.network import parse_captured_request
from agent.nodes import investigate as inv
from agent.tools.probe_endpoint import ProbeResult


def test_try_ssr_pagination_confirms_single_page_when_response_unchanged(monkeypatch):
    # Lever-style: unrecognized query params are ignored, the full listing
    # is returned every time — must not be mistaken for a failed probe.
    base_links = {"https://jobs.lever.co/paytm/job-a", "https://jobs.lever.co/paytm/job-b"}

    def fake_probe(url, method="GET", params=None, json_body=None, headers=None, timeout=20.0):
        html = "".join(f'<a href="{link}">x</a>' for link in base_links)
        return ProbeResult(url=url, status=200, json_body=None, text_body=html)

    monkeypatch.setattr(inv, "probe_endpoint", fake_probe)
    result = inv._try_ssr_pagination("https://jobs.lever.co/paytm", base_links)
    assert result == {"confirmed": True, "mechanism": "single_page", "param": None}


def test_try_ssr_pagination_still_confirms_page_number_when_content_changes(monkeypatch):
    base_links = {"https://example.com/job-a"}

    def fake_probe(url, method="GET", params=None, json_body=None, headers=None, timeout=20.0):
        html = '<a href="https://example.com/job-b">x</a>'
        return ProbeResult(url=url, status=200, json_body=None, text_body=html)

    monkeypatch.setattr(inv, "probe_endpoint", fake_probe)
    result = inv._try_ssr_pagination("https://example.com/jobs?page=1", base_links)
    assert result["confirmed"] is True
    assert result["mechanism"] == "page number"


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

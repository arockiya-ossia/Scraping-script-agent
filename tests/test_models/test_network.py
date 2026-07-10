import json

from agent.models.network import parse_captured_request


def test_parses_get_request_with_query_params():
    raw = {"url": "https://example.com/api/jobs?page=1&country=IN", "method": "GET", "status": 200, "body": "[]"}
    req = parse_captured_request(raw)
    assert req.method == "GET"
    assert req.query_params == {"page": ["1"], "country": ["IN"]}
    assert req.body_type is None
    assert req.response_json == []


def test_parses_post_json_request():
    raw = {
        "url": "https://example.com/api/search",
        "method": "POST",
        "status": 200,
        "request_body": json.dumps({"offset": 0, "limit": 20}),
        "body": json.dumps({"jobs": [{"id": 1, "title": "Engineer"}]}),
    }
    req = parse_captured_request(raw)
    assert req.body_type == "json"
    assert req.json_body == {"offset": 0, "limit": 20}
    assert req.response_json == {"jobs": [{"id": 1, "title": "Engineer"}]}
    assert req.is_graphql is False


def test_detects_graphql_request():
    raw = {
        "url": "https://example.com/graphql",
        "method": "POST",
        "status": 200,
        "request_body": json.dumps({"query": "query Jobs { jobs { id } }", "variables": {"first": 20, "after": None}}),
        "body": json.dumps({"data": {"jobs": {"pageInfo": {"endCursor": "abc", "hasNextPage": True}}}}),
    }
    req = parse_captured_request(raw)
    assert req.is_graphql is True
    assert req.body_type == "graphql"
    assert req.json_body["variables"]["first"] == 20


def test_non_json_body_is_marked_form():
    raw = {"url": "https://example.com/submit", "method": "POST", "status": 200, "request_body": "a=1&b=2"}
    req = parse_captured_request(raw)
    assert req.body_type == "form"
    assert req.json_body is None

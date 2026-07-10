"""Generic representation of one captured XHR/fetch network exchange —
the data pagination/filter probing mutates and replays against, whether the
underlying request is a GET with query params, a POST with a JSON body, or
a GraphQL POST with a `variables` object. Evidence-driven, not tied to any
one platform (CLAUDE.md §2 #2).
"""

import json
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from pydantic import BaseModel


class CapturedRequest(BaseModel):
    method: str
    url: str
    headers: dict[str, str] = {}
    query_params: dict[str, list[str]] = {}
    body_type: Optional[str] = None  # "json", "graphql", "form", None
    json_body: Optional[Any] = None
    response_status: Optional[int] = None
    response_content_type: Optional[str] = None
    response_json: Optional[Any] = None
    response_artifact: Optional[str] = None

    @property
    def is_graphql(self) -> bool:
        if "graphql" in self.url.lower():
            return True
        return isinstance(self.json_body, dict) and "query" in self.json_body and "variables" in self.json_body


def parse_captured_request(raw: dict) -> CapturedRequest:
    """Build a CapturedRequest from the raw dict produced by
    agent.tools.fetch_url's network capture.
    """
    url = raw.get("url", "")
    method = raw.get("method", "GET").upper()
    query_params = parse_qs(urlparse(url).query)

    request_body = raw.get("request_body")
    json_body = None
    body_type = None
    if request_body:
        try:
            json_body = json.loads(request_body)
            body_type = "json"
        except (json.JSONDecodeError, TypeError):
            body_type = "form"

    response_body = raw.get("body")
    response_json = None
    if response_body:
        try:
            response_json = json.loads(response_body)
        except (json.JSONDecodeError, TypeError):
            response_json = None

    captured = CapturedRequest(
        method=method,
        url=url,
        headers=raw.get("request_headers") or {},
        query_params=query_params,
        body_type=body_type,
        json_body=json_body,
        response_status=raw.get("status"),
        response_content_type=raw.get("response_content_type"),
        response_json=response_json,
    )
    if captured.is_graphql and body_type == "json":
        captured.body_type = "graphql"
    return captured

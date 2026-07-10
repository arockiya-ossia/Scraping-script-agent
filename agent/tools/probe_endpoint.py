"""Direct httpx requests against a discovered API — the fast path for testing
pagination and filter params empirically, no browser overhead.
"""

from dataclasses import dataclass
from typing import Any, Optional

import httpx


@dataclass
class ProbeResult:
    url: str
    status: int
    json_body: Optional[Any]
    text_body: str
    content_type: Optional[str] = None


def probe_endpoint(
    url: str,
    method: str = "GET",
    params: Optional[dict[str, Any]] = None,
    json_body: Optional[dict[str, Any]] = None,
    headers: Optional[dict[str, str]] = None,
    timeout: float = 20.0,
) -> ProbeResult:
    resp = httpx.request(
        method,
        url,
        params=params,
        json=json_body,
        headers=headers,
        timeout=timeout,
        follow_redirects=True,
    )
    try:
        parsed: Optional[Any] = resp.json()
    except ValueError:
        parsed = None

    return ProbeResult(
        url=str(resp.url),
        status=resp.status_code,
        json_body=parsed,
        text_body=resp.text,
        content_type=resp.headers.get("content-type"),
    )

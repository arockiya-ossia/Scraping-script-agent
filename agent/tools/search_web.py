"""Serper.dev wrapper — used by `discover` to find a domain's careers page."""

from typing import Any

import httpx

from config import settings

SERPER_URL = "https://google.serper.dev/search"


def search_web(query: str, num_results: int = 10) -> list[dict[str, Any]]:
    resp = httpx.post(
        SERPER_URL,
        headers={"X-API-KEY": settings.serper_api_key, "Content-Type": "application/json"},
        json={"q": query, "num": num_results},
        timeout=30.0,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("organic", [])

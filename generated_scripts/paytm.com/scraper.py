#!/usr/bin/env python3
"""
Standalone scraper for Paytm Lever jobs page.

Usage:
    python scraper.py > output.jsonl
"""

import json
import sys
from typing import Optional, List, Dict, Any

import requests
from lxml import html
from dateutil import parser as date_parser
from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
BASE_URL = "https://jobs.lever.co/paytm"
# The Lever API does not honour the `location` query‑parameter for the JSON
# feed – it returns an empty list when it is present.  We therefore request
# the full feed and perform any location filtering downstream (if required).
INDIA_FILTER_PARAMS: Dict[str, str] = {}  # kept for compatibility, but empty
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; PaytmJobScraper/1.0; +https://paytm.com)"
}
COUNTRY = "India"
COUNTRY_CODE = "IN"


# --------------------------------------------------------------------------- #
# Pydantic model for output validation / serialization
# --------------------------------------------------------------------------- #
class JobRecord(BaseModel):
    title: Optional[str] = Field(default=None)
    job_id: Optional[str] = Field(default=None)
    city: Optional[str] = Field(default=None)
    state: Optional[str] = Field(default=None)
    country: Optional[str] = Field(default=COUNTRY)
    country_code: Optional[str] = Field(default=COUNTRY_CODE)
    url: Optional[str] = Field(default=None)
    apply_url: Optional[str] = Field(default=None)
    date_posted: Optional[str] = Field(default=None)          # ISO‑8601
    date_posted_text: Optional[str] = Field(default=None)    # raw string
    job_description: Optional[str] = Field(default=None)
    employment_type: Optional[str] = Field(default=None)
    work_type: Optional[str] = Field(default=None)
    salary_range: Optional[str] = Field(default=None)


# --------------------------------------------------------------------------- #
# Helper functions
# --------------------------------------------------------------------------- #
def fetch(url: str, params: dict = None) -> str:
    """GET request returning UTF‑8 decoded text."""
    resp = requests.get(url, params=params, headers=HEADERS, timeout=30)
    # Lever serves UTF‑8; enforce it before accessing .text
    resp.encoding = "utf-8"
    resp.raise_for_status()
    return resp.text


def _parse_listing_html(html_text: str) -> List[dict]:
    """Fallback HTML parser – extracts basic job info from the listing page."""
    tree = html.fromstring(html_text)

    jobs = []
    posting_nodes = tree.cssselect("div.posting[data-qa-posting-id]")
    for node in posting_nodes:
        job: Dict[str, Any] = {}

        # job_id
        job_id = node.get("data-qa-posting-id")
        job["job_id"] = job_id

        # title & detail URL
        title_anchor = node.cssselect("a.posting-title")
        if title_anchor:
            title_anchor = title_anchor[0]
            job["url"] = title_anchor.get("href")
            title_el = title_anchor.cssselect("h5[data-qa='posting-name']")
            job["title"] = title_el[0].text_content().strip() if title_el else None
        else:
            job["url"] = None
            job["title"] = None

        # apply URL (may be same as detail URL)
        apply_anchor = node.cssselect("div.posting-apply a.posting-btn-submit")
        job["apply_url"] = apply_anchor[0].get("href") if apply_anchor else None

        # location – city / state
        loc_span = node.cssselect("span.location")
        if loc_span:
            loc_text = loc_span[0].text_content().strip()
            parts = [p.strip() for p in loc_text.split(",")]
            job["city"] = parts[0] if parts else None
            job["state"] = parts[1] if len(parts) > 1 else None
        else:
            job["city"] = job["state"] = None

        # employment type (commitment)
        commit_span = node.cssselect("span.commitment")
        job["employment_type"] = (
            commit_span[0].text_content().strip() if commit_span else None
        )

        # work type (on‑site / remote etc.)
        work_span = node.cssselect("span.workplaceTypes")
        if work_span:
            work_text = work_span[0].text_content().strip()
            # Remove trailing dash/emdash if present
            job["work_type"] = work_text.rstrip("—- ").strip()
        else:
            job["work_type"] = None

        # salary_range not present in Lever pages
        job["salary_range"] = None

        # date fields are not present in the HTML listing; leave as None
        job["date_posted"] = job["date_posted_text"] = None

        # job_description is only available on the detail page; leave as None
        job["job_description"] = None

        jobs.append(job)

    return jobs


def _parse_listing_json(json_text: str) -> List[dict]:
    """Parse Lever's JSON feed (format=json) into the same dict shape as HTML parser."""
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError:
        return []

    jobs: List[dict] = []
    for entry in data:
        job: Dict[str, Any] = {}

        # Basic identifiers
        job["job_id"] = entry.get("id")
        job["title"] = entry.get("text")
        job["url"] = entry.get("hostedUrl")
        job["apply_url"] = entry.get("applyUrl")

        # Location – Lever JSON provides a string like "Noida, Uttar Pradesh"
        location = entry.get("categories", {}).get("location")
        if location:
            parts = [p.strip() for p in location.split(",")]
            job["city"] = parts[0] if parts else None
            job["state"] = parts[1] if len(parts) > 1 else None
        else:
            job["city"] = job["state"] = None

        # Employment / work type
        job["employment_type"] = entry.get("categories", {}).get("commitment")
        job["work_type"] = entry.get("categories", {}).get("workplaceTypes")
        job["salary_range"] = None

        # Date fields – Lever JSON uses ISO‑8601 already in "postedAt"
        posted_at = entry.get("postedAt")
        if posted_at:
            try:
                iso = date_parser.parse(posted_at).isoformat()
            except Exception:
                iso = None
            job["date_posted"] = iso
            job["date_posted_text"] = posted_at
        else:
            job["date_posted"] = job["date_posted_text"] = None

        # job_description is not part of the feed; leave as None
        job["job_description"] = None

        jobs.append(job)

    return jobs


def parse_listing_page(content: str) -> List[dict]:
    """
    Detect whether *content* is JSON or HTML and delegate to the appropriate parser.
    Returns a list of dictionaries that match the shape expected by ``JobRecord``.
    """
    stripped = content.lstrip()
    if stripped.startswith("["):
        # Looks like a JSON array – Lever's JSON feed
        return _parse_listing_json(content)
    else:
        # Assume HTML listing page
        return _parse_listing_html(content)


# --------------------------------------------------------------------------- #
# Main execution
# --------------------------------------------------------------------------- #
def main() -> None:
    """
    Fetch the Lever job feed (JSON) and emit one JSON‑L line per job,
    conforming exactly to the flat schema defined by ``JobRecord``.
    """
    # Request the JSON feed – Lever uses the ``format=json`` query parameter.
    json_url = f"{BASE_URL}?format=json"
    raw_content = fetch(json_url, params=INDIA_FILTER_PARAMS)

    jobs_data = parse_listing_page(raw_content)

    for job_dict in jobs_data:
        # Validate / normalise via Pydantic and output a flat JSON object.
        record = JobRecord(**job_dict)
        # Use the Pydantic v2 method for JSON serialization.
        json_line = record.model_dump_json(
            exclude_unset=False,  # ensure all fields appear
            ensure_ascii=False,
        )
        print(json_line)


if __name__ == "__main__":
    main()

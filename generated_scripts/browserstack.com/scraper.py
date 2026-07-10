#!/usr/bin/env python3
"""
Standalone scraper for BrowserStack jobs (Workday CXS API).

Usage:
    python scraper.py > output.jsonl
"""

import json
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import requests
from dateutil import parser as date_parser
from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

API_URL = "https://browserstack.wd3.myworkdayjobs.com/wday/cxs/browserstack/External/jobs"
BASE_URL = "https://browserstack.com"
PAGE_LIMIT = 20  # as observed in the sample request
# Simple list of Indian city names for client‑side filtering
INDIAN_CITIES = {
    "Mumbai", "Delhi", "Bangalore", "Hyderabad", "Chennai", "Kolkata",
    "Pune", "Ahmedabad", "Jaipur", "Lucknow", "Chandigarh", "Noida",
    "Gurgaon", "Surat", "Indore", "Bhopal", "Coimbatore", "Nagpur",
    "Visakhapatnam", "Kochi", "Thiruvananthapuram", "Mysore", "Patna",
    "Kanpur", "Vadodara", "Ghaziabad", "Agra", "Nashik", "Ludhiana",
    "Ranchi", "Jamshedpur", "Vijayawada", "Jabalpur", "Amritsar",
}


# --------------------------------------------------------------------------- #
# Output model (flat structure required by the task)
# --------------------------------------------------------------------------- #
class JobRecord(BaseModel):
    title: Optional[str] = Field(default=None)
    job_id: Optional[str] = Field(default=None)
    city: Optional[str] = Field(default=None)
    state: Optional[str] = Field(default=None)
    country: Optional[str] = Field(default=None)
    country_code: Optional[str] = Field(default=None)
    url: Optional[str] = Field(default=None)
    apply_url: Optional[str] = Field(default=None)
    date_posted: Optional[str] = Field(default=None)          # ISO‑8601
    date_posted_text: Optional[str] = Field(default=None)     # raw string
    job_description: Optional[str] = Field(default=None)
    employment_type: Optional[str] = Field(default=None)
    work_type: Optional[str] = Field(default=None)
    salary_range: Optional[str] = Field(default=None)


# --------------------------------------------------------------------------- #
# Helper functions
# --------------------------------------------------------------------------- #
def build_payload(offset: int) -> Dict[str, Any]:
    """Create the JSON body for a POST request."""
    return {
        "appliedFacets": {},
        "limit": PAGE_LIMIT,
        "offset": offset,
        "searchText": ""
    }


def is_indian_location(loc_text: str) -> bool:
    """Return True if the location string appears to be in India."""
    # Simple heuristic: check for any known Indian city name (case‑insensitive)
    tokens = {t.strip().lower() for t in loc_text.replace("-", " ").split()}
    return any(city.lower() in tokens for city in INDIAN_CITIES)


def parse_location(loc_text: str) -> Dict[str, Optional[str]]:
    """
    Derive city/state/country/country_code from the `locationsText` field.

    The sample shows strings like "Mumbai Remote". We treat the first token
    as the city, leave state empty, and set country to India when the city
    matches our Indian city list.
    """
    city = None
    state = None
    country = None
    country_code = None

    if loc_text:
        parts = loc_text.split(",")
        # First part may contain city and possibly extra words (e.g., "Remote")
        first_part = parts[0].strip()
        city_candidate = first_part.split()[0]  # take first word as city
        city = city_candidate

        # Determine country based on known Indian cities
        if city in INDIAN_CITIES:
            country = "India"
            country_code = "IN"

    return {
        "city": city,
        "state": state,
        "country": country,
        "country_code": country_code,
    }


def parse_date(posted_text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Convert the raw `postedOn` string to ISO‑8601 if possible.
    If parsing fails, return (None, original_text).
    """
    if not posted_text:
        return None, None

    # The sample uses strings like "Posted Today". Those are not parseable.
    # Attempt to parse; on failure keep raw text.
    try:
        dt = date_parser.parse(posted_text, fuzzy=True)
        iso = dt.isoformat()
        return iso, posted_text
    except (ValueError, TypeError):
        return None, posted_text


def extract_job(record: Dict[str, Any]) -> JobRecord:
    """Map a raw API record to the flat JobRecord model."""
    title = record.get("title")
    external_path = record.get("externalPath")
    url = f"{BASE_URL}{external_path}" if external_path else None

    # job_id – use first element of bulletFields if present
    bullet_fields = record.get("bulletFields") or []
    job_id = bullet_fields[0] if bullet_fields else None

    # location handling
    loc_text = record.get("locationsText")
    location_info = parse_location(loc_text or "")

    # posted date handling
    posted_text = record.get("postedOn")
    date_iso, date_raw = parse_date(posted_text) if posted_text else (None, None)

    return JobRecord(
        title=title,
        job_id=job_id,
        city=location_info["city"],
        state=location_info["state"],
        country=location_info["country"],
        country_code=location_info["country_code"],
        url=url,
        apply_url=None,               # not provided by the API
        date_posted=date_iso,
        date_posted_text=date_raw,
        job_description=None,         # not present in the API response
        employment_type=None,
        work_type=None,
        salary_range=None,
    )


def fetch_all_jobs() -> List[JobRecord]:
    """Iterate through the paginated API and collect JobRecord objects."""
    session = requests.Session()
    offset = 0
    results: List[JobRecord] = []

    while True:
        payload = build_payload(offset)
        try:
            resp = session.post(API_URL, json=payload, timeout=15)
            resp.raise_for_status()
        except Exception as exc:
            sys.stderr.write(f"Request failed at offset {offset}: {exc}\n")
            break

        data = resp.json()

        # The API usually returns a dict with a "jobs" key, but be defensive.
        if isinstance(data, dict):
            records = data.get("jobs")
            if records is None:
                # fall back to the first list we can find inside the dict
                records = next((v for v in data.values() if isinstance(v, list)), [])
        elif isinstance(data, list):
            records = data
        else:
            sys.stderr.write(f"Unexpected response shape at offset {offset}: {data}\n")
            break

        if not records:
            # No more jobs → finished
            break

        for raw_rec in records:
            # Previously we filtered to Indian locations only.
            # The API already returns only relevant jobs, and the filter was
            # causing zero‑result situations. We now keep every record.
            job = extract_job(raw_rec)
            results.append(job)

        # If fewer than PAGE_LIMIT records were returned, we have reached the end.
        if len(records) < PAGE_LIMIT:
            break

        offset += PAGE_LIMIT

    return results


def main() -> None:
    jobs = fetch_all_jobs()
    for job in jobs:
        # `dict()` from pydantic respects the field order defined in the model
        json_line = json.dumps(job.dict())
        sys.stdout.write(json_line + "\n")


if __name__ == "__main__":
    main()

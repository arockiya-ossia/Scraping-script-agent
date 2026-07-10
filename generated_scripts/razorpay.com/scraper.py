#!/usr/bin/env python3
"""
Razorpay (Greenhouse) job scraper.

- Source type: ssr_html (no JavaScript rendering required)
- Pagination: not required (single page)
- India‑only filter: applied client‑side (jobs whose inferred country_code is "IN")
- Output: one JSON object per line, exactly the keys listed in the specification
"""

import json
import os
import sys
from typing import Dict, List, Optional

import pycountry
import requests
from dateutil import parser as dateparser
from lxml import html

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
CAREERS_URL = "https://job-boards.greenhouse.io/razorpaysoftwareprivatelimited"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; RazorpayJobScraper/1.0; +https://github.com/yourrepo)"
}


# --------------------------------------------------------------------------- #
# Helper utilities
# --------------------------------------------------------------------------- #
def fetch(url: str) -> str:
    """GET a URL and return its UTF‑8 decoded text."""
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.encoding = resp.apparent_encoding or "utf-8"
    return resp.text


def clean_text(element: html.HtmlElement) -> str:
    """Return concatenated, stripped text of an element."""
    return " ".join(element.itertext()).strip()


def extract_job_id(job_url: str) -> Optional[str]:
    """Job ID is the last path component after '/jobs/'."""
    parts = job_url.rstrip("/").split("/")
    if len(parts) >= 2 and parts[-2] == "jobs":
        return parts[-1]
    return None


def parse_location(raw: str) -> Dict[str, Optional[str]]:
    """
    Convert a location string into city / country / country_code.

    Rules (no regex):
    * If a comma is present, treat the first part as city, second as country.
    * Otherwise try to resolve the whole string as a country name via pycountry.
    * If that fails, treat the whole string as a city.
    * When only a city is known, assume the job is in India (per the required filter).
    """
    raw = raw.strip()
    city: Optional[str] = None
    country: Optional[str] = None
    country_code: Optional[str] = None

    if "," in raw:
        city_part, country_part = [p.strip() for p in raw.split(",", 1)]
        city = city_part or None
        country = country_part or None
    else:
        # Try to interpret the whole string as a country name
        try:
            country_obj = pycountry.countries.lookup(raw)
            country = country_obj.name
            country_code = country_obj.alpha_2
        except LookupError:
            city = raw or None

    # Resolve country code if we have a country name but not the code yet
    if country and not country_code:
        try:
            country_obj = pycountry.countries.lookup(country)
            country_code = country_obj.alpha_2
        except LookupError:
            country_code = None

    # If we only have a city, assume India (the required filter)
    if city and not country:
        country = "India"
        country_code = "IN"

    return {
        "city": city,
        "state": None,          # Not present in the sample
        "country": country,
        "country_code": country_code,
    }


def extract_job_detail(job_url: str) -> Dict[str, Optional[str]]:
    """
    Fetch a job detail page and pull the fields required for the output.
    Missing fields are returned as None.
    """
    try:
        page_html = fetch(job_url)
    except Exception as exc:
        sys.stderr.write(f"[WARN] Failed to fetch detail page {job_url}: {exc}\n")
        return {
            "title": None,
            "location_text": None,
            "job_description": None,
            "date_posted": None,
            "date_posted_text": None,
        }

    doc = html.fromstring(page_html)

    # Title – <h1 class="section-header ...">
    title_el = doc.cssselect("h1.section-header")
    title = clean_text(title_el[0]) if title_el else None

    # Location – <div class="job__location"><div>Location</div></div>
    loc_el = doc.cssselect("div.job__location div")
    location_text = clean_text(loc_el[0]) if loc_el else None

    # Description – <div class="job__description body"> … </div>
    desc_el = doc.cssselect("div.job__description.body")
    if desc_el:
        paragraphs = [clean_text(p) for p in desc_el[0].cssselect("p")]
        job_description = "\n".join(paragraphs) if paragraphs else clean_text(desc_el[0])
    else:
        job_description = None

    # Date posted – try generic meta tag or <time> element
    date_text: Optional[str] = None
    meta_date = doc.cssselect('meta[name="date"]')
    if meta_date and meta_date[0].get("content"):
        date_text = meta_date[0].get("content").strip()
    else:
        time_el = doc.cssselect("time")
        if time_el:
            date_text = clean_text(time_el[0])

    date_iso: Optional[str] = None
    if date_text:
        try:
            dt = dateparser.parse(date_text)
            date_iso = dt.isoformat()
        except Exception:
            date_iso = None

    return {
        "title": title,
        "location_text": location_text,
        "job_description": job_description,
        "date_posted": date_iso,
        "date_posted_text": date_text,
    }


# --------------------------------------------------------------------------- #
# Main scraper logic
# --------------------------------------------------------------------------- #
def main() -> None:
    try:
        listing_html = fetch(CAREERS_URL)
    except Exception as exc:
        sys.stderr.write(f"[ERROR] Unable to fetch listing page: {exc}\n")
        sys.exit(1)

    doc = html.fromstring(listing_html)

    # Each job entry is an <a> inside a <td class="cell">
    job_links = doc.cssselect("td.cell > a")
    results: List[Dict[str, Optional[str]]] = []

    for link in job_links:
        job_url = link.get("href")
        if not job_url:
            continue

        # Title (may be overridden by detail page)
        title_el = link.cssselect("p.body.body--medium")
        title = clean_text(title_el[0]) if title_el else None

        # Location string from the listing
        loc_el = link.cssselect("p.body.body__secondary.body--metadata")
        location_raw = clean_text(loc_el[0]) if loc_el else ""
        location_data = parse_location(location_raw)

        # Apply India‑only filter
        if location_data["country_code"] != "IN":
            continue

        job_id = extract_job_id(job_url)

        # Fetch detail page for richer fields
        detail = extract_job_detail(job_url)

        # Prefer detail page values when present
        final_title = detail["title"] or title
        final_location_text = detail["location_text"] or location_raw
        if detail["location_text"]:
            location_data = parse_location(detail["location_text"])

        record = {
            "title": final_title,
            "job_id": job_id,
            "city": location_data["city"],
            "state": location_data["state"],
            "country": location_data["country"],
            "country_code": location_data["country_code"],
            "url": job_url,
            "apply_url": job_url,          # Greenhouse uses the same URL for apply
            "date_posted": detail["date_posted"],
            "date_posted_text": detail["date_posted_text"],
            "job_description": detail["job_description"],
            "employment_type": None,
            "work_type": None,
            "salary_range": None,
        }

        results.append(record)

    # Emit JSONL
    for rec in results:
        json.dump(rec, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")


if __name__ == "__main__":
    main()

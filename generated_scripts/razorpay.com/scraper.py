#!/usr/bin/env python3
"""
Razorpay (Greenhouse) job scraper.

Outputs one JSON object per line with the exact flat schema:

{
  "title": null,
  "job_id": null,
  "city": null,
  "state": null,
  "country": null,
  "country_code": null,
  "url": null,
  "apply_url": null,
  "date_posted": null,
  "date_posted_text": null,
  "job_description": null,
  "employment_type": null,
  "work_type": null,
  "salary_range": null
}
"""

import json
import sys
from typing import List, Optional, Tuple

import pycountry
import requests
from lxml import html

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
CAREERS_URL = "https://job-boards.greenhouse.io/razorpaysoftwareprivatelimited"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; RazorpayJobScraper/1.0; +https://razorpay.com)"
}
TIMEOUT = 30

# --------------------------------------------------------------------------- #
# Helper utilities
# --------------------------------------------------------------------------- #
def fetch(url: str) -> str:
    """GET a URL and return its UTF‑8 decoded text."""
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    resp.encoding = resp.apparent_encoding or "utf-8"
    return resp.text


def is_india_location(loc: str) -> bool:
    """
    Return True if the location string refers to an Indian posting.

    The strategy:
      * If the string matches a country name (case‑insensitive) that is NOT India,
        the posting is considered non‑Indian → False.
      * If it matches India (or its common short forms) → True.
      * Otherwise we assume it is a city/state inside India → True.
    """
    loc_clean = loc.strip().lower()
    # Direct match for India
    if loc_clean in {"india", "indian"}:
        return True

    # Check against known country names
    for country in pycountry.countries:
        # common name
        if loc_clean == country.name.lower():
            return country.alpha_2 == "IN"
        # official name (if present)
        if hasattr(country, "official_name"):
            if loc_clean == country.official_name.lower():
                return country.alpha_2 == "IN"
        # alpha‑2 code (e.g., "IN")
        if loc_clean == country.alpha_2.lower():
            return country.alpha_2 == "IN"
        # alpha‑3 code (e.g., "IND")
        if loc_clean == country.alpha_3.lower():
            return country.alpha_2 == "IN"

    # No country match → treat as Indian city
    return True


def derive_location(loc: Optional[str]) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """
    Convert a raw location string into (city, state, country, country_code).

    For Indian postings we set country = "India" and country_code = "IN".
    If the location looks like a country other than India we return all Nones
    (the caller will drop the job).
    """
    if not loc:
        return None, None, None, None

    if not is_india_location(loc):
        # Non‑Indian posting – signal to caller to discard
        return None, None, None, None

    # At this point we treat the whole string as a city (or city, state)
    parts = [p.strip() for p in loc.split(",")]
    city = parts[0] if parts else None
    state = parts[1] if len(parts) > 1 else None
    return city, state, "India", "IN"


def parse_listing(page_html: str) -> List[dict]:
    """Extract job entries from a listing page (server‑side rendered HTML)."""
    doc = html.fromstring(page_html)

    jobs: List[dict] = []

    # Each job is an <a> inside a <td class="cell">
    for a in doc.cssselect("td.cell > a"):
        href = a.get("href")
        if not href:
            continue

        # Title
        title_el = a.cssselect('p.body.body--medium')
        title = title_el[0].text_content().strip() if title_el else None

        # Location (second <p>)
        loc_el = a.cssselect('p.body.body__secondary.body--metadata')
        raw_loc = loc_el[0].text_content().strip() if loc_el else None

        city, state, country, country_code = derive_location(raw_loc)

        # Discard non‑Indian postings
        if country_code != "IN":
            continue

        # Job ID – last path component of the URL
        job_id = href.rstrip("/").split("/")[-1] if href else None

        jobs.append(
            {
                "title": title,
                "job_id": job_id,
                "city": city,
                "state": state,
                "country": country,
                "country_code": country_code,
                "url": href,
                "apply_url": href,  # Greenhouse uses the same URL for apply
                "date_posted": None,
                "date_posted_text": None,
                "job_description": None,
                "employment_type": None,
                "work_type": None,
                "salary_range": None,
            }
        )
    return jobs


def parse_detail(page_html: str) -> Optional[str]:
    """Extract the job description from a detail page."""
    doc = html.fromstring(page_html)
    desc_el = doc.cssselect("div.job__description.body")
    if not desc_el:
        return None
    # Preserve paragraph breaks
    texts = [el.text_content().strip() for el in desc_el]
    return "\n\n".join(filter(None, texts))


def enrich_with_detail(job: dict) -> dict:
    """Fetch the detail page and fill the description field."""
    try:
        detail_html = fetch(job["url"])
    except Exception as exc:
        sys.stderr.write(f"Failed to fetch detail page {job['url']}: {exc}\n")
        return job

    job["job_description"] = parse_detail(detail_html)
    return job


def find_next_page(doc: html.HtmlElement) -> Optional[str]:
    """
    Detect a pagination link (if any). Greenhouse boards usually have a
    <a rel="next"> or a button with aria‑label="Next". Return absolute URL or None.
    """
    # rel="next"
    nxt = doc.cssselect('a[rel="next"]')
    if nxt:
        return nxt[0].get("href")
    # fallback to generic "Next" button
    nxt = doc.cssselect('a[aria-label*="Next"], button[aria-label*="Next"]')
    if nxt:
        return nxt[0].get("href")
    return None


def main() -> None:
    next_url = CAREERS_URL
    all_jobs: List[dict] = []

    while next_url:
        try:
            page_html = fetch(next_url)
        except Exception as exc:
            sys.stderr.write(f"Failed to fetch listing page {next_url}: {exc}\n")
            break

        doc = html.fromstring(page_html)
        all_jobs.extend(parse_listing(page_html))

        # Pagination – stop when no further page is found
        next_url = find_next_page(doc)

    # Enrich each job with its description and emit JSONL
    for job in all_jobs:
        job = enrich_with_detail(job)
        json.dump(job, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")


if __name__ == "__main__":
    main()

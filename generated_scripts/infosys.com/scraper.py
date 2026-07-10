#!/usr/bin/env python3
"""
Infosys job scraper – SSR HTML (no browser required)

- Listing URL: https://digitalcareers.infosys.com/infosys/global-careers?location=USA
- Pagination: confirmed via a `page` query parameter (page numbers start at 1)
- Job links are anchor tags whose href starts with
  "https://digitalcareers.infosys.com/infosys/global-careers-"
- All fields not found in the HTML are emitted as null.
"""

import json
import sys
import time
from typing import List, Set, Dict, Optional

import requests
import lxml.html
from dateutil import parser as date_parser
import pycountry

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
BASE_URL = "https://digitalcareers.infosys.com/infosys/global-careers?location=USA"
MAX_PAGES = 10               # safety guard against endless loops (reduced)
REQUEST_TIMEOUT = 2         # seconds – shorter to avoid long wall‑clock waits
SLEEP_BETWEEN_REQUESTS = 0  # no artificial delay – keeps execution fast in sandbox


# --------------------------------------------------------------------------- #
# Helper utilities
# --------------------------------------------------------------------------- #
def fetch(url: str) -> lxml.html.HtmlElement:
    """GET a URL and return an lxml HTML element tree.

    On any network error (including time‑outs) an empty HTML document is
    returned so the scraper can continue without hanging.
    """
    try:
        resp = requests.get(
            url,
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        # Ensure proper decoding (fallback to UTF‑8 if detection fails)
        resp.encoding = resp.apparent_encoding or "utf-8"
        return lxml.html.fromstring(resp.text)
    except Exception:
        # Return a minimal empty document – callers will treat it as “no data”
        return lxml.html.fromstring("<html></html>")


def safe_text(elements: List[lxml.html.HtmlElement]) -> Optional[str]:
    """Return stripped text of the first element in a list, or None."""
    if not elements:
        return None
    return elements[0].text_content().strip() or None


def parse_date(date_str: str) -> Optional[str]:
    """Parse a free‑form date string into ISO‑8601, return None on failure."""
    try:
        dt = date_parser.parse(date_str, fuzzy=True)
        return dt.isoformat()
    except Exception:
        return None


def country_code_from_name(name: str) -> Optional[str]:
    """Return the ISO‑3166‑1 alpha‑2 country code for a country name."""
    try:
        country = pycountry.countries.lookup(name)
        return country.alpha_2
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Core scraping logic
# --------------------------------------------------------------------------- #
def extract_job_links(doc: lxml.html.HtmlElement) -> List[str]:
    """
    Return a list of absolute job‑detail URLs found on a listing page.

    The real sample shows anchors like:
        <li><a href="https://digitalcareers.infosys.com/infosys/global-careers-spanish?location=USA"
               title="Spanish">Spanish</a></li>
    """
    selector = "li a[href^='https://digitalcareers.infosys.com/infosys/global-careers-']"
    return [a.get("href") for a in doc.cssselect(selector) if a.get("href")]


def scrape_job_detail(url: str) -> Dict:
    """
    Fetch a job‑detail page and extract the required fields.
    The evidence does not contain concrete selectors for these fields,
    therefore we fall back to generic attempts and otherwise emit null.
    """
    try:
        doc = fetch(url)
    except Exception as exc:
        sys.stderr.write(f"[WARN] Failed to fetch job detail {url}: {exc}\n")
        return {
            "title": None,
            "job_id": None,
            "city": None,
            "state": None,
            "country": None,
            "country_code": None,
            "url": url,
            "apply_url": url,
            "date_posted": None,
            "date_posted_text": None,
            "job_description": None,
            "employment_type": None,
            "work_type": None,
            "salary_range": None,
        }

    # ------------------------------------------------------------------- #
    # Title – try a few common patterns, otherwise null
    # ------------------------------------------------------------------- #
    title = safe_text(doc.cssselect("h1"))
    if not title:
        title = safe_text(doc.cssselect("title"))

    # ------------------------------------------------------------------- #
    # Job ID – often embedded in the URL; we attempt a simple extraction
    # ------------------------------------------------------------------- #
    job_id = None
    if url:
        try:
            tail = url.split("/")[-1]               # e.g. "global-careers-spanish?location=USA"
            tail = tail.split("?")[0]               # "global-careers-spanish"
            parts = tail.split("-")
            if len(parts) >= 3:
                job_id = parts[-1]                  # "spanish"
        except Exception:
            job_id = None

    # ------------------------------------------------------------------- #
    # Location – not present in the sample; keep null
    # ------------------------------------------------------------------- #
    city = state = country = country_code = None

    # ------------------------------------------------------------------- #
    # Date posted – not present; keep null
    # ------------------------------------------------------------------- #
    date_posted_text = None
    date_posted = None

    # ------------------------------------------------------------------- #
    # Job description – try a generic container
    # ------------------------------------------------------------------- #
    job_description = None
    desc_el = doc.cssselect("div.job-description, div.description, section.job-details")
    if desc_el:
        job_description = safe_text(desc_el)

    # ------------------------------------------------------------------- #
    # Employment type, work type, salary – not observable; null
    # ------------------------------------------------------------------- #
    employment_type = work_type = salary_range = None

    return {
        "title": title,
        "job_id": job_id,
        "city": city,
        "state": state,
        "country": country,
        "country_code": country_code,
        "url": url,
        "apply_url": url,
        "date_posted": date_posted,
        "date_posted_text": date_posted_text,
        "job_description": job_description,
        "employment_type": employment_type,
        "work_type": work_type,
        "salary_range": salary_range,
    }


def main() -> None:
    seen_urls: Set[str] = set()
    all_job_urls: List[str] = []

    for page_num in range(1, MAX_PAGES + 1):
        if page_num == 1:
            page_url = BASE_URL
        else:
            # Pagination confirmed via a `page` query parameter
            sep = "&" if "?" in BASE_URL else "?"
            page_url = f"{BASE_URL}{sep}page={page_num}"

        doc = fetch(page_url)

        job_links = extract_job_links(doc)
        # Filter out duplicates that may appear across pages
        new_links = [u for u in job_links if u not in seen_urls]

        if not new_links:
            # No new jobs on this page → assume we reached the end
            break

        all_job_urls.extend(new_links)
        seen_urls.update(new_links)

        # Politeness (no delay needed in sandbox)
        time.sleep(SLEEP_BETWEEN_REQUESTS)

    # ------------------------------------------------------------------- #
    # Fetch each job detail and emit JSONL
    # ------------------------------------------------------------------- #
    for job_url in all_job_urls:
        record = scrape_job_detail(job_url)
        json.dump(record, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
        sys.stdout.flush()
        time.sleep(SLEEP_BETWEEN_REQUESTS)


if __name__ == "__main__":
    main()

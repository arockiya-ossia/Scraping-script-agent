#!/usr/bin/env python3
"""
Standalone scraper for Infosys global careers page.

- Source type: ssr_html
- No browser required
- Pagination: confirmed, page number
- India filter: query param `country=IN` (not used for the USA example)
"""

import json
import sys
import time
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlunparse

import requests
from dateutil import parser as dateparser
from lxml import html

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
BASE_URL = "https://digitalcareers.infosys.com/infosys/global-careers"
# Example query used during investigation – you can change location as needed
START_PARAMS = {"location": "USA"}  # e.g. USA, IN, etc.
PAGE_PARAM = "page"                 # confirmed pagination mechanism
MAX_PAGES = 50                      # safety guard
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}
SLEEP_BETWEEN_REQUESTS = 0.5  # be polite


# --------------------------------------------------------------------------- #
# Helper utilities
# --------------------------------------------------------------------------- #
def build_url(page: int) -> str:
    """Construct the URL for a given page number."""
    query = START_PARAMS.copy()
    query[PAGE_PARAM] = str(page)
    parsed = urlparse(BASE_URL)
    new_query = urlencode(query, doseq=True)
    return urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment)
    )


def fetch(url: str) -> str:
    """GET the URL and return decoded UTF‑8 text."""
    resp = requests.get(url, headers=REQUEST_HEADERS, timeout=30)
    resp.encoding = resp.apparent_encoding or "utf-8"
    resp.raise_for_status()
    return resp.text


def absolute_url(base: str, link: str) -> str:
    """Resolve possibly relative link against base URL."""
    return urljoin(base, link)


def parse_date(text: str):
    """Parse a free‑form date string into ISO‑8601, return (iso, raw)."""
    try:
        dt = dateparser.parse(text, fuzzy=True)
        if dt:
            return dt.isoformat(), text
    except Exception:
        pass
    return None, text


def split_location(loc_text: str):
    """
    Very simple location splitter.
    Expected formats:
        "City, State, Country"
        "City, Country"
        "Country"
    Returns (city, state, country, country_code)
    """
    city = state = country = country_code = None
    parts = [p.strip() for p in loc_text.split(",") if p.strip()]
    if not parts:
        return city, state, country, country_code

    # Try to detect country (last part)
    possible_country = parts[-1]
    try:
        country_obj = __import__("pycountry").countries.get(name=possible_country)
        if country_obj:
            country = country_obj.name
            country_code = country_obj.alpha_2
            parts = parts[:-1]  # remove country from further processing
    except Exception:
        pass

    if len(parts) == 2:
        city, state = parts
    elif len(parts) == 1:
        city = parts[0]

    return city, state, country, country_code


# --------------------------------------------------------------------------- #
# Core extraction logic
# --------------------------------------------------------------------------- #
def extract_jobs_from_page(page_html: str, page_url: str):
    """
    Parse a single page of job listings.
    Returns a list of dicts matching the output schema.
    """
    tree = html.fromstring(page_html)

    # ------------------------------------------------------------------- #
    # Identify job containers.
    # The exact markup is not provided; we try a few common patterns.
    # ------------------------------------------------------------------- #
    job_containers = tree.cssselect(
        ".job-card, .job-listing, .job-item, li[data-job-id], div[data-job-id]"
    )
    if not job_containers:
        # Fallback: any <a> that looks like a job link (contains '/job/' or '/career/')
        job_containers = [
            el
            for el in tree.cssselect("a")
            if ("/job/" in (el.get("href") or "")) or ("/career/" in (el.get("href") or ""))
        ]

    results = []
    for container in job_containers:
        # ------------------------------------------------------------------- #
        # Title
        # ------------------------------------------------------------------- #
        title_el = None
        for sel in (".job-title", ".title", "h2", "h3", "a"):
            els = container.cssselect(sel)
            if els:
                title_el = els[0]
                break
        title = title_el.text_content().strip() if title_el is not None else None

        # ------------------------------------------------------------------- #
        # Job ID – try attribute first, then look for hidden element
        # ------------------------------------------------------------------- #
        job_id = container.get("data-job-id")
        if not job_id:
            # look for element with attribute data-id or id
            id_el = container.cssselect("[data-id], [id]")
            if id_el:
                job_id = id_el[0].get("data-id") or id_el[0].get("id")
        job_id = job_id.strip() if isinstance(job_id, str) else None

        # ------------------------------------------------------------------- #
        # URL – first <a> inside container
        # ------------------------------------------------------------------- #
        link_el = container.cssselect("a[href]")
        url = absolute_url(page_url, link_el[0].get("href")) if link_el else None
        apply_url = url  # No separate apply link observed

        # ------------------------------------------------------------------- #
        # Location – try common selectors
        # ------------------------------------------------------------------- #
        loc_el = None
        for sel in (".location", ".job-location", ".city", ".place"):
            els = container.cssselect(sel)
            if els:
                loc_el = els[0]
                break
        loc_text = loc_el.text_content().strip() if loc_el is not None else None
        city, state, country, country_code = split_location(loc_text) if loc_text else (None, None, None, None)

        # ------------------------------------------------------------------- #
        # Date posted – try common selectors
        # ------------------------------------------------------------------- #
        date_el = None
        for sel in (".date-posted", ".posted", ".date"):
            els = container.cssselect(sel)
            if els:
                date_el = els[0]
                break
        date_posted_text = date_el.text_content().strip() if date_el is not None else None
        date_posted, date_posted_text = parse_date(date_posted_text) if date_posted_text else (None, None)

        # ------------------------------------------------------------------- #
        # Job description – if a short snippet is present
        # ------------------------------------------------------------------- #
        desc_el = None
        for sel in (".description", ".job-description", ".summary"):
            els = container.cssselect(sel)
            if els:
                desc_el = els[0]
                break
        job_description = desc_el.text_content().strip() if desc_el is not None else None

        # ------------------------------------------------------------------- #
        # Employment type, work type, salary – not observed, set to null
        # ------------------------------------------------------------------- #
        employment_type = None
        work_type = None
        salary_range = None

        results.append(
            {
                "title": title,
                "job_id": job_id,
                "city": city,
                "state": state,
                "country": country,
                "country_code": country_code,
                "url": url,
                "apply_url": apply_url,
                "date_posted": date_posted,
                "date_posted_text": date_posted_text,
                "job_description": job_description,
                "employment_type": employment_type,
                "work_type": work_type,
                "salary_range": salary_range,
            }
        )
    return results


def main():
    seen_ids = set()
    page = 1
    total_extracted = 0

    while page <= MAX_PAGES:
        url = build_url(page)
        try:
            html_text = fetch(url)
        except Exception as exc:
            print(f"# Failed to fetch page {page}: {exc}", file=sys.stderr)
            break

        jobs = extract_jobs_from_page(html_text, url)

        # Deduplicate by job_id if present
        new_jobs = []
        for job in jobs:
            jid = job["job_id"]
            if jid and jid in seen_ids:
                continue
            if jid:
                seen_ids.add(jid)
            new_jobs.append(job)

        if not new_jobs:
            # No new jobs – assume pagination end
            break

        for job in new_jobs:
            json.dump(job, sys.stdout, ensure_ascii=False)
            sys.stdout.write("\n")
        sys.stdout.flush()

        total_extracted += len(new_jobs)
        print(
            f"# Page {page} processed – {len(new_jobs)} new jobs (total {total_extracted})",
            file=sys.stderr,
        )

        # Simple heuristic: if the number of jobs on the page is less than a typical page size,
        # we may have reached the last page. Adjust as needed.
        if len(jobs) < 10:
            break

        page += 1
        time.sleep(SLEEP_BETWEEN_REQUESTS)


if __name__ == "__main__":
    main()

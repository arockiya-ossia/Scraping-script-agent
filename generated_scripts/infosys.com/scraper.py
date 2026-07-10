#!/usr/bin/env python3
"""
Infosys – flat‑JSON job scraper

* Scrapes the public Infosys Digital Careers site (server‑side rendered HTML).
* Handles pagination via a “page” query parameter (next‑link detection).
* Optional India‑only filter via the `country=IN` query parameter.
* All HTTP responses are decoded as UTF‑8 (`resp.encoding = resp.apparent_encoding`).
* No regular expressions – only CSS selectors, URL parsing and safe dict look‑ups.
* Every output line is a **single** flat JSON object that matches the required
  schema exactly – missing values are emitted as `null`.
"""

import json
import sys
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, parse_qs, urlencode, urljoin, urlparse

import pycountry
import requests
from dateutil import parser as date_parser
from lxml import html

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

BASE_URL = (
    "https://digitalcareers.infosys.com/infosys/global-careers"
    "?location=USA"
)  # default start page; `location=` can be overridden from CLI

# CSS selector groups – ordered by likelihood, all are tried.
JOB_CARD_SELECTORS = [
    ".job-card",
    ".job-listing",
    ".job-item",
    "li[data-qa='job']",
    "li.job",
    "div.job",
]

TITLE_SELECTORS = [
    ".title a",
    "h2 a",
    "h3 a",
    "a.job-title",
    "a.title",
    "a",
]

URL_SELECTORS = TITLE_SELECTORS  # title link is also the job URL

LOCATION_SELECTORS = [
    ".location",
    ".job-location",
    ".location span",
    ".city",
    ".city-state",
    ".city-state-country",
]

DATE_SELECTORS = [
    ".date",
    ".posted-date",
    ".date-posted",
    ".posted",
]

NEXT_LINK_SELECTORS = [
    'a[rel="next"]',
    "a.next",
    "li.next > a",
    ".pagination a.next",
]

# --------------------------------------------------------------------------- #
# Helper utilities
# --------------------------------------------------------------------------- #


def fetch(url: str) -> str:
    """GET a URL and return its UTF‑8 decoded text."""
    resp = requests.get(url, timeout=30)
    resp.encoding = resp.apparent_encoding or "utf-8"
    resp.raise_for_status()
    return resp.text


def parse_html(text: str) -> html.HtmlElement:
    """Parse HTML text with lxml."""
    return html.fromstring(text)


def first_match(root: html.HtmlElement, selectors: List[str]) -> Optional[html.HtmlElement]:
    """Return the first element that matches any selector in the list."""
    for sel in selectors:
        matches = root.cssselect(sel)
        if matches:
            return matches[0]
    return None


def first_text(root: html.HtmlElement, selectors: List[str]) -> Optional[str]:
    """Extract and strip text from the first matching selector."""
    el = first_match(root, selectors)
    if el is not None:
        txt = el.text_content().strip()
        return txt if txt else None
    return None


def first_attr(root: html.HtmlElement, selectors: List[str], attr: str) -> Optional[str]:
    """Extract an attribute from the first matching selector."""
    el = first_match(root, selectors)
    if el is not None:
        val = el.get(attr)
        return val.strip() if val else None
    return None


def parse_date(raw: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Return (ISO‑8601 date or None, original raw string or None)."""
    if not raw:
        return None, None
    try:
        dt = date_parser.parse(raw, fuzzy=True)
        return dt.isoformat(), raw
    except Exception:
        return None, raw


def parse_location(text: Optional[str]) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """
    Very tolerant parser for location strings like:
        "Austin, TX, USA"
        "Bengaluru, Karnataka, India"
        "London, United Kingdom"
    Returns (city, state, country, country_code) – any missing part is None.
    """
    if not text:
        return None, None, None, None

    parts = [p.strip() for p in text.split(",")]
    city = state = country = None

    if len(parts) == 3:
        city, state, country = parts
    elif len(parts) == 2:
        city, country = parts
    elif len(parts) == 1:
        country = parts[0]

    country_code = None
    if country:
        try:
            c_obj = pycountry.countries.lookup(country)
            country = c_obj.name
            country_code = c_obj.alpha_2
        except LookupError:
            pass  # keep original string, country_code stays None

    return city or None, state or None, country or None, country_code


def extract_job_id(card: html.HtmlElement, url: Optional[str]) -> Optional[str]:
    """
    Try to obtain a stable job identifier.
    1. Look for common data attributes.
    2. Fall back to the URL – many sites embed the id as a path segment or query param.
    """
    for attr in ("data-job-id", "data-id", "data-jobid", "id"):
        val = card.get(attr)
        if val:
            return val.strip()

    if url:
        parsed = urlparse(url)
        # query‑param based id, e.g. ?jobId=12345
        qs = parse_qs(parsed.query)
        for key in ("jobId", "job_id", "id"):
            if key in qs and qs[key]:
                return qs[key][0]

        # path‑segment based id, e.g. /jobs/12345/software-engineer
        segments = [seg for seg in parsed.path.split("/") if seg]
        if segments:
            # take the first numeric‑looking segment
            for seg in segments:
                if seg.isdigit():
                    return seg
            # otherwise return the last segment (often a slug that contains the id)
            return segments[-1]

    return None


def extract_next_page(tree: html.HtmlElement, current_url: str) -> Optional[str]:
    """Find the URL of the next pagination page, or None."""
    link = first_match(tree, NEXT_LINK_SELECTORS)
    if not link:
        return None
    href = link.get("href")
    if not href:
        return None
    return urljoin(current_url, href)


def build_start_url(base: str, location: str = "USA", india: bool = False) -> str:
    """
    Construct the start URL, optionally adding `country=IN` for India‑only results.
    """
    parsed = urlparse(base)
    query = dict(parse_qsl(parsed.query))
    query["location"] = location
    if india:
        query["country"] = "IN"
    new_query = urlencode(query, doseq=True)
    return parsed._replace(query=new_query).geturl()


def extract_jobs_from_page(tree: html.HtmlElement, page_url: str) -> List[Dict[str, Any]]:
    """Return a list of flat job dicts extracted from a single page."""
    jobs: List[Dict[str, Any]] = []

    # Gather all possible job cards
    cards: List[html.HtmlElement] = []
    for sel in JOB_CARD_SELECTORS:
        cards.extend(tree.cssselect(sel))

    # De‑duplicate – some selectors overlap
    seen = set()
    unique_cards = []
    for c in cards:
        uid = c.get("data-job-id") or c.get("id") or hash(c)
        if uid not in seen:
            seen.add(uid)
            unique_cards.append(c)

    for card in unique_cards:
        title = first_text(card, TITLE_SELECTORS)
        url = first_attr(card, URL_SELECTORS, "href")
        if url:
            url = urljoin(page_url, url)

        location_raw = first_text(card, LOCATION_SELECTORS)
        city, state, country, country_code = parse_location(location_raw)

        date_raw = first_text(card, DATE_SELECTORS)
        date_iso, date_raw = parse_date(date_raw)

        job_id = extract_job_id(card, url)

        job: Dict[str, Any] = {
            "title": title,
            "job_id": job_id,
            "city": city,
            "state": state,
            "country": country,
            "country_code": country_code,
            "url": url,
            "apply_url": url,
            "date_posted": date_iso,
            "date_posted_text": date_raw,
            "job_description": None,
            "employment_type": None,
            "work_type": None,
            "salary_range": None,
        }

        # Optional lightweight fetch for description (kept safe – failures yield null)
        if url:
            try:
                detail_html = fetch(url)
                detail_tree = parse_html(detail_html)
                desc = first_text(
                    detail_tree, [".job-description", "#jobDescription", ".description"]
                )
                job["job_description"] = desc
            except Exception:
                pass  # keep description as null

        jobs.append(job)

    return jobs


# --------------------------------------------------------------------------- #
# Main entry point
# --------------------------------------------------------------------------- #


def main() -> None:
    """
    CLI usage:
        python infosys_scraper.py               # default USA
        python infosys_scraper.py Canada        # location=Canada
        python infosys_scraper.py --india       # location=USA + country=IN
        python infosys_scraper.py India --india # location=India + country=IN
    """
    args = sys.argv[1:]
    india = False
    location = "USA"

    if "--india" in args:
        india = True
        args.remove("--india")

    if args:
        location = args[0]

    start_url = build_start_url(BASE_URL, location=location, india=india)
    next_url: Optional[str] = start_url

    while next_url:
        try:
            page_html = fetch(next_url)
        except Exception as exc:
            print(f"Failed to fetch {next_url}: {exc}", file=sys.stderr)
            break

        tree = parse_html(page_html)
        jobs = extract_jobs_from_page(tree, next_url)

        for job in jobs:
            # Ensure every key exists (they already do) and emit a single‑line JSON.
            print(json.dumps(job, ensure_ascii=False))

        next_url = extract_next_page(tree, next_url)


if __name__ == "__main__":
    main()

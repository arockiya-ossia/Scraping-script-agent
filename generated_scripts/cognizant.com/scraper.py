#!/usr/bin/env python3
"""
Cognizant Careers scraper
Extracts job listings from https://careers.cognizant.com/us-en/
using Playwright (rendered SPA) and follows each job link to collect
additional details via a plain HTTP request.

Output: one JSON object per line (JSONL) written to stdout.
"""

import json
import os
import sys
from urllib.parse import urljoin, urlparse

import requests
from dateutil import parser as dateparser
from lxml import html
import pycountry
from playwright.sync_api import sync_playwright

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
CAREERS_URL = "https://careers.cognizant.com/us-en/"

# --------------------------------------------------------------------------- #
# Helper utilities
# --------------------------------------------------------------------------- #
def absolute_url(base: str, link: str) -> str:
    """Return absolute URL given a base and possibly relative link."""
    return urljoin(base, link)


def get_text_or_none(element):
    return element.text_content().strip() if element is not None else None


def extract_location(text: str):
    """
    Very simple location parser.
    Expected formats:
        "City, State, Country"
        "City, Country"
        "Country"
    Returns (city, state, country, country_code)
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
            country_obj = pycountry.countries.lookup(country)
            country_code = country_obj.alpha_2
        except LookupError:
            country_code = None

    return city, state, country, country_code


def fetch_detail_page(url: str) -> html.HtmlElement:
    """Download a job detail page and return an lxml root element."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    }
    resp = requests.get(url, headers=headers, timeout=30)
    # Ensure proper decoding (UTF‑8 or detected encoding)
    resp.encoding = resp.apparent_encoding or "utf-8"
    return html.fromstring(resp.text)


def safe_css(root: html.HtmlElement, selector: str):
    """Return first element matching selector or None."""
    elems = root.cssselect(selector)
    return elems[0] if elems else None


def extract_job_detail(job_url: str):
    """Extract fields from a single job detail page."""
    root = fetch_detail_page(job_url)

    # ----- title (fallback if not captured on listing) -----
    title_el = safe_css(root, "h1, h2, .job-title, .title")
    title = get_text_or_none(title_el)

    # ----- job description -----
    desc_el = safe_css(
        root,
        ".job-description, .description, #jobDescription, "
        "section.job-description, div[data-test='jobDescription']",
    )
    job_description = get_text_or_none(desc_el)

    # ----- date posted (raw text) -----
    date_el = safe_css(
        root,
        "time, .date-posted, .posted-date, meta[property='article:published_time']",
    )
    date_posted_text = None
    date_posted_iso = None
    if date_el is not None:
        # meta tag handling
        if date_el.tag == "meta" and date_el.get("content"):
            date_posted_text = date_el.get("content").strip()
        else:
            date_posted_text = get_text_or_none(date_el)

        if date_posted_text:
            try:
                dt = dateparser.parse(date_posted_text, fuzzy=True)
                date_posted_iso = dt.isoformat()
            except Exception:
                date_posted_iso = None

    # ----- employment type -----
    emp_type_el = safe_css(
        root,
        ".employment-type, .employmentType, .job-type, .job-type span"
    )
    employment_type = get_text_or_none(emp_type_el)

    # ----- work type (e.g., Remote, On‑site) -----
    work_type_el = safe_css(
        root,
        ".work-type, .workType, .location-type, .job-location-type"
    )
    work_type = get_text_or_none(work_type_el)

    # ----- salary range -----
    salary_el = safe_css(
        root,
        ".salary-range, .salary, .compensation, .pay-range"
    )
    salary_range = get_text_or_none(salary_el)

    # ----- location -----
    location_el = safe_css(
        root,
        ".location, .job-location, .job-location span, .city-state-country"
    )
    location_text = get_text_or_none(location_el)
    city, state, country, country_code = extract_location(location_text)

    # ----- apply URL (often a button/link) -----
    apply_el = safe_css(
        root,
        "a.apply, a.btn-apply, a[data-test='applyButton'], a[data-qa='apply']"
    )
    apply_url = absolute_url(job_url, apply_el.get("href")) if apply_el is not None else None

    # ----- job ID (derived from URL) -----
    parsed = urlparse(job_url)
    # Expected pattern: /us-en/jobs/<job_id>/...
    job_id = None
    path_parts = [p for p in parsed.path.split("/") if p]
    if "jobs" in path_parts:
        idx = path_parts.index("jobs")
        if len(path_parts) > idx + 1:
            job_id = path_parts[idx + 1]

    return {
        "title": title,
        "job_id": job_id,
        "city": city,
        "state": state,
        "country": country,
        "country_code": country_code,
        "url": job_url,
        "apply_url": apply_url,
        "date_posted": date_posted_iso,
        "date_posted_text": date_posted_text,
        "job_description": job_description,
        "employment_type": employment_type,
        "work_type": work_type,
        "salary_range": salary_range,
    }


# --------------------------------------------------------------------------- #
# Main scraper
# --------------------------------------------------------------------------- #
def main():
    # Playwright launch options
    launch_kwargs = {
        "headless": True,
        "args": ["--no-sandbox", "--disable-dev-shm-usage"],
    }
    https_proxy = os.environ.get("HTTPS_PROXY")
    if https_proxy:
        launch_kwargs["proxy"] = {"server": https_proxy}

    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_kwargs)
        context = browser.new_context()
        page = context.new_page()
        # Increase timeout and use a less strict wait condition to avoid
        # premature timeout on pages that never reach "networkidle".
        page.goto(CAREERS_URL, wait_until="load", timeout=120000)

        # Wait for the job cards to be present in the DOM (attached, not necessarily visible)
        try:
            page.wait_for_selector(
                "h2.card-title a.stretched-link",
                state="attached",
                timeout=60000,
            )
        except Exception:
            # If the selector never appears, we continue with an empty page content
            pass

        # Grab the rendered HTML
        content = page.content()
        root = html.fromstring(content)

        # ------------------------------------------------------------------- #
        # Extract job links from the listing page
        # ------------------------------------------------------------------- #
        job_anchors = root.cssselect("h2.card-title a.stretched-link")
        seen = set()
        for anchor in job_anchors:
            href = anchor.get("href")
            if not href:
                continue
            job_url = absolute_url(CAREERS_URL, href)
            if job_url in seen:
                continue
            seen.add(job_url)

            # ---------------------------------------------------------------- #
            # Detail page extraction (plain HTTP request)
            # ---------------------------------------------------------------- #
            job_data = extract_job_detail(job_url)

            # If title was not captured from detail page, fall back to listing text
            if not job_data["title"]:
                job_data["title"] = get_text_or_none(anchor)

            # Ensure required keys exist (they already do, but be explicit)
            for key in [
                "title",
                "job_id",
                "city",
                "state",
                "country",
                "country_code",
                "url",
                "apply_url",
                "date_posted",
                "date_posted_text",
                "job_description",
                "employment_type",
                "work_type",
                "salary_range",
            ]:
                if key not in job_data:
                    job_data[key] = None

            json.dump(job_data, sys.stdout, ensure_ascii=False)
            sys.stdout.write("\n")
            sys.stdout.flush()

        context.close()
        browser.close()


if __name__ == "__main__":
    main()

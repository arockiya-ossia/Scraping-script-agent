#!/usr/bin/env python3
"""
Freshworks (SmartRecruiters) scraper.

Usage:
    python scraper.py > output.jsonl
"""

import sys
import json
import time
from typing import Optional, List

import requests
from lxml import html
from dateutil import parser as date_parser
from pydantic import BaseModel, Field

# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------
BASE_LISTING_URL = "https://careers.smartrecruiters.com/Freshworks"
PAGE_PARAM = "page"
COUNTRY_PARAM = "country"
INDIA_COUNTRY_CODE = "IN"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; FreshworksScraper/1.0; +https://example.com/bot)"
}
REQUEST_TIMEOUT = 15
SLEEP_BETWEEN_REQUESTS = 0.5  # be gentle


# ----------------------------------------------------------------------
# Data model (flat as required)
# ----------------------------------------------------------------------
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
    date_posted_text: Optional[str] = Field(default=None)    # raw string
    job_description: Optional[str] = Field(default=None)
    employment_type: Optional[str] = Field(default=None)
    work_type: Optional[str] = Field(default=None)
    salary_range: Optional[str] = Field(default=None)


# ----------------------------------------------------------------------
# Helper functions
# ----------------------------------------------------------------------
def fetch(url: str) -> str:
    """GET a URL and return its text content."""
    resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.text


def parse_listing_page(html_text: str) -> List[str]:
    """Return a list of job detail URLs found on a listing page."""
    doc = html.fromstring(html_text)
    links = doc.cssselect('li.opening-job a.js-job-ad-link')
    return [a.get('href') for a in links if a.get('href')]


def extract_job_id(job_url: str) -> Optional[str]:
    """
    Job URL pattern:
    https://jobs.smartrecruiters.com/Freshworks/744000136768822-manager-solution-engineering
    The numeric part before the first hyphen is the job id.
    """
    try:
        last_part = job_url.rstrip("/").split("/")[-1]
        job_id = last_part.split("-")[0]
        return job_id
    except Exception:
        return None


def split_address(formatted: str) -> (Optional[str], Optional[str], Optional[str]):
    """
    Expected format: "City, State, Country"
    Any missing component results in None.
    """
    parts = [p.strip() for p in formatted.split(",")]
    city = parts[0] if len(parts) > 0 else None
    state = parts[1] if len(parts) > 1 else None
    country = parts[2] if len(parts) > 2 else None
    return city, state, country


def parse_job_detail(job_url: str) -> JobRecord:
    """Fetch a job detail page and extract required fields."""
    html_text = fetch(job_url)
    doc = html.fromstring(html_text)

    # Title
    title_el = doc.cssselect('h1.job-title[itemprop="title"]')
    title = title_el[0].text_content().strip() if title_el else None

    # Job ID
    job_id = extract_job_id(job_url)

    # Location (spl-job-location)
    loc_el = doc.cssselect('spl-job-location')
    city = state = country = None
    country_code = None
    work_type = None
    if loc_el:
        formatted = loc_el[0].get('formattedaddress')
        if formatted:
            city, state, country = split_address(formatted)
        # work_type from workplacetype attribute (e.g., "remote")
        work_type = loc_el[0].get('workplacetype')

    # Employment type
    emp_el = doc.cssselect('li[itemprop="employmentType"]')
    employment_type = emp_el[0].text_content().strip() if emp_el else None

    # Date posted (optional)
    date_text = None
    date_iso = None
    time_el = doc.cssselect('time[itemprop="datePosted"]')
    if time_el:
        date_text = time_el[0].text_content().strip() or None
        datetime_attr = time_el[0].get('datetime')
        try:
            dt = date_parser.isoparse(datetime_attr) if datetime_attr else date_parser.parse(date_text)
            date_iso = dt.isoformat()
        except Exception:
            date_iso = None

    # Job description
    desc_el = doc.cssselect('div[itemprop="description"]')
    job_description = desc_el[0].text_content().strip() if desc_el else None

    # Salary range – not present in sample
    salary_range = None

    record = JobRecord(
        title=title,
        job_id=job_id,
        city=city,
        state=state,
        country=country,
        country_code=country_code,
        url=job_url,
        apply_url=job_url,
        date_posted=date_iso,
        date_posted_text=date_text,
        job_description=job_description,
        employment_type=employment_type,
        work_type=work_type,
        salary_range=salary_range,
    )
    return record


def scrape_all_jobs() -> None:
    """Iterate over paginated listing pages, fetch each job, and emit JSONL."""
    page = 1
    while True:
        params = {
            PAGE_PARAM: str(page),
            COUNTRY_PARAM: INDIA_COUNTRY_CODE,
        }
        listing_url = BASE_LISTING_URL
        resp = requests.get(listing_url, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        job_links = parse_listing_page(resp.text)

        if not job_links:
            # No more jobs – stop pagination
            break

        for job_url in job_links:
            try:
                record = parse_job_detail(job_url)
                json_line = json.dumps(record.dict(), ensure_ascii=False)
                print(json_line)
                # Respectful pause between job detail requests
                time.sleep(SLEEP_BETWEEN_REQUESTS)
            except Exception as exc:
                # In production you might log this; here we just skip problematic jobs
                sys.stderr.write(f"Error processing {job_url}: {exc}\n")
                sys.stderr.flush()

        page += 1
        time.sleep(SLEEP_BETWEEN_REQUESTS)


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------
if __name__ == "__main__":
    scrape_all_jobs()

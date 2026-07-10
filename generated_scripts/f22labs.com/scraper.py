#!/usr/bin/env python3
"""
Standalone scraper for https://f22labs.com/careers

- The job listings are rendered client‑side → Playwright is required.
- Pagination is not required – a single rendered page contains all jobs.
- Only the information that can be reliably extracted from the rendered
  listing is populated; all other fields are emitted as null.
- Output: one JSON object per line (JSONL) written to stdout.
"""

import json
import os
import sys
import urllib.parse

import requests
from lxml import html
from playwright.sync_api import sync_playwright


CAREERS_URL = "https://f22labs.com/careers"

# --------------------------------------------------------------------------- #
# Helper utilities
# --------------------------------------------------------------------------- #
def extract_job_id(job_url: str) -> str | None:
    """
    Extract the numeric job identifier from a Zoho Recruit URL.
    Example:
        https://f22labs.zohorecruit.in/jobs/Careers/65449000002129032/...
    Returns the numeric part as a string, or None if it cannot be found.
    """
    try:
        path = urllib.parse.urlparse(job_url).path
        # Expected pattern: /jobs/Careers/<numeric_id>/...
        parts = [p for p in path.split("/") if p]
        # Find the first part that looks like a number (all digits)
        for part in parts:
            if part.isdigit():
                return part
    except Exception:
        pass
    return None


def fetch_detail_page(url: str) -> html.HtmlElement | None:
    """
    Retrieve a job‑detail page with a plain HTTP request.
    Returns an lxml HtmlElement tree or None on failure.
    """
    try:
        resp = requests.get(url, timeout=30)
        resp.encoding = resp.apparent_encoding or "utf-8"
        return html.fromstring(resp.text)
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Main scraping logic
# --------------------------------------------------------------------------- #
def main() -> None:
    # Prepare Playwright launch options
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

        # Load the careers page and wait for network activity to settle
        page.goto(CAREERS_URL, wait_until="networkidle")

        # Ensure the job anchors are present
        page.wait_for_selector("h3 a.cw-3-title", timeout=15000)

        # Grab all job link elements
        job_anchors = page.query_selector_all("h3 a.cw-3-title")

        for anchor in job_anchors:
            title = anchor.inner_text().strip() or None
            href = anchor.get_attribute("href") or None

            # Basic validation – skip if href is missing
            if not href:
                continue

            job_id = extract_job_id(href)

            # Build the output record with required fields
            record = {
                "title": title,
                "job_id": job_id,
                "city": None,
                "state": None,
                "country": None,
                "country_code": None,
                "url": href,
                "apply_url": href,
                "date_posted": None,
                "date_posted_text": None,
                "job_description": None,
                "employment_type": None,
                "work_type": None,
                "salary_range": None,
            }

            # OPTIONAL: attempt to fetch the detail page for richer data.
            # The specification allows plain‑HTTP fetching of detail pages.
            detail_tree = fetch_detail_page(href)
            if detail_tree is not None:
                # The exact structure of the detail page is unknown.
                # We therefore leave all additional fields as null,
                # complying with the “no invented selectors” rule.
                pass

            # Emit the JSON line
            json.dump(record, sys.stdout, ensure_ascii=False)
            sys.stdout.write("\n")

        # Clean up
        context.close()
        browser.close()


if __name__ == "__main__":
    main()

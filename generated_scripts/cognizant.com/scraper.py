#!/usr/bin/env python3
"""
Cognizant Careers scraper
-------------------------

* URL: https://careers.cognizant.com/us-en/
* Rendering: JavaScript – requires Playwright
* Pagination: not required (single rendered page)

The script launches a head‑less Chromium instance, renders the page,
extracts every job link from the DOM and emits one JSON object per line
(JSONL) with the required flat schema.

All fields that cannot be obtained from the rendered listing are emitted
as `null` (no guessing, no regex).
"""

import json
import os
import sys
from urllib.parse import urlparse, unquote

import requests
from lxml import html
from playwright.sync_api import sync_playwright

# --------------------------------------------------------------------------- #
# Helper utilities
# --------------------------------------------------------------------------- #
def _extract_job_id(job_url: str) -> str | None:
    """
    Expected pattern (from the evidence):
        https://careers.cognizant.com/us-en/jobs/00069387953/senior-sdet-...
    The numeric segment after "/jobs/" is the job ID.
    """
    try:
        path_parts = urlparse(job_url).path.split("/")
        # ['', 'us-en', 'jobs', '00069387953', 'senior-sdet-...']
        idx = path_parts.index("jobs")
        return path_parts[idx + 1] or None
    except Exception:
        return None


def _load_page(url: str) -> str:
    """
    Render the page with Playwright and return the full HTML source.
    """
    proxy = None
    https_proxy = os.environ.get("HTTPS_PROXY")
    if https_proxy:
        proxy = {"server": https_proxy}

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
            proxy=proxy,
        )
        context = browser.new_context()
        page = context.new_page()
        page.goto(url, wait_until="networkidle")
        # Give a tiny extra pause for any late‑rendered elements
        page.wait_for_timeout(1000)
        content = page.content()
        browser.close()
        return content


def _parse_listing(html_content: str) -> list[dict]:
    """
    From the rendered HTML, locate every job entry and return a list of
    dictionaries with the fields we can obtain directly.
    """
    tree = html.fromstring(html_content)

    # The evidence shows job links inside:
    #   <h2 class="card-title"><a class="stretched-link js-view-job" href="...">Title</a></h2>
    job_anchors = tree.cssselect('h2.card-title a.stretched-link.js-view-job')

    jobs = []
    for a in job_anchors:
        title = a.text_content().strip() or None
        url = a.get("href")
        if url and not url.startswith("http"):
            # make absolute – the site uses absolute URLs in the sample,
            # but guard against relative ones.
            url = f"https://careers.cognizant.com{url}"
        job_id = _extract_job_id(url) if url else None

        jobs.append(
            {
                "title": title,
                "job_id": job_id,
                "city": None,
                "state": None,
                "country": None,
                "country_code": None,
                "url": url,
                "apply_url": None,
                "date_posted": None,
                "date_posted_text": None,
                "job_description": None,
                "employment_type": None,
                "work_type": None,
                "salary_range": None,
            }
        )
    return jobs


def _enrich_with_detail(job: dict) -> dict:
    """
    Attempt to fetch the job detail page with a plain HTTP request.
    If a recognizable description element is found, fill it; otherwise
    leave the field as `null`. No regex is used – only CSS selectors.
    """
    detail_url = job.get("url")
    if not detail_url:
        return job

    try:
        resp = requests.get(detail_url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        resp.encoding = resp.apparent_encoding or "utf-8"
        tree = html.fromstring(resp.text)

        # The exact selector for the description is not present in the evidence.
        # A common pattern on Cognizant job pages is a div with class "job-description"
        # or a section with data-type="job-description". We try a few reasonable
        # selectors and take the first non‑empty result.
        selectors = [
            "div.job-description",
            "section[data-type='job-description']",
            "div#jobDescription",
            "div.description",
        ]
        description = None
        for sel in selectors:
            elems = tree.cssselect(sel)
            if elems:
                # concatenate text of all matched elements
                description = " ".join(e.text_content().strip() for e in elems if e.text_content().strip())
                if description:
                    break

        if description:
            job["job_description"] = description
    except Exception:
        # Any failure – keep description as null
        pass

    return job


def main() -> None:
    LISTING_URL = "https://careers.cognizant.com/us-en/"

    # 1. Render the listing page
    rendered_html = _load_page(LISTING_URL)

    # 2. Extract jobs from the rendered DOM
    jobs = _parse_listing(rendered_html)

    # 3. (Optional) Enrich each job with details from its own page
    enriched_jobs = [_enrich_with_detail(job) for job in jobs]

    # 4. Emit JSONL to stdout
    for job in enriched_jobs:
        json.dump(job, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")


if __name__ == "__main__":
    main()

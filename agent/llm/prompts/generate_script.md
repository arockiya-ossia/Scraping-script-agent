# Generate Script

Write a standalone Python scraper for domain `{domain}` using the confirmed
evidence below:

```
{evidence_json}
```

## Real sample from the actual page/API (write selectors/paths against THIS)

```
{evidence_sample}
```

Write every CSS selector, JSON path, and dict key access against the exact
shape shown above — not against what this platform "usually" looks like.
If a field you need isn't visible in the sample, still write reasonable
extraction code for it (it may appear on other job postings), but never
invent a selector you can't tie back to something in the sample or the
evidence above.

## Choose the right fetch strategy from `source_type` / `requires_firecrawl` / `requires_browser`

- If `requires_firecrawl` is `true`: the site edge-blocks plain HTTP **and**
  plain Playwright (both were blocked during investigation; only Firecrawl's
  stealth rendering reached the listing). The scraper MUST fetch the rendered
  HTML via the **Firecrawl API** at runtime, then parse it locally with
  `lxml`. Firecrawl is a fetch/render service, not an LLM — this does not
  violate "no LLM calls at runtime". Do NOT use Playwright here (it will be
  blocked) and do NOT use plain `requests` on the listing page (403).
  - Read the key from `os.environ["FIRECRAWL_API_KEY"]` (already set in the
    sandbox). Call it with `httpx`/`requests`:
    `POST https://api.firecrawl.dev/v1/scrape`, header
    `Authorization: Bearer <key>`, JSON body `{{"url": <page_url>, "formats": ["html"]}}`.
    The rendered HTML is at `resp.json()["data"]["html"]` — parse THAT with
    `lxml`, extracting job links/fields with CSS selectors (never regex).
  - If `pagination_status` is `confirmed` (a load-more/next control), pass
    Firecrawl `actions` in the body to drive it, e.g.
    `"actions": [{{"type": "click", "selector": <ctrl>}}, {{"type": "wait", "milliseconds": 1500}}]`
    repeated/looped, then read the final rendered HTML. If `not_required`, one
    scrape is the whole listing — no pagination loop.
  - Individual job-detail pages, if separately fetchable, may still use plain
    `requests`; only the blocked listing needs Firecrawl.
- If `requires_browser` is `true` (source_type `spa_rendered`, and NOT
  `requires_firecrawl`): the job
  listing exists ONLY after JavaScript renders — a plain `requests`/`httpx`
  fetch returns none of it. You **must** use **Playwright** (sync API) to
  launch Chromium, `page.goto(url, wait_until="networkidle")`, then extract
  jobs from `page.content()` (parse with `lxml`) or via `page.query_selector_all`.
  - Launch Chromium headless with
    `args=["--no-sandbox", "--disable-dev-shm-usage"]` (required to run as
    root inside Docker) and, if `os.environ.get("HTTPS_PROXY")` is set, pass
    `proxy={{"server": os.environ["HTTPS_PROXY"]}}` to
    `p.chromium.launch(...)` so egress goes through the sandbox proxy.
    Use the **sync** API (`from playwright.sync_api import sync_playwright`).
  - If `pagination_status` is `not_required`, one rendered page is the
    complete listing — do NOT invent a pagination loop. If it is `confirmed`
    (a "load more"/"next" control), loop: click the control and wait, until
    it disappears or the job count stops growing (cap the loop, e.g. 50
    iterations, so a broken control can't spin forever).
  - Individual job-detail pages may be plain-HTTP fetchable even when the
    listing isn't — you may use `requests` for detail pages if that's
    simpler, but the LISTING enumeration must go through Playwright.
- Otherwise (`requires_browser` false — API or true SSR): use
  `requests`/`httpx` + `lxml`/`jmespath`. Do NOT use Playwright for these;
  it's slower and unnecessary.

## Hard requirements

- Output must be a single, self-contained Python file runnable as
  `python scraper.py > output.jsonl` with no dependency on this agent process
  and **no LLM calls at runtime**.
- Available libraries in the sandbox: `requests`/`httpx`, `lxml`, `cssselect`,
  `jmespath`, `python-dateutil`, `pydantic`, `pycountry`, `playwright` (sync
  API; browsers are pre-installed — use ONLY when `requires_browser` is true),
  and `pypdf` (only if the evidence says the source is a PDF — fetch the PDF
  URL directly and parse with `pypdf.PdfReader`). The Firecrawl API is
  allowed at runtime **only** when `requires_firecrawl` is true (see the fetch
  strategy above) — it is a render service, not an LLM. Never call any LLM API
  from the generated script.
- **Never use `re` or regex** for field extraction. Use CSS selectors
  (`lxml`/`cssselect`) or JSON paths (`jmespath`) only.
- Prefer server-side India filtering (the confirmed query param) over
  pulling everything and filtering client-side. Only filter client-side if
  `india_filter_mechanism == "client_side_fallback"`.
- Parse dates with `dateutil.parser` into `date_posted` (ISO), keep the raw
  string in `date_posted_text`. Never regex-parsed.
- Derive `city`/`state`/`country`/`country_code` via string operations
  (`.split(",")`, containment checks, or `pycountry`) — never regex, never
  inferred from free-text job descriptions.
- Any field not structurally present must be emitted as `null` — never guess
  or hallucinate.
- Decode HTTP responses as UTF-8 explicitly. With `requests`, set
  `resp.encoding = resp.apparent_encoding` (or `"utf-8"`) before reading
  `resp.text` — don't rely on the guessed default, or curly quotes/accented
  letters in job titles and descriptions come out mojibake (’ instead of ').
  With `httpx`, prefer decoding `resp.content` as UTF-8 explicitly.
- Implement the **full** pagination loop confirmed during investigation, not
  just the first page.
- Emit one JSON object per line (JSONL). Each line is a **flat object with
  exactly these top-level keys** — no wrapper object, no nesting under
  "properties" or anything else, just this shape with real values filled in
  (or `null` where genuinely missing):

```json
{job_record_example}
```

Return only the Python source, no prose.

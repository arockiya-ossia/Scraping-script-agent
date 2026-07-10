# Rewrite

The generated scraper for domain `{domain}` failed validation in a way that
indicates the underlying approach — not just a small bug — was wrong.

## Evidence (still considered valid)

```
{evidence_json}
```

## Real sample from the actual page/API (write selectors/paths against THIS)

```
{evidence_sample}
```

## Previous script (for reference — do not patch, start fresh)

```python
{script_code}
```

## Failure that triggered this rewrite

- Category: `{failure_category}`
- Details: {failure_details}

## Required output shape — flat object, exactly these keys, no wrapper

```json
{job_record_example}
```

Write a new standalone scraper from scratch that addresses this failure,
following every requirement in CLAUDE.md §10 (no regex, CSS selectors/JSON
paths only, null for missing fields, full pagination, server-side India
filtering when available). Each output line must match the flat shape
above exactly — no wrapper object. Decode HTTP responses as UTF-8 explicitly
(e.g. `resp.encoding = resp.apparent_encoding` before `resp.text` with
`requests`) so curly quotes/accented letters don't come out mojibake.

Fetch strategy (in priority order):
- If `requires_firecrawl: true`, the site edge-blocks both plain HTTP and
  Playwright — fetch the rendered HTML via the **Firecrawl API** at runtime:
  `POST https://api.firecrawl.dev/v1/scrape`, header
  `Authorization: Bearer os.environ["FIRECRAWL_API_KEY"]`, JSON body
  `{{"url": <page_url>, "formats": ["html"]}}`; parse `resp.json()["data"]["html"]`
  with `lxml`. For a `confirmed` load-more control, drive it via Firecrawl
  `actions`. Firecrawl is a render service, not an LLM — allowed at runtime.
  Do NOT use Playwright or plain `requests` on the blocked listing.
- Else if `requires_browser: true` / `source_type: spa_rendered`, the listing
  only exists after JavaScript renders — use **Playwright** (sync API),
  launching Chromium through the egress proxy in `HTTPS_PROXY`
  (`proxy={{"server": os.environ["HTTPS_PROXY"]}}` when that env var is set),
  and extract from the rendered DOM.
- Else use `requests`/`httpx` — never Playwright for a plain API/SSR source.

If `pagination_status` is `not_required`, do not invent a pagination loop.

Return only the Python source.

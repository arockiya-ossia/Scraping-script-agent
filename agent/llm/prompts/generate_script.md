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

## Hard requirements

- Output must be a single, self-contained Python file runnable as
  `python scraper.py > output.jsonl` with no dependency on this agent process
  and **no LLM calls at runtime**.
- Only these libraries are available in the sandbox: `requests`/`httpx`,
  `lxml`, `cssselect`, `jmespath`, `python-dateutil`, `pydantic`.
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

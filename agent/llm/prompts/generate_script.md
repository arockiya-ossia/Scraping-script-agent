# Generate Script

Write a standalone Python scraper for domain `{domain}` using the confirmed
evidence below:

```
{evidence_json}
```

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
- Implement the **full** pagination loop confirmed during investigation, not
  just the first page.
- Emit one JSON object per line (JSONL), each shaped like `JobRecord`:

```
{job_record_schema}
```

Return only the Python source, no prose.

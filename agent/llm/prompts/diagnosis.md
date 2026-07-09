# Failure Diagnosis

The scraper for domain `{domain}` ran, but validation reported a failure.
Classify **why** it failed into exactly one `FailureCategory`:

- `SYNTAX_ERROR` — the script didn't parse/import.
- `RUNTIME_ERROR` — it crashed while running.
- `HTTP_FORBIDDEN` — the source blocked the request (403/challenge page).
- `TIMEOUT` — the run exceeded the sandbox wall-clock limit.
- `SCHEMA_DRIFT` — the API/HTML response shape no longer matches what was
  assumed during investigation.
- `PAGINATION_UNDERCOUNT` — row count is materially below the source's own
  reported total.
- `ZERO_RESULTS_FILTER_MISMATCH` — zero rows because the wrong filter
  param/value was sent to the source.
- `ZERO_RESULTS_PARSING_BUG` — data came back fine, but our parsing logic
  produced zero/garbage rows.
- `CONTAINS_REGEX` — static check caught `import re` or regex usage.
- `OTHER` — anything that doesn't fit the above.

## Facts

- Validation report: `{validation_report_json}`
- stderr / stdout excerpt: {stderr_excerpt}

Only use facts given above — do not speculate beyond them. Return the
category and a one-paragraph justification.

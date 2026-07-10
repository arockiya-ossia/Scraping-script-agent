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
Return only the Python source.

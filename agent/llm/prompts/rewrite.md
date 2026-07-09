# Rewrite

The generated scraper for domain `{domain}` failed validation in a way that
indicates the underlying approach — not just a small bug — was wrong.

## Evidence (still considered valid)

```
{evidence_json}
```

## Previous script (for reference — do not patch, start fresh)

```python
{script_code}
```

## Failure that triggered this rewrite

- Category: `{failure_category}`
- Details: {failure_details}

Write a new standalone scraper from scratch that addresses this failure,
following every requirement in CLAUDE.md §10 (no regex, CSS selectors/JSON
paths only, null for missing fields, full pagination, server-side India
filtering when available). Return only the Python source.

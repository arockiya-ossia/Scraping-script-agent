# Patch

The generated scraper below failed validation with a **targeted, fixable**
issue — do not rewrite it from scratch, edit only what's broken.

## Current script

```python
{script_code}
```

## Failure

- Category: `{failure_category}`
- Details: {failure_details}
- stderr / stdout (if any): {stderr_excerpt}

## Instructions

Make the smallest change that fixes the diagnosed issue while preserving
every other requirement from CLAUDE.md §10 (no regex, null-for-missing,
full pagination, CSS/JSON-path extraction only). Return the complete,
corrected Python source — not a diff.

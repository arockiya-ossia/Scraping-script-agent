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

## Real sample from the actual page/API (if the bug is a selector/path mismatch)

```
{evidence_sample}
```

## Required output shape — flat object, exactly these keys, no wrapper

```json
{job_record_example}
```

## Instructions

Make the smallest change that fixes the diagnosed issue while preserving
every other requirement from CLAUDE.md §10 (no regex, null-for-missing,
full pagination, CSS/JSON-path extraction only). If the bug is that output
lines don't match the required flat shape above (e.g. wrapped in a
"properties"/"title"/"type" object, or otherwise nested), that is the fix —
emit the flat shape directly.

If the category is `mojibake_encoding`: text fields contain artifacts like
’ or “ where a plain apostrophe/quote should be — this means the HTTP
response bytes were decoded with the wrong charset somewhere. With
`requests`, set `resp.encoding = resp.apparent_encoding` (or hardcode
`"utf-8"` if you know the source serves UTF-8) *before* reading `resp.text`;
with `httpx`, prefer `resp.content.decode("utf-8")` over relying on a
guessed default. Do not "fix" this by post-processing the mojibake string
with string replacement — fix the decode step itself.

Return the complete, corrected Python source — not a diff.

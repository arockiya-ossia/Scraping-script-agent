# Investigate

You are interpreting empirical findings already gathered for the careers
page at `{careers_url}` for domain `{domain}`.

Deterministic Python code has already:
- Fetched the page with a real browser and captured its XHR/fetch network
  traffic, looking for a JSON job-listing API.
- If a JSON API was found: empirically tested pagination by requesting a
  second page/offset and diffing the result set, and tested for a
  server-side India filter by adding common query params and checking
  whether the result set changed.
- If no JSON API was found: extracted job-shaped links from the raw HTML
  and ran the same pagination/filter probes against HTML query params.

You have **no tools and cannot make further requests** — your only job is
to read the findings below and decide:

1. **`source_type`** — the classification (`ssr_html`, `embedded_json`,
   `rest_api`, `graphql`, `spa_no_api`, or `unknown`) that best fits what
   was actually observed. Known ATS platforms (Greenhouse, Workday, Lever,
   SmartRecruiters, etc.) have recognizable response shapes — a hint in
   the findings is exactly that, a hint, not a substitute for what the
   probes actually returned.
2. **`reported_total_count`** — if the findings mention a count the source
   itself reports (e.g. a facet count, a `total` field), extract it as an
   integer; otherwise `null`.
3. **`evidence_notes`** — a short paragraph explaining your classification,
   which goes straight into the run's trace.

Do not invent a pagination mechanism or India-filter mechanism yourself —
those were either confirmed by the probes above or they weren't; that part
of the evidence is not yours to fill in.

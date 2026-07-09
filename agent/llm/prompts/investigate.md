# Investigate

You are investigating the careers page at `{careers_url}` for domain `{domain}`.

You have access to `fetch_url` (Playwright, with network capture) and
`probe_endpoint` (direct httpx). Use them to empirically determine:

1. **Source type** — is job data server-rendered HTML, embedded JSON in the
   page (e.g. a `<script type="application/json">` blob), a REST API, a
   GraphQL API, or a JS-only SPA with no discoverable API?
2. **Pagination mechanism** — offset/limit, cursor, page number, or infinite
   scroll. You must confirm this by actually calling `probe_endpoint` with a
   different page/offset value and observing that the result set changes —
   never guess from the UI alone.
3. **India filter mechanism** — is there a query param (e.g. `country=IN`,
   `location=India`) that filters server-side? If not, note
   `client_side_fallback` so the generated script pulls all jobs and filters
   client-side.
4. **Reported total count** — if the source itself exposes a total job count,
   record it; it's used later for pagination-undercount sanity checks.

Known ATS platforms (Greenhouse, Workday, Lever, SmartRecruiters, etc.) have
recognizable response shapes — treat a match as a **hint only**. Every fact
must still be confirmed empirically via `probe_endpoint`/`fetch_url` before
being trusted (CLAUDE.md Constraint #2).

Return an `InvestigationEvidence` object. Put your reasoning in
`evidence_notes` — it goes straight into the trace.

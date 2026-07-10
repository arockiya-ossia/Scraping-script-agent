# job-scraper-agent

An AI agent that **writes scraper code** — it doesn't scrape jobs itself.
Given only a company domain (`razorpay.com`), it finds the real careers
page, figures out how the job data is actually served, generates a
**standalone Python scraper** for that specific site, runs it in a sandbox,
validates the output, and self-repairs on failure — unattended. The
generated scraper runs with **no LLM calls and no browser at runtime**.


## Quick start

```bash
python -m venv venv
source venv/Scripts/activate        # PowerShell: venv\Scripts\Activate.ps1
pip install -e ".[dev]"
playwright install chromium
cp .env.example .env                # fill in FREELLMAPI_*, SERPER_API_KEY; FIRECRAWL_API_KEY optional
docker compose up -d egress-proxy   # once per session
python scripts/run_agent.py --domain razorpay.com
pytest tests/ -q
```

Running it prints live progress — every tool call, decision, and
validation, as it happens — not just a final status line.

## How it flows

```
domain
  │
  ▼
discover ──▶ investigate ──▶ evidence_check ──(insufficient, budget--)──▶ back to investigate
                                   │ (sufficient)
                                   ▼
                          generate_script ──▶ docker_execute ──▶ validate ──▶ pass ──▶ finalize
                                   ▲                                            │
                                   │                                          fail
                                   │                                            ▼
                                   │                                  failure_diagnosis
                                   │                                            │
                                   └──────────── repair_strategy ◀──────────────┘
                                        (patch → docker_execute, rewrite → generate_script,
                                         re-investigate → investigate)
```

A single global attempt budget (default 8) is shared across both loop-back
points. When it hits zero, the run ends with an honest `status=failed` —
never a hang, never a fabricated result.

## What each node does

- **`discover`** — domain → careers URL. Searches, probes common paths, and
  follows links to a known ATS platform (Greenhouse, Lever, Workday,
  SmartRecruiters, Zoho Recruit, etc. — a horizontal fingerprint list, never
  hardcoded per company).
- **`investigate`** — the core node. Fetches the page with Playwright,
  captures every network request, and empirically determines: is there a
  JSON API (REST or GraphQL)? Is it paginated, and by what mechanism? Is
  there a server-side India filter? Every fact here comes from an actual
  probe diff — never an LLM guess. If nothing's found passively, it tries
  (in order): following an ATS link found only after JS renders, bounded
  browser interactions (click/scroll/fill), and — capped to once per run —
  a Firecrawl escalation. The LLM's only job here is interpreting the
  gathered facts into a `source_type` classification.
- **`evidence_check`** — a plain Python gate (`is_sufficient()`), never an
  LLM self-assessment.
- **`generate_script`** — the LLM writes the actual scraper, grounded in a
  real sample of the page/API (not guessed from training-data memory of
  "what this platform usually looks like").
- **`docker_execute`** — runs the generated script in an isolated,
  network-restricted Docker sandbox.
- **`validate`** — deterministic checks: no regex, no mojibake, valid
  JSONL, required fields present, all locations are India, sample URLs
  resolve.
- **`failure_diagnosis` → `repair_strategy`** — classifies *why* it failed,
  then routes to a targeted patch, a full rewrite, or fresh investigation,
  based on a real routing table (not blind retry).
- **`finalize`** — writes the trace, the cost report, and (if it failed)
  makes sure that's stated honestly.

## What you get per domain

```
generated_scripts/{domain}/
  scraper.py          # runs standalone, no LLM/browser needed
  output.jsonl         # the scraped India jobs
  cost_report.json     # tokens, repair attempts, Firecrawl credits
  confidence.json      # per-field confidence, keyed by job_id
traces/{domain}_{run_id}.jsonl   # every tool call/decision/evidence/execution/validation
artifacts/{domain}/              # raw HTML, network captures, each script revision
```

## Two extra safety nets

- **Cross-run memory** (`agent/memory/`) — when a pagination/filter param
  is confirmed for a given *platform shape* (ATS fingerprint + normalized
  URL/response structure — never a domain name), it's remembered and tried
  first next time the same shape shows up on an unrelated company. Never a
  substitute for re-confirming — just a faster first guess.
- **Firecrawl escalation** — only reached when Playwright/httpx have
  demonstrably failed (blocked with `403`, zero interactive DOM nodes
  found, or the URL is a PDF). Capped to one attempt per run since credits
  are a real, paid, scarce resource — `cost_report.json` tracks the exact
  spend via a measured before/after balance delta, not an estimate.

## The sandbox's network policy

The scraper container has no direct internet route — only egress through a
Squid proxy sidecar that denies cloud metadata (`169.254.169.254`) and all
RFC1918 private ranges, allows everything else. Verify it's actually
enforced, not just documented:

```bash
docker run --rm --network sandbox_net -e HTTPS_PROXY=http://egress-proxy:3128 curlimages/curl \
  curl -s -o /dev/null -w "%{http_code}\n" https://169.254.169.254/   # expect 000 (blocked)
```

## Known limitations

- **Sites where the job listing is 100% client-rendered with no
  discoverable API at all** can't be replicated by the sandbox's
  `requests`-only runtime — confirmed directly on one real domain (even
  Firecrawl found nothing beyond what Playwright already saw). This
  produces an honest failure, not a fabricated result — it's a real
  architectural boundary (no browser engine at scraper runtime), not a bug.
- Heavily bot-protected domains may still honestly fail even after the
  Firecrawl `HTTP_FORBIDDEN` retry.
- The job-vs-navigation-link heuristics (word count, generic-word
  filtering) are tuned against every real domain this system has been
  tested on, but are fuzzy by nature — a lightweight, generalizable
  heuristic beats a hardcoded per-domain rule, even at the cost of not
  being flawless.

## Testing

```bash
pytest tests/ -q
```

90 tests: every model, the graph's routing/budget logic in isolation, the
job-link classification heuristics against real-world URL patterns from
every ATS platform this system has hit, pagination/filter mutation logic
for REST/POST/GraphQL, the no-regex and no-mojibake validator checks, cross-run
memory signature computation, Firecrawl credit-delta tracking, and a fully
deterministic browser-interaction test (a local HTML fixture with no live
website, so it can't flake).

Verified live against 4 real ATS platforms with zero orchestrator code
changes between them — Greenhouse, SmartRecruiters, Workday (via its POST
API), and Lever — plus honest-failure verification against a bot-protected
domain and a genuinely unscrapable-within-constraints one.

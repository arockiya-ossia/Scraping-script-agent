# job-scraper-agent

An AI agent whose job is to **write code**, not scrape jobs itself. Given only a
company domain (e.g. `razorpay.com` — no careers URL, no other input), it
discovers the real careers page, empirically investigates how the job data is
actually served, generates a **standalone Python scraper** for that specific
site, runs it in a sandbox, validates the output, and self-repairs on failure
— entirely unattended. The generated scraper itself runs with **no LLM calls
and no browser at runtime**.

Full build spec: [CLAUDE.md](CLAUDE.md). This document explains how the
implementation actually works, end to end, including every real bug found and
fixed along the way — because several of those bugs reveal load-bearing
design decisions that aren't obvious from the code alone.

---

## Table of contents

1. [Quick start](#quick-start)
2. [High-level architecture](#high-level-architecture)
3. [The LangGraph state machine](#the-langgraph-state-machine)
4. [AgentState — what flows through the graph](#agentstate--what-flows-through-the-graph)
5. [Node-by-node walkthrough](#node-by-node-walkthrough)
6. [Tools](#tools)
7. [Models](#models)
8. [LLM prompts](#llm-prompts)
9. [The Docker sandbox](#the-docker-sandbox)
10. [The trace system](#the-trace-system)
11. [The Firecrawl escalation ladder](#the-firecrawl-escalation-ladder)
12. [Validation checks](#validation-checks)
13. [The repair loop](#the-repair-loop)
14. [Known bugs found and fixed](#known-bugs-found-and-fixed-a-running-log)
15. [Known limitations](#known-limitations)
16. [Repository structure](#repository-structure)
17. [Testing](#testing)

---

## Quick start

```bash
python -m venv venv
source venv/Scripts/activate        # Windows Git Bash; venv\Scripts\Activate.ps1 for PowerShell
pip install -e ".[dev]"
playwright install chromium
cp .env.example .env                # fill in real API keys — see below
docker compose up -d egress-proxy   # once per session; see "Sandbox" below
python scripts/run_agent.py --domain razorpay.com
pytest tests/ -q
```

### Required `.env` keys

| Key | Used for |
|---|---|
| `FREELLMAPI_BASE_URL`, `FREELLMAPI_API_KEY`, `FREELLMAPI_MODEL` | All LLM calls (investigation interpretation, codegen, patch/rewrite, diagnosis). `FREELLMAPI_MODEL=auto` lets the router pick — pinning to a specific model can hit provider-routing quirks (see [bug log](#known-bugs-found-and-fixed-a-running-log)). |
| `SERPER_API_KEY` | `discover`'s web search |
| `FIRECRAWL_API_KEY` | Optional — only needed for the three escalation paths (§11). The system works without it; those paths just get skipped. |
| `SANDBOX_*`, `EGRESS_PROXY_HOST` | Docker sandbox resource limits and network config — see [`.env.example`](.env.example) for the full list |

Running it prints **live progress to the terminal** — every tool call, decision,
evidence classification, code revision, execution, and validation, as it
happens (`trace_sink.console = True` in `run_agent.py`), not just a final
status line.

---

## High-level architecture

```
domain
  │
  ▼
┌──────────┐   ┌─────────────┐   ┌────────────────┐
│ discover │──▶│ investigate │──▶│ evidence_check  │  (deterministic gate)
└──────────┘   └─────────────┘   └────────┬────────┘
                     ▲                    │
                     │ (insufficient,     │ (sufficient)
                     │  budget--)         ▼
                     │            ┌────────────────┐   ┌────────────────┐   ┌──────────┐
                     └────────────│ generate_script │──▶│ docker_execute │──▶│ validate │
                                  └────────────────┘   └────────────────┘   └────┬─────┘
                                          ▲                                      │
                                          │                              ┌───────┴───────┐
                                          │                          pass│               │fail
                                          │                              ▼               ▼
                                          │                         ┌─────────┐   ┌───────────────────┐
                                          │                         │ finalize│   │ failure_diagnosis  │
                                          │                         └─────────┘   └─────────┬──────────┘
                                          │                                                 ▼
                                          │                                       ┌───────────────────┐
                                          └───────────────────────────────────────│  repair_strategy   │
                                                       (rewrite)                  └─────────┬──────────┘
                                                                          patch ◀────────────┼──────────▶ re-investigate
                                                                            │                              │
                                                                            ▼                              ▼
                                                                      docker_execute                  investigate
```

Two things make this an actual state machine rather than a fixed pipeline:

1. **A single global attempt budget** (`total_attempts`, default 8 —
   `MAX_TOTAL_ATTEMPTS`) shared across *both* loop-back points: the
   evidence-insufficient loop (`evidence_check` → `investigate`) and the
   repair loop (`validate` fail → `repair_strategy` → wherever it routes).
   When it hits zero, the graph routes straight to `finalize` with an honest
   `status=failed` — never a fabricated success, never a hang.
2. **A real routing table**, not "retry blindly": `failure_diagnosis`
   classifies *why* validation failed into one of 9 categories, and
   `repair_strategy` maps each category to a specific action (`patch`,
   `rewrite`, or `re-investigate`) via `agent/nodes/repair_strategy.py`'s
   `ROUTING_TABLE` — a real `dict`, not just documentation.

---

## The LangGraph state machine

Built in `agent/graph.py`. Every node is wrapped in `@traced`
(`agent/nodes/__init__.py`), which automatically emits a `node_enter`/
`node_exit` pair to the trace sink — no node can silently skip logging, and
you don't have to remember to instrument a new one.

**Load-bearing gotcha**: LangGraph only persists state mutations returned by a
*node function*. Mutating `state` inside a **conditional-edge function**
(the function passed to `add_conditional_edges` that returns a routing
string) does **not** persist — it's purely a read for deciding where to go
next. This caused a real infinite loop early on: the budget decrement was
originally inside the conditional-edge function, so `total_attempts` never
actually decreased, and `evidence_check` looped until LangGraph's recursion
limit killed the process. Fixed by moving the decrement into the
`evidence_check` **node** itself; the edge function (`_route_evidence` in
`graph.py`) now only reads state, never writes it.

### Conditional edges

| From | Condition | To |
|---|---|---|
| `evidence_check` | `evidence.is_sufficient()` | `generate_script` |
| `evidence_check` | not sufficient, budget left | `investigate` (loop) |
| `evidence_check` | not sufficient, budget exhausted | `finalize` |
| `validate` | `report.passed` | `finalize` |
| `validate` | failed, budget left | `failure_diagnosis` |
| `validate` | failed, budget exhausted | `finalize` |
| `repair_strategy` | routing table says `patch` | `docker_execute` |
| `repair_strategy` | routing table says `rewrite` | `generate_script` |
| `repair_strategy` | routing table says `re-investigate` | `investigate` |

---

## AgentState — what flows through the graph

`agent/state.py`. The original spec (§6.5) defines a minimal `TypedDict`;
several fields were added on top of it during real implementation, each
because something genuinely needed to cross a node boundary and there was no
other place for it to live:

| Field | Added because |
|---|---|
| `last_stderr`, `last_exit_code`, `last_timed_out` | Set by `docker_execute`, read by `validate` — a sandbox failure has to reach the classifier somehow. |
| `evidence_sample_path` | Set by `investigate`, read by `generate_script` — a path into `artifacts/` (per the "never inline full payloads into state" rule) pointing at a concrete HTML/JSON sample of the real page, so codegen writes selectors against actual markup instead of guessing from training-data memory. |
| `script_revision` | Incremented on every `generate_script` call and every `repair_strategy` patch — feeds the trace's `code_generated` events and archives each revision to `artifacts/`. |
| `run_id` | Trace file discriminator: `traces/{domain}_{run_id}.jsonl`, matching the spec's naming exactly (a real unix timestamp, not the placeholder `"run"` it started as). |
| `firecrawl_actions_attempted` | Caps the Firecrawl Actions escalation to once per run — without it, the evidence-insufficient retry loop would re-burn a real, paid Firecrawl credit on the identical answer every single attempt. |

---

## Node-by-node walkthrough

### `discover` — domain → careers URL

`agent/nodes/discover.py`. Pure Python, **no LLM call** — cheap
reconnaissance, not a decision that needs judgment.

1. `search_web(f"{domain} careers jobs")` (Serper).
2. Filter results to links containing the domain plus "career"/"job".
3. Fall back to probing common paths (`/careers`, `/jobs`, `/en/careers`,
   `/company/careers`, etc.) via plain `httpx` (`probe_endpoint`).
4. **Follow ATS links**: parse the first responding page for `<a>` hrefs
   whose *resolved host* matches a known ATS platform
   (`ATS_DOMAIN_MARKERS` — Greenhouse, Lever, Workday, SmartRecruiters,
   iCIMS, Workable, Ashby, Jobvite, BambooHR, Zoho Recruit, Freshteam,
   Keka, Darwinbox, Recruitee, Breezy). This is a **horizontal
   fingerprint list** (CLAUDE.md §2 #2 explicitly allows this — it's a
   pattern used across thousands of companies, not a per-company
   hardcode) and is only ever a *hint*: nothing here is trusted without
   `investigate`'s empirical confirmation afterward.
5. Among multiple matching ATS links, picks the **shortest** one (fewest
   path segments) — a marketing page can link to several individual job
   postings before it links the actual listing/search page, and the
   listing URL is generally the shorter one.
6. Guards: rejects `mailto:`/`tel:`/`javascript:`/`#` links, and rejects
   any href containing unrendered client-templating placeholder syntax
   (`{{`, `}}`, `${`, `<%`, `%>`) — a widget that errored out mid-hydration
   can leave a literal `/jobs/{{cxPropShortenUrl}}` in the DOM, which is
   short and would otherwise win the "shortest URL" tiebreak over every
   real candidate.
7. Matches the ATS-domain marker against the **resolved URL's host**, not
   a raw substring of the href — a substring check would false-positive on
   a `mailto:` share link whose URL-encoded body text happens to *contain*
   the ATS domain as plain text.

### `investigate` — careers URL → `InvestigationEvidence`

`agent/nodes/investigate.py`. By far the largest node. Empirical gathering
is deterministic Python; the LLM's job is narrowly scoped to interpreting
ambiguous signals (`source_type` classification, `reported_total_count`)
— `pagination_param_confirmed` is **never** set from the LLM's say-so, only
from an actual `probe_endpoint` diff (CLAUDE.md §6.2).

**Escalation checks, in order, before the normal fetch:**

- **PDF**: a `HEAD` probe checks content-type. If `application/pdf`, skip
  straight to Firecrawl's native PDF parsing (§11).
- **HTTP_FORBIDDEN retry**: if the *prior* validation failed with
  `HTTP_FORBIDDEN` (carried in `state["validation_report"]` from the last
  repair cycle), fetch via Firecrawl instead of Playwright this pass.

**Normal fetch and classification** (`_classify_candidates`, reused
identically on every retry against whatever fetch result is current):

1. `fetch_url()` (Playwright/headless Chromium) loads the page and
   captures every XHR/fetch request+response — method, headers, body,
   content-type — via `agent/models/network.py`'s `CapturedRequest`.
2. **JSON API search** (`_find_json_api_candidates`): scans captured
   bodies for a job-shaped payload. `_looks_like_job_list` recurses
   through nesting (bounded depth) and unwraps Relay-style
   `edges: [{node: {...}}]`, so GraphQL's typical
   `data → field → edges → node` shape is found the same way a flat REST
   array is — no separate GraphQL-specific code path needed. A denylist
   of common analytics/consent domains (`onetrust.com`,
   `google-analytics.com`, `segment.io`, etc.) prevents a cookie-consent
   config's JSON from being mistaken for a job listing just because it
   happens to have a `"title"`-keyed list.
3. **Pick the single best candidate across GET *and* POST/GraphQL
   together** by job count — a GET request returning 1-2 metadata
   records (e.g. a page's "About Us" sidebar content, which also has a
   `"title"` key) must not outrank a POST endpoint that actually returned
   the real 20-job listing. Then branch on that winner's method:
   - **GET**: `_try_pagination_params` empirically tests `page=2`,
     `offset=N&limit=N`, `start=N` — confirmed if the result set changes
     (multi-page), empties (single page), **or stays identical and
     non-empty across every variant tried** (Lever-style: unrecognized
     params are silently ignored and the full listing is always
     returned — a genuinely different, equally valid "confirmed"
     outcome from "the response emptied out").
   - **POST/GraphQL**: `_try_json_body_pagination` mutates the *observed*
     request body's pagination-shaped keys (`offset`/`limit`, `page`) or,
     for GraphQL, a Relay `after` cursor — pulled from the real
     `endCursor` in the *observed response* via `_find_key_recursive`,
     never fabricated. Replayed via `probe_endpoint`.
   - India filter (`_try_india_filter` / `_try_json_body_india_filter`):
     tries `country=IN`, `location=India`, `countryCode=IN` etc. against
     the URL or body, keeps whichever actually changes the result set.
4. **No JSON API — SSR HTML links** (`_extract_job_links`): requires
   *both* a keyword marker match *and* a job-title-shaped slug
   (`_looks_like_job_slug`):
   - 3+ hyphen-separated words with 2+ non-generic → accepted (real job
     titles, or GUIDs, which also split into several hyphen segments).
   - A single generic word ("jobs", "careers") immediately before a bare
     numeric ID → accepted by default. Some platforms (Greenhouse:
     `/{company}/jobs/{id}`) encode **no** descriptive title in the URL at
     all — the segment before the ID is a fixed literal, identical on
     every posting, so there's nothing to discriminate against.
   - Anything else short → rejected. This whole layered heuristic exists
     because Swiss Re reuses the *identical* `/go/{slug}/{id}/` URL
     pattern for both real job postings and navigation/category pages
     ("Working at Swiss Re in EMEA", "UK Career Site") — some of which
     even contain a job-related keyword ("Job-List", "Search-Jobs") and
     would slip past a keyword-only check.
   - **Critical safety check**: before trusting these links as
     `SSR_HTML`, verify they *survive a plain HTTP fetch* of the same
     URL (no JS). Playwright executes JavaScript, so its rendered HTML
     can contain client-only content (a career-site embed widget) that a
     `requests`-based scraper — the sandbox's only option at runtime —
     could never replicate. If the links only exist post-JS, they're
     rejected outright rather than confidently misclassified.
5. **Neither found**: falls through to the retry ladder below before
   settling on `SPA_NO_API`.

**Retry ladder when evidence stays insufficient** (this covers both "found
nothing at all" and "found something but couldn't confirm pagination" —
both are `not evidence.is_sufficient()`):

1. **ATS-hop** (up to 2 hops, only from a *non-ATS-hosted* starting URL —
   see the guard below): reuses `discover`'s `_find_ats_link`, but against
   the JS-rendered HTML `investigate` already has, catching ATS links
   that only exist post-JS-execution.
2. **Bounded browser interactions**: click a "search/view jobs"-labeled
   button, fill+Enter a blank search input, scroll — capped at
   `max_interactions=10`, `max_scrolls=5`, `max_page_time=60s`
   (`agent/tools/fetch_url.py`).
3. **Firecrawl Actions** (§11) — capped to once per run.
4. Still nothing → `SPA_NO_API`, with an honest rationale recorded in the
   trace.

**LLM synthesis**: all gathered findings go to the LLM
(`agent/llm/prompts/investigate.md`) purely to interpret `source_type` and
`reported_total_count` — the prompt is explicit that this is a **read-only
interpretation of already-gathered findings**, not a live-tool-using agent
(see the [bug log](#known-bugs-found-and-fixed-a-running-log) for why that
distinction matters).

**Evidence sample**: a concrete real sample — a JSON record, or cleaned
listing+detail HTML built from the *actual matched anchor elements* (not a
blind head-of-page truncation, which can hand the LLM zero real markup on a
large page) — is written to `artifacts/{domain}/evidence_sample.txt`. This
is what `generate_script` reads to ground its selectors in reality.

### `evidence_check` — deterministic gate

`agent/nodes/evidence_check.py`. Calls `InvestigationEvidence.is_sufficient()`
— a plain Python method, never an LLM self-assessment (models are
unreliable judges of their own certainty). Also where the global budget
decrements on an insufficient-evidence loop-back (see the LangGraph gotcha
above).

### `generate_script` — evidence → scraper source

`agent/nodes/generate_script.py`. LLM call, no tools. Branches between
`generate_script.md` (fresh) and `rewrite.md` (if there's a prior failed
`validation_report` — the model sees exactly what went wrong and the
failure category, and is told to write a genuinely different approach, not
regenerate the same broken script).

Both prompts pass a **real example `JobRecord` instance** (all fields
`null`), not `JobRecord.model_json_schema()`. Passing the JSON *Schema*
caused the LLM to literally reproduce the schema's own
`{"properties": {...}, "title": "JobRecord", "type": "object"}` wrapper
around every row instead of a flat instance — a bug that silently poisoned
every repair attempt for an entire run, since the same broken prompt was
reused on every patch/rewrite/re-investigate cycle.

Other hard requirements baked into the prompt (enforced by the validator
too, not just requested):
- Only `requests`/`httpx`, `lxml`, `cssselect`, `jmespath`,
  `python-dateutil`, `pydantic`, `pycountry` — and `pypdf`, but *only* when
  the evidence explicitly says the source is a PDF.
- Never `re`/regex for field extraction.
- Explicit UTF-8 decoding (`resp.encoding = resp.apparent_encoding` before
  reading `resp.text`) — otherwise curly quotes and accented letters come
  out mojibake (`â€™` instead of `'`).
- Dates via `dateutil.parser`; location fields via string ops or
  `pycountry`; missing fields → `null`, never guessed.
- The full pagination loop confirmed during investigation, not just page 1.
- Server-side India filtering when confirmed; client-side only as an
  explicit fallback.

Writes `generated_scripts/{domain}/scraper.py`, archives the revision to
`artifacts/{domain}/{n}_scraper_v{rev}.py`, increments
`state["script_revision"]`.

### `docker_execute` — run it in the sandbox

`agent/nodes/docker_execute.py` → `agent/tools/sandbox.py`. Builds the
image (cached after the first real build) and runs the script with:

- `--network sandbox_net` — internal-only Docker network (no default
  internet route); the only egress path is the `egress-proxy` sidecar.
- `--memory`/`--cpus` limits, `--read-only` root filesystem except a
  mounted `/output` volume, a hard wall-clock timeout
  (`SANDBOX_TIMEOUT_SECONDS`, default 120s).
- `HTTP_PROXY`/`HTTPS_PROXY` env vars pointing at the egress proxy.

All of these — image name, memory, CPU, network name, proxy host — are
`.env`-driven (`config.py`), not hardcoded in `sandbox.py`, so a resource
tweak never requires a code change (the same principle as "no hardcoded
domains," applied to infra params).

Captures exit code, stdout, stderr, and whether it timed out — all stashed
on `state` for `validate` to classify.

### `validate` — deterministic checks against the output

`agent/nodes/validate.py` → `agent/tools/validator.py`. Runs cheapest-first:

1. **Sandbox-level failure first** — timeout or non-zero exit code, before
   even looking at the output file (no point content-checking a script
   that crashed). Non-zero exit is classified `SYNTAX_ERROR` if `stderr`
   contains `SyntaxError`, else `RUNTIME_ERROR`.
2. **`check_no_regex`** — AST-based, not substring-based. A naive
   substring check on `"import re"` **false-positives on
   `"import requests"`** (`"import re"` is a literal substring:
   `"import re"` + `"quests"`), which would reject every scraper using the
   one HTTP library the sandbox actually ships. Walks the AST for actual
   `import re` / `from re import ...` / `re.something(...)` / `__import__('re')`.
3. **`check_no_mojibake`** — scans every text field for UTF-8-decoded-as-
   Latin-1 artifacts (`’`/`“`/`–`/`—`/accented-letter misdecodes/the
   replacement character `�`). Byte-verified via explicit `\uXXXX`
   codepoints, not typed/pasted literal characters, so the check can't be
   silently corrupted by a terminal or file encoding round-trip — exactly
   the class of bug it exists to catch.
4. Valid JSONL, parses against `JobRecord` (else `SCHEMA_DRIFT`).
5. Row count sanity — empty output is `ZERO_RESULTS_PARSING_BUG`;
   materially below `reported_total_count` is `PAGINATION_UNDERCOUNT`.
6. Required-field presence — `title`/`job_id`/`url` non-null in >50% of
   rows, else `SCHEMA_DRIFT`.
7. `country_code` is `IN` or `null` for every row, else
   `ZERO_RESULTS_FILTER_MISMATCH`.
8. Spot-check 2-3 sampled URLs actually resolve (agent-side `httpx`
   request, not from inside the sandbox).

### `failure_diagnosis` — classify *why* it failed

`agent/nodes/failure_diagnosis.py`. Most failures are already categorized
deterministically by `validate.py` — this node only escalates to an LLM
call (`diagnosis.md`) when the category is genuinely still `None`.

### `repair_strategy` — diagnosis → action, and the patch itself

`agent/nodes/repair_strategy.py`. The routing table (`ROUTING_TABLE`) is
pure Python:

| Category | Action |
|---|---|
| `SYNTAX_ERROR`, `RUNTIME_ERROR`, `TIMEOUT`, `ZERO_RESULTS_PARSING_BUG`, `CONTAINS_REGEX`, `MOJIBAKE_ENCODING` | `patch` |
| `PAGINATION_UNDERCOUNT` | `patch` (cheaper; genuinely ambiguous between an off-by-one bug and a wrong approach per spec) |
| `HTTP_FORBIDDEN`, `SCHEMA_DRIFT`, `ZERO_RESULTS_FILTER_MISMATCH` | `re-investigate` |
| `OTHER` | `rewrite` |

For `patch`: this node itself calls the LLM (`patch.md`), including the
evidence sample and the required flat output shape (so a patch can fix a
selector-shape bug, not just a syntax typo), writes the patched script
directly, and routes straight to `docker_execute` — no intermediate codegen
node, per the spec's own routing diagram. `rewrite` deliberately does
*not* call the LLM here; it routes to `generate_script`, which does the
full regen using `rewrite.md`. `re-investigate` calls no LLM at all.

### `finalize` — trace, cost report, deliverables

`agent/nodes/finalize.py`. If `status` is still `"running"` when this is
reached (budget exhausted without a resolution), it's forced to `"failed"`
— never left ambiguous. Writes `generated_scripts/{domain}/cost_report.json`:

```json
{
  "domain": "razorpay.com",
  "status": "success",
  "repair_attempts": 2,
  "total_attempts_used": 2,
  "tokens_prompt": 3397,
  "tokens_completion": 2634,
  "firecrawl_credits_used": 0
}
```

---

## Tools

`agent/tools/`

| Tool | What it does |
|---|---|
| `search_web.py` | Serper.dev wrapper for `discover`. |
| `fetch_url.py` | Playwright wrapper. Captures full XHR/fetch request+response pairs (not just response bodies). Also implements bounded generic interactions — `_try_click_reveal_button`, `_try_search_submit`, `_try_scroll` — capped by a `_Budget` object (`max_interactions`, `max_scrolls`, `max_page_time`). |
| `probe_endpoint.py` | Direct `httpx` requests — the fast path for empirically testing pagination/filter params without browser overhead. `ProbeResult` carries `content_type` (used for the PDF-detection check). |
| `sandbox.py` | Docker build/run wrapper — all resource params `.env`-driven. |
| `validator.py` | The deterministic checks described above. |
| `firecrawl_client.py` | See [§11](#the-firecrawl-escalation-ladder). |

---

## Models

`agent/models/`

- **`job_record.py`** — `JobRecord`, the JSONL output schema (§5 of the
  original spec, verbatim).
- **`evidence.py`** — `InvestigationEvidence` + `SourceType` +
  `is_sufficient()` (the deterministic gate).
- **`validation.py`** — `ValidationReport` + `FailureCategory` (10 values,
  including `MOJIBAKE_ENCODING`, added beyond the original 9).
- **`trace.py`** — `TraceEvent`. Uses Pydantic's `extra="allow"` deliberately:
  different event `type`s (`tool_call`, `decision`, `evidence`,
  `code_generated`, `execution`, `validation`, `repair_decision`, ...) carry
  genuinely different payload shapes, and forcing one universal schema with
  dozens of `Optional` fields would be worse than letting each call site
  attach exactly what's relevant.
- **`network.py`** — `CapturedRequest`, the generic representation of one
  XHR/fetch exchange (method, headers, query params, JSON body, response) —
  what pagination/filter probing mutates and replays, whether the underlying
  request is GET-with-query-params, POST-with-a-JSON-body, or GraphQL.
  `is_graphql` detects via URL (`"graphql"` in the path) or body shape
  (`query` + `variables` keys present).

---

## LLM prompts

`agent/llm/prompts/`

| File | Used by |
|---|---|
| `investigate.md` | Interpreting empirical findings into `source_type`/`reported_total_count`. Deliberately does **not** claim live tool access — it explicitly states the model has "no tools and cannot make further requests," only findings already gathered. An earlier version claimed the model "has access to `fetch_url`/`probe_endpoint`," which caused a Groq-hosted backend to attempt a phantom tool call (surfaced as a `502`) since no `tools` array was actually registered on the API request. |
| `generate_script.md` | Fresh codegen. Includes a real `JobRecord` example instance, not the JSON Schema (see the bug log). |
| `rewrite.md` | Full regen after an `OTHER`/uncategorizable failure — includes the previous script, the failure category/details, and the same real-instance example. |
| `patch.md` | Targeted fix for a diagnosed, specific bug — includes the evidence sample and required output shape too, so a patch can fix a selector-shape issue, not just a one-line typo. Explicitly calls out the mojibake-encoding fix (set `resp.encoding` correctly, don't post-process the mangled string). |
| `diagnosis.md` | Classifying an otherwise-uncategorized failure into a `FailureCategory`. |

`agent/llm/client.py` is the single choke point for all LLM calls — nothing
else in the codebase imports an LLM SDK directly. Retries transient
`429`/`502`/`503`/`504` up to 3 times with exponential backoff (added after a
real `502` was traced to a Groq-hosted backend, not a code bug on this end).
`agent/llm/codeformat.py` provides `extract_code`/`extract_json` — strips
markdown fences models routinely add despite being told not to.

---

## The Docker sandbox

`agent/sandbox/` + `docker-compose.yml`.

**Network policy** (CLAUDE.md §8): public outbound web access, but no
private/internal (RFC1918) access, no cloud metadata endpoint
(`169.254.169.254`), no host services. Enforced with an **egress-proxy
sidecar**, not `NET_ADMIN` + `iptables` inside the untrusted sandbox
container (avoids giving untrusted, LLM-generated code that capability at
all):

- `docker-compose.yml` defines `sandbox_net` as `internal: true` — no
  default route to the internet for anything on that network.
- The `egress-proxy` service (Squid, `agent/sandbox/egress_proxy/`) joins
  both `sandbox_net` and the default bridge, so it's the only bridge
  between them. `squid.conf` is a real deny-list (checked first, deny
  wins): `169.254.169.254/32`, `10.0.0.0/8`, `172.16.0.0/12`,
  `192.168.0.0/16`, loopback, link-local — as **data**, not code, so
  changing the policy never requires touching `sandbox.py`.
- `sandbox.py` attaches each scraper container to `sandbox_net` and sets
  `HTTP_PROXY`/`HTTPS_PROXY` to the proxy.

**Start it** (once per session):

```bash
docker compose up -d egress-proxy
```

**Verify the policy is actually enforced** (not just documented):

```bash
docker run --rm --network sandbox_net -e HTTPS_PROXY=http://egress-proxy:3128 curlimages/curl \
  curl -s -o /dev/null -w "%{http_code}\n" https://169.254.169.254/   # expect 000 (blocked)
docker run --rm --network sandbox_net -e HTTPS_PROXY=http://egress-proxy:3128 curlimages/curl \
  curl -s -o /dev/null -w "%{http_code}\n" https://example.com/       # expect 200 (allowed)
```

The Squid access log confirms this directly by ACL match, not by network
coincidence:

```
TCP_DENIED/403 CONNECT 169.254.169.254:443
TCP_TUNNEL/200 CONNECT example.com:443
```

---

## The trace system

`agent/trace/sink.py`. Two-layer architecture (CLAUDE.md §9):

1. **Persistent trace store**: `traces/{domain}_{run_id}.jsonl` — the
   complete, append-only record. Every event (`node_enter`, `tool_call`,
   `tool_result`, `decision`, `evidence`, `code_generated`, `execution`,
   `validation`, `repair_decision`, `node_exit`) is written immediately.
2. **Full payloads live on disk, referenced by path**: `save_artifact()`
   writes HTML dumps, network captures, each script revision, and stderr
   dumps to `artifacts/{domain}/{n}_{name}`, and the trace event carries
   only the path — never the full content inline. This keeps both the
   trace file and any LLM context that reads it back from bloating.

**Console mirroring**: `TraceSink(console=True)` (set by `run_agent.py`)
prints a short, human-readable line for every event as it happens:

```
>>> investigate
    [investigate] -> fetch_url({'url': 'https://job-boards.greenhouse.io/...'})
    [investigate] <- fetch_url: status=200, network_request_count=4, artifact_ref=...
    [investigate] DECISION classify_ssr_html: No JSON API found, but 23 job-shaped anchor links...
    [investigate] EVIDENCE source_type=ssr_html pagination_confirmed=True sufficient=True confidence=0.6
```

`node_exit` events are intentionally silent in the console (the matching
`node_enter` already marked the transition — printing both would just be
noise). Off by default (`console=False`) so the test suite isn't spammed;
`run_agent.py` turns it on explicitly.

---

## The Firecrawl escalation ladder

`agent/tools/firecrawl_client.py`. **Never the default fetch path** —
Playwright (`fetch_url`) and `httpx` (`probe_endpoint`) remain primary.
Firecrawl is reached only when they've demonstrably failed, at exactly
three points:

1. **`investigate` re-run after `HTTP_FORBIDDEN`**: retry the fetch through
   Firecrawl (its own proxy/stealth handling) before giving up — this is
   CLAUDE.md §12's adversarial bot-protected domain case.
2. **Zero job DOM nodes after bounded Playwright interactions**: one
   Firecrawl Actions attempt with a richer click+scroll+wait sequence.
3. **The careers URL resolves to a PDF**: route to Firecrawl's native PDF
   parsing instead of failing outright (the resulting extracted text
   becomes the evidence sample; the *generated scraper* still fetches and
   parses the PDF itself at runtime with `pypdf` — Firecrawl is
   investigation-time only, never baked into generated code).

**Credit tracking is a measured delta, not an estimate.** Firecrawl's
`/v1/scrape` response doesn't report per-call cost, but
`/v1/team/credit-usage` returns the account's real `remaining_credits`
balance — `FirecrawlClient.scrape()` snapshots it before and after every
call and takes the actual difference. Verified directly: one basic scrape
call measured as exactly 1 credit via this delta method.

**Capped to once per run**, not once per retry
(`state["firecrawl_actions_attempted"]`). The evidence-insufficient loop
re-invokes `investigate()` from scratch on every attempt, and the page
structure doesn't change between retries — without the cap, a domain that
never produces evidence would burn one real, paid credit per retry (was 7
credits on a real test domain; capped run used 1).

`cost_report.json` tracks `firecrawl_credits_used` explicitly, alongside
`tokens_prompt`/`tokens_completion` — per the framing that Firecrawl
credits, not LLM tokens, are the scarcest, least-linear-cost resource in
this system.

---

## Validation checks

See [`validate` above](#validate--deterministic-checks-against-the-output)
for the full ordered list. All 10 `FailureCategory` values:

`SYNTAX_ERROR`, `RUNTIME_ERROR`, `HTTP_FORBIDDEN`, `TIMEOUT`,
`SCHEMA_DRIFT`, `PAGINATION_UNDERCOUNT`, `ZERO_RESULTS_FILTER_MISMATCH`,
`ZERO_RESULTS_PARSING_BUG`, `CONTAINS_REGEX`, `MOJIBAKE_ENCODING`.

---

## The repair loop

See [`repair_strategy`](#repair_strategy--diagnosis--action-and-the-patch-itself)
above for the routing table. The important structural point: `patch` and
`rewrite` are **not the same code path**. `patch` edits the existing script
via `repair_strategy` itself and reruns it directly; `rewrite` throws the
old script away and asks `generate_script` for a full new attempt, informed
by *why* the old one failed. Confirmed working live: a real run needed 7
repair cycles (mixing both patch and rewrite) before succeeding on the 8th
— proving the loop doesn't just retry blindly, it actually converges.

---

## Known bugs found and fixed (a running log)

Kept here deliberately, not scrubbed — several of these are exactly the
kind of thing that would silently reappear if the underlying design lesson
were lost:

1. **Conditional-edge mutation doesn't persist in LangGraph.** Caused an
   infinite loop; fixed by moving the budget decrement into the
   `evidence_check` node.
2. **`"import re" in "import requests"` is `True`.** A naive substring
   check on the no-regex requirement would reject every scraper using the
   sandbox's own recommended HTTP library. Fixed with an AST walk.
3. **JSON Schema passed as "the shape to emit."** The LLM reproduced the
   schema's own `{"properties": ..., "title": "JobRecord", "type": "object"}`
   wrapper instead of a flat instance — poisoned every repair attempt in a
   run since the same broken prompt was reused every cycle. Fixed by
   passing a real example instance.
4. **A prompt claiming live tool access it didn't have.** Caused a
   Groq-hosted backend to attempt a phantom tool call, surfaced as a `502`.
   Fixed by rewriting the prompt to describe its actual (read-only,
   post-hoc interpretation) role.
5. **Windows console encoding.** `subprocess.run` defaults to `cp1252` on
   Windows; scraper output containing non-ASCII job data crashed the
   pipeline. Fixed with explicit `encoding="utf-8", errors="replace"`.
6. **False-positive job-API detection.** A OneTrust cookie-consent JSON
   payload tripped the job-list heuristic (had a `"title"`-keyed list).
   Fixed with a denylist of common analytics/consent domains.
7. **JS-rendered content misclassified as SSR.** Playwright's rendered
   HTML can contain client-only content a `requests`-based scraper can
   never see at runtime. Fixed by verifying job links survive a plain HTTP
   fetch before trusting them.
8. **Lever-style "unchanged, non-empty" pagination response.** Was only
   recognized as a failed probe, not a valid single-page confirmation.
   Fixed by adding that as a legitimate outcome — but this initially
   introduced bug #10 below as a side effect.
9. **Swiss Re reuses the same URL pattern for job postings and nav pages.**
   A keyword-only heuristic couldn't tell "Job-List" (navigation) from
   "Senior-Actuary-Analyst" (a real job) apart. Fixed with a layered
   slug-shape heuristic — word count, generic-word filtering, and a
   special case for platforms (Greenhouse) that encode no title in the URL
   at all.
10. **The slug-shape fix from #9 initially broke Greenhouse.** Greenhouse's
    bare `/jobs/{id}` URLs have no descriptive slug — the segment before
    the ID is just the literal word "jobs," which the new filter rejected
    as too short. Fixed by accepting a lone generic word immediately
    before a bare numeric ID as a distinct case from a *varying* slug that
    needs judging.
11. **ATS-hop matched Greenhouse's own corporate pages.** Once `investigate`
    was already on a legitimate `job-boards.greenhouse.io` page, its own
    footer links (privacy policy, sign-in, regional marketing) also live
    on `greenhouse.io` and matched the same ATS marker, derailing a
    working job board into the vendor's own login/marketing maze for the
    rest of the run. Fixed by only attempting the ATS-hop when the
    *starting* URL isn't already on an ATS-hosted domain.
12. **Firecrawl Actions re-burned a credit on every retry.** The
    evidence-insufficient loop re-invokes `investigate()` from scratch,
    and without a run-level cap, a domain that never produces evidence
    burned one real credit per retry (7 credits observed on one real
    test). Fixed with `state["firecrawl_actions_attempted"]`.
13. **`_find_ats_link` matched a `mailto:` share link.** The target ATS
    domain appeared as plain text inside the link's URL-encoded email
    body, not because it actually linked there — crashed Playwright
    trying to navigate to a `mailto:` URL. Fixed by matching against the
    *resolved URL's actual host*, never a raw substring of the href.

---

## Known limitations

- **JS-only widgets with zero underlying API.** If a career site's job
  data is rendered entirely client-side by a widget that never makes a
  discoverable network call (confirmed directly on one real domain — even
  Firecrawl's rendering found nothing beyond what Playwright already saw),
  there is no `requests`-only way to replicate it at scraper runtime. This
  is an honest architectural boundary, not a bug: the sandbox deliberately
  has no browser engine (§8), so a domain like this correctly produces an
  honest failure, not a fabricated result.
- **Bot-protected domains** (Cloudflare challenge pages, etc.) rely on the
  `HTTP_FORBIDDEN` → Firecrawl retry path; sites with sufficiently
  aggressive protection may still honestly fail even after that escalation.
- **Slug-shape heuristics are fuzzy, not perfect.** They're verified
  against every real domain this system has been tested against, but a
  sufficiently unusual URL scheme could still produce a false
  positive/negative. This is deliberate: a lightweight, generalizable
  heuristic beats a hardcoded per-domain rule (CLAUDE.md §2 #2), even at
  the cost of not being flawless.

---

## Repository structure

```
scraping_script_agent/
├── CLAUDE.md                    # the original build spec
├── README.md                    # this file
├── .env.example
├── config.py                    # the only module that reads os.environ
├── docker-compose.yml           # egress-proxy sidecar + sandbox_net
├── agent/
│   ├── graph.py                 # the LangGraph StateGraph
│   ├── state.py                 # AgentState
│   ├── nodes/                   # the 9 graph nodes (§5 above)
│   ├── tools/                   # search_web, fetch_url, probe_endpoint,
│   │                            #   sandbox, validator, firecrawl_client
│   ├── models/                  # JobRecord, InvestigationEvidence,
│   │                            #   ValidationReport, TraceEvent, CapturedRequest
│   ├── llm/
│   │   ├── client.py            # the single LLM choke point
│   │   ├── codeformat.py        # extract_code / extract_json
│   │   └── prompts/             # investigate.md, generate_script.md,
│   │                            #   rewrite.md, patch.md, diagnosis.md
│   ├── trace/
│   │   └── sink.py              # TraceSink — persistent JSONL + console mirror
│   └── sandbox/
│       ├── Dockerfile           # the scraper execution image
│       ├── entrypoint.sh
│       └── egress_proxy/        # Squid config + Dockerfile
├── generated_scripts/{domain}/  # output: scraper.py, output.jsonl, cost_report.json
├── traces/{domain}_{run_id}.jsonl
├── artifacts/{domain}/          # HTML dumps, network captures, script revisions, stderr
├── tests/
│   ├── test_models/
│   ├── test_nodes/
│   ├── test_tools/
│   └── test_graph_routing.py
└── scripts/
    └── run_agent.py             # CLI entry point
```

---

## Testing

```bash
pytest tests/ -q
```

73 tests, covering: every Pydantic model, the graph's routing logic and
attempt-budget accounting in isolation (no real LLM/network calls), the
job-slug-shape heuristic against every real domain's actual URL patterns
(including every bug case in the [log](#known-bugs-found-and-fixed-a-running-log)
above, so none of them can silently regress), pagination/filter mutation
logic for REST/POST/GraphQL via monkeypatched `probe_endpoint`, the
AST-based no-regex check (including the exact `"import requests"`
false-positive case), the mojibake check (verified against byte-exact
`\uXXXX` codepoints, not typed characters), Firecrawl credit-delta tracking
against a scripted fake HTTP client, the Firecrawl-once-per-run cap and the
ATS-hop-skip-when-already-on-ATS-host guard via full `investigate()`
integration tests with every dependency mocked, and a fully deterministic
browser-interaction test (a local HTML fixture + Playwright route
interception — a "Search Jobs" click firing a real `fetch()` that gets
captured — with no live website involved, so it can't flake).

Verified live against real production domains across 4 distinct ATS
platforms with zero orchestrator code changes between them: **Greenhouse**
(razorpay.com), **SmartRecruiters** (freshworks.com), **Workday**
(browserstack.com, via its POST-based CXS API), and **Lever** (paytm.com).
Also verified an honest-failure case against a Cloudflare-protected domain
(no hang, no fabricated data) and a genuinely unscrapable-within-constraints
case (a JS-only widget with no discoverable API).

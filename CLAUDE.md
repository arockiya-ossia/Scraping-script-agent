# CLAUDE.md — AI Agent That Writes Job-Scraper Scripts

This file is the authoritative build spec for this repository. Claude Code should
read this file in full before writing any code, and should treat every
"Non-Negotiable Constraint" below as a hard requirement to check against before
considering any part of this project done.

---

## 1. Mission

Build an **AI agent whose job is to write code** — not to scrape jobs itself.

Given only a company **domain** (e.g. `swissre.com` — no careers URL, no other
input), the agent must, with **zero human intervention**:

1. Discover the real careers page/API for that domain.
2. Investigate the data source (SSR HTML, embedded JSON, REST/GraphQL API, or
   JS-only SPA) and determine the pagination scheme.
3. Generate a **standalone scraper script** that extracts that company's
   **India-based** job listings and writes them to JSONL.
4. **Run and validate** the generated script, and **self-repair** it if the
   output is wrong.
5. **Generalize** — a brand-new domain must go through the same process from
   scratch, with no new orchestrator code and no human involvement.

The agent is not the scraper. The agent produces the scraper. Once generated,
the scraper must run with **no LLM calls at runtime**.

---

## 2. Non-Negotiable Constraints

These map directly to the grading criteria. Do not violate any of these to make
something "work" faster.

| # | Constraint | Enforcement in this codebase |
|---|---|---|
| 1 | Every meaningful decision (endpoint, selector, pagination strategy) is made by the LLM at runtime, not hardcoded. | All such decisions live in `agent/nodes/investigate.py` and `agent/nodes/generate_script.py`, driven by LLM output, and are logged to the trace. |
| 2 | No per-domain hardcoded logic in **human-written** code (`if domain == "x"`, hand-built selector lookup tables, etc.). The agent may write domain-specific code — that's its job. | Orchestrator code (`agent/`) must never branch on a literal domain or company name. Generic ATS-fingerprint detection (Greenhouse/Workday/etc. response shapes) is allowed since it's a horizontal pattern used across thousands of companies — but it is only ever a *hint*; every fact must still be confirmed empirically via `probe_endpoint`/`fetch_url` before being trusted. |
| 3 | The agent must actually run the generated script and verify it, self-healing on failure — never hand over untested code. | `agent/nodes/docker_execute.py` + `agent/nodes/validate.py` + the bounded repair loop (§7). |
| 4 | **No regex** for field extraction in agent-generated code. CSS selectors / JSON paths / structured parsing only. | The code-generation prompt forbids `re`. The validator additionally runs a static check on the generated source (`agent/tools/validator.py::check_no_regex`) that fails validation if `import re` or regex usage is detected, forcing a repair cycle. |
| 5 | Missing fields → `null`. Never hallucinated or inferred from prose. | Enforced in the code-gen prompt and spot-checked by the validator (a field that is suspiciously 100% non-null across a source that shouldn't expose it is flagged). |
| 6 | Every run produces a full reasoning/action trace, not just the final script. | Two-layer trace system, §9. |

---

## 3. Tech Stack

| Layer | Choice | Why |
|---|---|---|
| Orchestration | **LangGraph** | Explicit state machine with typed state, conditional edges, and checkpointing — needed for the bounded, branching repair loop in §7. |
| State & schema validation | **Pydantic** | One schema definition reused for agent state, tool I/O, the job-record output schema, and validation reports. |
| Browser investigation | **Playwright** (Python) | Renders JS-heavy pages and captures the network log (XHR/fetch calls) — this is how hidden JSON/GraphQL APIs get discovered without ever hardcoding a platform name. |
| Direct HTTP experiments | **httpx** | Fast path for `probe_endpoint` — testing pagination params and filter params directly against a discovered API, no browser overhead. |
| Sandboxed execution | **Docker** | Runs agent-generated code in isolation. See §8 for the network policy — this is the one place where "convenient" and "safe" are not the same thing, so read it carefully. |
| LLM | **freellmapi** (https://github.com/tashfeenahmed/freellmapi) | All reasoning + code generation calls. Wrapped behind `agent/llm/client.py` so the rest of the codebase never talks to it directly (makes token accounting and provider-swapping trivial). |
| Search | **Serper** | Careers-page discovery. Reserve **Firecrawl** (not wired in by default) for the adversarial bot-protected test domain in the stretch goals — plain Playwright will likely get blocked there, and building bot-evasion into Playwright yourself is a time sink. |

---

## 4. Repository Structure

```
job-scraper-agent/
├── CLAUDE.md
├── README.md
├── .env.example
├── .gitignore
├── pyproject.toml
├── config.py                        # loads .env via python-dotenv, exposes a Settings object
├── agent/
│   ├── __init__.py
│   ├── graph.py                      # LangGraph StateGraph: nodes, edges, conditional routing
│   ├── state.py                      # AgentState (TypedDict) + shared Pydantic sub-models
│   ├── nodes/
│   │   ├── discover.py               # domain -> careers URL
│   │   ├── investigate.py            # careers URL -> source_facts (source type, pagination, filter)
│   │   ├── evidence_check.py         # structural gate: is source_facts sufficient to codegen?
│   │   ├── generate_script.py        # source_facts -> scraper script (LLM call)
│   │   ├── docker_execute.py         # run the script in the sandbox, capture stdout/stderr/output file
│   │   ├── validate.py               # deterministic checks against the JSONL output
│   │   ├── failure_diagnosis.py      # categorize *why* validation failed
│   │   ├── repair_strategy.py        # map diagnosis -> {patch, rewrite, re-investigate}
│   │   └── finalize.py               # write trace, cost report, and final deliverables
│   ├── tools/
│   │   ├── search_web.py             # Serper wrapper
│   │   ├── fetch_url.py              # Playwright wrapper + network capture
│   │   ├── probe_endpoint.py         # httpx wrapper for empirical API testing
│   │   ├── sandbox.py                # Docker execution wrapper (build/run/timeout/cleanup)
│   │   └── validator.py              # schema validation + sanity checks + static no-regex check
│   ├── llm/
│   │   ├── client.py                 # freellmapi client wrapper; tracks token usage per call
│   │   └── prompts/
│   │       ├── investigate.md
│   │       ├── generate_script.md
│   │       ├── patch.md
│   │       ├── rewrite.md
│   │       └── diagnosis.md
│   ├── models/
│   │   ├── job_record.py             # JobRecord — the JSONL schema from §5 of the problem statement
│   │   ├── evidence.py               # InvestigationEvidence
│   │   ├── validation.py             # ValidationReport, FailureCategory enum
│   │   └── trace.py                  # TraceEvent schema
│   ├── trace/
│   │   └── sink.py                   # append-only JSONL trace writer + artifact storage
│   ├── sandbox/
│   │   ├── Dockerfile                # scraper execution image (no LLM libs inside)
│   │   ├── entrypoint.sh
│   │   └── egress_proxy/             # sidecar proxy enforcing the network allow/deny policy (§8)
│   └── memory/                       # stretch goal: cross-run learned pattern store
│       └── store.py
├── generated_scripts/                # output: {domain}/scraper.py, {domain}/output.jsonl
├── traces/                           # persistent trace store: {domain}_{timestamp}.jsonl
├── artifacts/                        # HTML dumps, network captures, script versions, stderr dumps
├── tests/
│   ├── test_tools/
│   ├── test_models/
│   ├── test_graph_routing.py
│   └── fixtures/
└── scripts/
    └── run_agent.py                  # CLI: python scripts/run_agent.py --domain swissre.com
```

---

## 5. Environment & Configuration

All secrets and tunables come from `.env` via `python-dotenv`. **Never hardcode
a key in source.** Copy `.env.example` to `.env` and fill in real values —
`.env` is git-ignored.

`config.py` is the single place that reads the environment:

```python
# config.py
from dotenv import load_dotenv
import os
from pathlib import Path
from pydantic import BaseModel

load_dotenv()

class Settings(BaseModel):
    # LLM
    freellmapi_base_url: str = os.environ["FREELLMAPI_BASE_URL"]
    freellmapi_api_key: str = os.environ["FREELLMAPI_API_KEY"]
    freellmapi_model: str = os.getenv("FREELLMAPI_MODEL", "default")

    # Search
    serper_api_key: str = os.environ["SERPER_API_KEY"]
    serpapi_api_key: str | None = os.getenv("SERPAPI_API_KEY")     # optional backup
    firecrawl_api_key: str | None = os.getenv("FIRECRAWL_API_KEY")  # reserved for adversarial domain

    # Agent behavior
    max_repair_attempts: int = int(os.getenv("MAX_REPAIR_ATTEMPTS", "5"))
    max_total_attempts: int = int(os.getenv("MAX_TOTAL_ATTEMPTS", "8"))
    sandbox_timeout_seconds: int = int(os.getenv("SANDBOX_TIMEOUT_SECONDS", "120"))

    # Paths
    output_dir: Path = Path(os.getenv("OUTPUT_DIR", "generated_scripts"))
    trace_dir: Path = Path(os.getenv("TRACE_DIR", "traces"))
    artifacts_dir: Path = Path(os.getenv("ARTIFACTS_DIR", "artifacts"))

settings = Settings()
```

Every module imports `from config import settings` — nothing reaches for
`os.environ` directly outside this file.

---

## 6. Data Models

### 6.1 `JobRecord` (the JSONL output schema — matches problem statement §5 exactly)

```python
from pydantic import BaseModel
from typing import Optional

class JobRecord(BaseModel):
    title: Optional[str] = None
    job_id: Optional[str] = None            # stable requisition ID

    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    country_code: Optional[str] = None

    url: Optional[str] = None
    apply_url: Optional[str] = None

    date_posted: Optional[str] = None       # typed/ISO parsed, via dateutil — never regex
    date_posted_text: Optional[str] = None  # raw fallback text

    job_description: Optional[str] = None   # full text, single field/selector

    employment_type: Optional[str] = None   # structural only — null if not exposed structurally
    work_type: Optional[str] = None
    salary_range: Optional[str] = None
```

### 6.2 `InvestigationEvidence` (the structural gate — see §7, Evidence Check)

```python
from enum import Enum
from typing import Optional
from pydantic import BaseModel

class SourceType(str, Enum):
    SSR_HTML = "ssr_html"
    EMBEDDED_JSON = "embedded_json"
    REST_API = "rest_api"
    GRAPHQL = "graphql"
    SPA_NO_API = "spa_no_api"
    UNKNOWN = "unknown"

class InvestigationEvidence(BaseModel):
    careers_url: Optional[str] = None
    source_type: Optional[SourceType] = None
    pagination_param_confirmed: bool = False     # actually tested via probe_endpoint, not guessed
    pagination_mechanism: Optional[str] = None    # e.g. "offset/limit", "cursor", "page number", "infinite scroll"
    india_filter_mechanism: Optional[str] = None  # e.g. "query param country=IN", or "client_side_fallback"
    reported_total_count: Optional[int] = None    # if the source itself reports a total, for pagination sanity checks
    evidence_notes: str = ""                      # LLM's reasoning, goes straight into the trace

    def is_sufficient(self) -> bool:
        return (
            self.careers_url is not None
            and self.source_type is not None
            and self.pagination_param_confirmed
            and self.india_filter_mechanism is not None
        )
```

`is_sufficient()` is a **deterministic Python method**, not an LLM
self-assessment — models are unreliable at judging their own certainty. The
graph calls this method to decide whether to proceed to code generation or
loop back into more investigation.

### 6.3 `ValidationReport` / `FailureCategory`

```python
from enum import Enum
from typing import Optional
from pydantic import BaseModel

class FailureCategory(str, Enum):
    SYNTAX_ERROR = "syntax_error"
    RUNTIME_ERROR = "runtime_error"
    HTTP_FORBIDDEN = "http_forbidden"
    TIMEOUT = "timeout"
    SCHEMA_DRIFT = "schema_drift"                       # API response shape no longer matches what codegen assumed
    PAGINATION_UNDERCOUNT = "pagination_undercount"      # fewer rows than the source's own reported total
    ZERO_RESULTS_FILTER_MISMATCH = "zero_results_filter_mismatch"  # wrong param sent to the source
    ZERO_RESULTS_PARSING_BUG = "zero_results_parsing_bug"          # data came back fine, our parsing logic is wrong
    CONTAINS_REGEX = "contains_regex"                    # static check caught `import re` / regex usage
    OTHER = "other"

class ValidationReport(BaseModel):
    passed: bool
    row_count: int = 0
    non_null_field_rates: dict[str, float] = {}
    all_country_code_is_IN: Optional[bool] = None
    spot_check_urls_ok: Optional[bool] = None
    failure_category: Optional[FailureCategory] = None
    details: str = ""
```

### 6.4 `TraceEvent`

```python
from typing import Any, Optional
from pydantic import BaseModel
import time

class TraceEvent(BaseModel):
    timestamp: float = time.time()
    domain: str
    node: str
    event_type: str            # "node_enter", "tool_call", "llm_call", "node_exit", "repair_decision"
    summary: str                # short human-readable line, kept in LangGraph state
    artifact_ref: Optional[str] = None  # path into artifacts/ for full payloads (HTML, network capture, stderr, etc.)
    tokens_prompt: Optional[int] = None
    tokens_completion: Optional[int] = None
```

### 6.5 `AgentState` (LangGraph state)

```python
from typing import TypedDict, Optional, Literal
from agent.models.evidence import InvestigationEvidence
from agent.models.validation import ValidationReport

class AgentState(TypedDict):
    domain: str
    evidence: InvestigationEvidence
    script_code: Optional[str]
    script_path: Optional[str]
    validation_report: Optional[ValidationReport]
    repair_attempt: int
    total_attempts: int              # GLOBAL counter — decremented on ANY loop-back, see §7
    max_total_attempts: int
    status: Literal["running", "success", "failed"]
    trace_summary: list[dict]        # lightweight — summaries + artifact refs only, NOT full payloads
```

**Important:** `AgentState.trace_summary` stays small on purpose (see §9 —
this is the "small summaries + artifact references" layer). Full tool I/O,
HTML dumps, network captures, and generated script versions live on disk in
`artifacts/` and are referenced by path/hash, never inlined into LangGraph
state or the LLM's context window.

---

## 7. LangGraph Graph Definition

```
                         INPUT DOMAIN
                              |
                              v
                    +-------------------+
                    |  Domain Discovery |
                    +-------------------+
                              |
                              v
                    +-------------------+
                    |   Investigation   |  (search / httpx / Playwright)
                    +-------------------+
                              |
                              v
                    +-------------------+
                    |  Evidence Check   |  (deterministic: evidence.is_sufficient())
                    +-------------------+
                         /          \
                  insufficient      sufficient
                      |                 |
                      v                 v
             More Investigation   Code Generation
             (loops back up,           |
              global budget            v
              decrements)     +------------------+
                               | Docker Execution |
                               +------------------+
                                        |
                                        v
                            +------------------------+
                            | Deterministic Validator|
                            +------------------------+
                                  /             \
                               PASS             FAIL
                                |                |
                                v                v
                         Finish Success    Budget Check (total_attempts vs max_total_attempts)
                                              /    \
                                           stop    retry
                                            |        |
                                            v        v
                                      Honest Fail  Failure Diagnosis
                                                       |
                                                       v
                                              Repair Strategy
                                             /      |       \
                                          Patch   Rewrite  Re-investigate
                                            |       |          |
                                            v       v          v
                                          Docker   Code      Investigation
                                          Execute  Generation
```

### 7.1 Node responsibilities

| Node | Responsibility | Tools |
|---|---|---|
| `discover` | Domain → candidate careers URL (search + common-path probing) | `search_web`, `fetch_url` |
| `investigate` | Careers URL → `InvestigationEvidence` (source type, pagination, filter mechanism) | `fetch_url` (with network capture), `probe_endpoint` |
| `evidence_check` | Deterministic gate — `evidence.is_sufficient()` | none (pure Python) |
| `generate_script` | `InvestigationEvidence` → full scraper source (LLM call, no tools) | `agent/llm/client.py` |
| `docker_execute` | Run the script in the sandbox, capture stdout/stderr/exit code/output file | `sandbox.py` |
| `validate` | Deterministic schema + sanity checks against the JSONL output | `validator.py` |
| `failure_diagnosis` | Classify *why* validation failed into a `FailureCategory` | LLM call, given stderr/validation report as context |
| `repair_strategy` | Map `FailureCategory` → `{patch, rewrite, re-investigate}` | pure Python routing table (§7.3), LLM call for the patch/rewrite itself |
| `finalize` | Write the persistent trace, cost report, and deliverables; handles both success and honest-failure output | `trace/sink.py` |

### 7.2 Global attempt budget

There are two loop-back points (Evidence Check → More Investigation, and
Validator FAIL → Repair). Both decrement the **same** `state["total_attempts"]`
counter, capped at `max_total_attempts` (default 8, from `.env`). This
prevents a stubborn domain from silently accumulating, say, 5 investigation
loops × 5 repair loops = 25 LLM calls before failing. When the budget is
exhausted, route straight to `finalize` with `status = "failed"` — an honest
failure report, never a fabricated or partial-but-unlabeled result.

### 7.3 Failure → Repair Strategy routing table

Validation only reports **facts** (row count, null rates, HTTP status seen,
etc.). `failure_diagnosis` turns those facts into a category; `repair_strategy`
turns the category into an action. Do not skip the diagnosis step and jump
straight from FAIL to a generic "try again" — different failures need
different fixes:

| Diagnosis | Repair Strategy | Routes to |
|---|---|---|
| `SYNTAX_ERROR` | **Patch** — targeted edit to the existing script, not a full regen | `docker_execute` |
| `PAGINATION_UNDERCOUNT` (row count < source's own reported total) | **Patch** if the pagination *logic* has an off-by-one/loop-termination bug; **Rewrite** if the whole pagination approach was wrong | `docker_execute` or `generate_script` |
| `HTTP_FORBIDDEN` | **Re-investigate** — the request itself needs different headers/auth/approach | `investigate` |
| `SCHEMA_DRIFT` (API response shape changed since investigation) | **Re-investigate** — facts learned earlier are stale | `investigate` |
| `ZERO_RESULTS_FILTER_MISMATCH` (wrong param/value sent to the source) | **Re-investigate** — the filter mechanism itself was wrong | `investigate` |
| `ZERO_RESULTS_PARSING_BUG` (data came back fine, our location-matching logic is broken) | **Patch** — this is a bug in generated parsing code, not a bad fact about the source | `docker_execute` |
| `CONTAINS_REGEX` (static check caught regex usage) | **Patch** — rewrite the offending extraction to use CSS selectors/JSON paths | `docker_execute` |
| `OTHER` / unclassifiable | **Rewrite** | `generate_script` |

This table lives as actual routing logic in `agent/nodes/repair_strategy.py`,
not just documentation — keep it a real `dict[FailureCategory, RepairAction]`
that the conditional edge function reads.

---

## 8. Docker Sandbox — Execution & Network Policy

The generated scraper needs broad outbound HTTPS (career sites commonly
redirect through a separate ATS domain, CDN, or auth provider — a
same-origin-only allowlist will cause false failures). But "broad outbound"
must not mean the untrusted, LLM-generated script can reach internal
infrastructure.

**Policy:**

```
Public outbound web access          ✓
Private/internal (RFC1918) access   ✗
Cloud metadata (169.254.169.254)    ✗
Host services / Docker bridge host  ✗
```

**Implementation — do not rely on "Docker isolation" alone; it does not
enforce this by default.** Use an egress-proxy sidecar rather than granting
the sandbox container `NET_ADMIN` + writing `iptables` rules inside an
untrusted container (avoid giving untrusted code that capability at all):

- The sandbox container has **no direct internet route** — its only egress
  path is through a small proxy sidecar (`agent/sandbox/egress_proxy/`).
- The proxy resolves and allows public IPs, and explicitly denies:
  `169.254.169.254/32`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, and
  loopback ranges.
- The sandbox container itself: no privileged mode, read-only root
  filesystem except a mounted `/output` volume for the JSONL result, a hard
  wall-clock timeout (`SANDBOX_TIMEOUT_SECONDS`, default 120s), and a memory/CPU
  limit set on `docker run`.
- The image (`agent/sandbox/Dockerfile`) only installs what a generated
  scraper is allowed to use: `requests`/`httpx`, `lxml`, `cssselect`,
  `jmespath`, `python-dateutil`, `pydantic`. No LLM client libraries inside
  the sandbox — this guarantees the "no LLM calls at runtime" requirement
  structurally, not just by convention.

---

## 9. Trace Logging — Two-Layer Architecture

Full tool I/O (HTML dumps, network captures, stderr, every script version,
full validation reports) will rapidly bloat both LangGraph state and any LLM
context that reads it back. Split into two layers:

1. **LangGraph state (`trace_summary`)** — small: one-line summaries plus a
   reference (path or hash) to the full artifact. This is what nodes actually
   pass to each other and what the LLM sees if it needs prior context.
2. **Persistent trace store (`traces/{domain}_{timestamp}.jsonl`)** — the
   complete, append-only record: every `TraceEvent`, full tool inputs/outputs,
   and pointers into `artifacts/` for large payloads (raw HTML, network
   capture JSON, each generated script version, stderr dumps). This is the
   deliverable required by Constraint #6, and also doubles as the basis for
   the token/cost report (sum `tokens_prompt`/`tokens_completion` across all
   `llm_call` events for a domain).

Wrap node functions rather than hand-logging inside each one:

```python
def traced(node_fn):
    def wrapper(state: AgentState) -> AgentState:
        trace_sink.emit(state["domain"], node=node_fn.__name__, event_type="node_enter")
        new_state = node_fn(state)
        trace_sink.emit(state["domain"], node=node_fn.__name__, event_type="node_exit")
        return new_state
    return wrapper
```

`trace_sink.emit(...)` writes the full event to the persistent JSONL file
immediately and appends only a short summary to `state["trace_summary"]`.

---

## 10. Code Generation Constraints (enforced in the prompt AND checked by the validator)

The `generate_script` prompt must explicitly instruct the LLM to:

- Use **CSS selectors** (`lxml`/`cssselect`) or **JSON paths** (`jmespath`) for
  every field — never `re`.
- Prefer **server-side India filtering** (a query param confirmed during
  investigation) over pulling all jobs and filtering client-side; fall back to
  client-side filtering only if `evidence.india_filter_mechanism ==
  "client_side_fallback"`.
- Parse dates with `dateutil.parser` into `date_posted` (typed/ISO), keeping
  the raw string in `date_posted_text` as a fallback — never regex-parsed.
- Derive `city`/`state`/`country`/`country_code` via string operations (`.split(",")`,
  containment checks against a static India states/cities table, or
  `pycountry`) — never regex, never inferred from the free-text job
  description.
- Emit `null` for any field not structurally present — never guess.
- Implement the full pagination loop confirmed during investigation (not just
  page 1).
- Write output as JSONL, one `JobRecord`-shaped object per line.

The validator's static check (`validator.py::check_no_regex`) scans the
generated source text for `import re` and common regex call patterns as a
second line of defense — if found, that's an automatic `CONTAINS_REGEX`
failure that routes to a patch cycle, regardless of what the prompt said.

---

## 11. Deterministic Validation Checks

`agent/nodes/validate.py` must run (in order, cheapest first):

1. **Static check** — no `import re` / regex usage in the generated source.
2. **File exists, valid JSONL** — every line parses as JSON and validates
   against `JobRecord`.
3. **Row count sanity** — `row_count > 0`; if `evidence.reported_total_count`
   is set, flag `PAGINATION_UNDERCOUNT` if the parsed count is materially
   lower.
4. **Required-field presence** — `title`, `job_id`, and `url` should be
   non-null for the large majority of rows; a near-100% null rate on any of
   these is suspicious and should fail.
5. **Location sanity** — `country_code` should be `"IN"` (or null) for every
   row; any other non-null value indicates a filtering bug.
6. **Spot-check URLs** — `httpx.head()`/`get()` on 2–3 sampled `url`/
   `apply_url` values, confirm a 2xx/3xx response (agent-side check, not
   inside the sandbox).

Every check populates `ValidationReport`; a failure on any of 3–6 should be
handed to `failure_diagnosis` with enough detail (which check failed, sample
rows, stderr if any) to classify correctly per the table in §7.3.

---

## 12. Stretch Goals — Implementation Notes

- **Cross-run memory** (`agent/memory/store.py`): key by a *site signature*
  (detected ATS fingerprint + response shape hash), not by domain. Surface
  stored hints to `investigate` as a prior guess to check first — never as a
  substitute for empirical confirmation via `probe_endpoint`. This keeps it
  from becoming a hidden hardcoded branch (Constraint #2).
- **Per-field confidence scoring**: keep this as a *sidecar* file
  (`{domain}/confidence.json`, keyed by `job_id` → `dict[str, float]`) rather
  than extending the fixed `JobRecord` schema from §5. Compute heuristically —
  e.g. a selector that matched consistently across all sampled jobs scores
  high; a fallback/best-effort path scores lower.
- **Adversarial bot-protected domain**: pick one deliberately difficult
  domain for testing. Expect `discover`/`investigate` to hit a 403/challenge
  page; this is exactly what `failure_diagnosis` should classify as
  `HTTP_FORBIDDEN`. If retries within the attempt budget still fail, `finalize`
  must produce an **honest failure report** — never a hang, never fabricated
  rows. This is explicitly graded on honesty over false success.
- **Cost report**: nearly free once tracing is in place — `finalize` sums
  `tokens_prompt`/`tokens_completion` across all `llm_call` trace events for
  the domain, plus a count of tool calls and repair iterations, into
  `{domain}/cost_report.json`.

---

## 13. Build Order (recommended milestones for Claude Code)

1. Scaffold the repo structure from §4; `config.py` + `.env.example` wired up
   with `python-dotenv`.
2. Implement the Pydantic models (§6) with unit tests — get these locked down
   first since everything else depends on them.
3. Implement tools individually with standalone tests before wiring the
   graph: `search_web`, `fetch_url` (incl. network capture), `probe_endpoint`.
4. Build the Docker sandbox: `Dockerfile`, egress proxy, a trivial "echo a
   fixed JSONL line" test script to prove the execution + timeout + network
   policy work before any LLM-generated code touches it.
5. Wire the LangGraph skeleton (§7) with stub node functions that return
   fixed data — validate the routing/conditional-edge logic and the global
   attempt budget in isolation (`tests/test_graph_routing.py`) before
   attaching real LLM calls.
6. Implement `discover` + `investigate` for real against one real, reasonably
   well-behaved domain (pick any live company careers page) — validate the
   evidence-gate logic end to end.
7. Implement `generate_script` + `docker_execute` + `validate` for that same
   domain — get one full successful run producing a valid JSONL file.
8. Implement `failure_diagnosis` + `repair_strategy` — deliberately break
   something (e.g. test against a domain with an unusual pagination scheme)
   to prove the repair loop actually routes correctly per §7.3, not just
   "retry blindly."
9. Run against a **second, unrelated domain** with a different ATS platform
   to confirm generalization — no orchestrator code should need to change.
10. Add trace/cost reporting polish, then tackle the stretch goals (§12) in
    priority order: adversarial domain → confidence scoring → cross-run
    memory.

---

## 14. Running It

```bash
python scripts/run_agent.py --domain swissre.com
```

Produces:
- `generated_scripts/swissre.com/scraper.py` — the standalone scraper
- `generated_scripts/swissre.com/output.jsonl` — the extracted jobs
- `traces/swissre.com_{timestamp}.jsonl` — the full reasoning/tool-call trace
- `generated_scripts/swissre.com/cost_report.json` — token/tool-call cost summary

---

## 15. Coding Conventions

- Type hints everywhere; Pydantic models over bare dicts for any structured
  data crossing a function boundary.
- `ruff` + `black` for formatting/linting.
- No module outside `config.py` reads `os.environ` directly.
- No module under `agent/` may contain a literal company domain or name in
  a conditional — grep for this (`if .*domain.* ==`) as a pre-commit sanity
  check.
- Every node function is wrapped with `traced(...)` (§9) — no node should
  write ad hoc print statements as its only record of what happened.

---

## 16. Definition of Done

- [ ] Given only a domain, the agent runs unattended end-to-end and produces
      a scraper + JSONL output for at least two structurally different real
      domains, with zero orchestrator code changes between them.
- [ ] The generated scraper runs standalone (`python generated_scripts/{domain}/scraper.py`)
      with no LLM API calls and no dependency on the agent process.
- [ ] Every field in the JSONL output matches §5's schema; missing fields are
      `null`, never inferred.
- [ ] No `re` usage anywhere in generated scraper code (statically verified).
- [ ] At least one run demonstrates the repair loop actually firing and
      succeeding after a diagnosed failure (not just a clean first-try run).
- [ ] The adversarial domain produces an honest failure report, not a hang or
      fabricated output.
- [ ] A full trace file and cost report exist for every run, success or
      failure.

from typing import Literal, Optional, TypedDict

from agent.models.evidence import InvestigationEvidence
from agent.models.validation import ValidationReport


class AgentState(TypedDict):
    domain: str
    evidence: InvestigationEvidence
    script_code: Optional[str]
    script_path: Optional[str]
    validation_report: Optional[ValidationReport]
    repair_attempt: int
    total_attempts: int  # GLOBAL counter — decremented on ANY loop-back, see CLAUDE.md §7
    max_total_attempts: int
    status: Literal["running", "success", "failed"]
    trace_summary: list[dict]  # lightweight — summaries + artifact refs only, NOT full payloads

    # Set by docker_execute, consumed by validate (§7.1: failure_diagnosis
    # needs stderr as context) — not in the original §6.5 schema, added
    # because a sandbox failure has to reach the classifier somehow.
    last_stderr: Optional[str]
    last_exit_code: Optional[int]
    last_timed_out: bool

    # Set by investigate, consumed by generate_script — a path into
    # artifacts/ (per §6.5's own "referenced by path/hash, never inlined
    # into state" rule), pointing at a concrete HTML/JSON sample of the real
    # page so codegen writes selectors against actual markup instead of
    # guessing from training data.
    evidence_sample_path: Optional[str]

    # Incremented on every generate_script (fresh or rewrite) and every
    # repair_strategy patch — feeds the trace's "code_generated" events'
    # revision number, and each revision's source is archived to artifacts/.
    script_revision: int

    run_id: str  # trace file discriminator: traces/{domain}_{run_id}.jsonl

    # Set once investigate.py has tried the Firecrawl-Actions escalation for
    # this run, regardless of outcome — evidence_check's insufficient-
    # evidence loop re-invokes investigate() from scratch on every retry,
    # and without this flag it would re-burn a real, paid Firecrawl credit
    # on the exact same URL every single attempt even though the page
    # structure hasn't changed and never will within one run.
    firecrawl_actions_attempted: bool

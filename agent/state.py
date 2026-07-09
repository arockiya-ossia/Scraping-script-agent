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

"""Deterministic schema + sanity checks against the JSONL output
(CLAUDE.md §11). Sandbox-level failures (timeout, non-zero exit) are
classified here first — no point running content checks on a script that
never produced real output — then delegates to agent/tools/validator.py.
"""

from agent.models.validation import FailureCategory, ValidationReport
from agent.nodes import traced
from agent.state import AgentState
from agent.tools.validator import validate_output
from config import settings


@traced
def validate(state: AgentState) -> AgentState:
    domain = state["domain"]
    output_path = settings.output_dir / domain / "output.jsonl"

    if state.get("last_timed_out"):
        state["validation_report"] = ValidationReport(
            passed=False,
            failure_category=FailureCategory.TIMEOUT,
            details="Sandbox wall-clock timeout exceeded.",
        )
        return state

    exit_code = state.get("last_exit_code")
    stderr = state.get("last_stderr") or ""
    if exit_code not in (None, 0):
        category = FailureCategory.SYNTAX_ERROR if "SyntaxError" in stderr else FailureCategory.RUNTIME_ERROR
        state["validation_report"] = ValidationReport(
            passed=False,
            failure_category=category,
            details=stderr[-2000:],
        )
        return state

    report = validate_output(
        script_source=state["script_code"] or "",
        output_path=str(output_path),
        reported_total_count=state["evidence"].reported_total_count,
    )
    state["validation_report"] = report
    if report.passed:
        state["status"] = "success"
    return state

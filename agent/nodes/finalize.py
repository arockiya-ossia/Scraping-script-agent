"""Writes the persistent trace, cost report, and deliverables — handles both
success and honest-failure output (CLAUDE.md §7.1, §12).
"""

import json

from agent.nodes import traced
from agent.state import AgentState
from config import settings


@traced
def finalize(state: AgentState) -> AgentState:
    domain = state["domain"]
    out_dir = settings.output_dir / domain
    out_dir.mkdir(parents=True, exist_ok=True)

    if state["status"] == "running":
        # Budget exhausted without a pass/fail resolution — never leave this
        # ambiguous (CLAUDE.md §7.2: honest failure, never a silent hang).
        state["status"] = "failed"

    cost_report = {
        "domain": domain,
        "status": state["status"],
        "repair_attempts": state["repair_attempt"],
        "total_attempts_used": state["max_total_attempts"] - state["total_attempts"],
    }
    (out_dir / "cost_report.json").write_text(json.dumps(cost_report, indent=2), encoding="utf-8")
    return state

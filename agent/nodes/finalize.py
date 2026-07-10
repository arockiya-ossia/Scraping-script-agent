"""Writes the persistent trace, cost report, and deliverables — handles both
success and honest-failure output (CLAUDE.md §7.1, §12).
"""

import json

from agent.llm.client import llm_client
from agent.nodes import traced
from agent.state import AgentState
from agent.tools.firecrawl_client import firecrawl_credits_used
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
        "tokens_prompt": llm_client.total_tokens_prompt,
        "tokens_completion": llm_client.total_tokens_completion,
        # Firecrawl is a paid, non-linear-cost escalation resource (§12) —
        # 0 unless the HTTP_FORBIDDEN retry, zero-DOM-nodes, or PDF path
        # actually fired this run.
        "firecrawl_credits_used": firecrawl_credits_used(),
    }
    (out_dir / "cost_report.json").write_text(json.dumps(cost_report, indent=2), encoding="utf-8")
    return state

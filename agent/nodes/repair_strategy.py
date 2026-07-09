"""Diagnosis -> RepairAction, per the routing table in CLAUDE.md §7.3.

The routing table itself is pure Python (§7.1: "pure Python routing table").
The LLM call for the repair content lives here too, but only for `patch` —
§7.1 says repair_strategy's tools include "LLM call for the patch/rewrite
itself", and per §7.3's own "Routes to" column, `patch` goes straight to
`docker_execute` (no intermediate codegen node), so the patched script has
to already exist by the time this node returns. `rewrite` deliberately does
NOT call the LLM here — it routes to generate_script, which does a full
regen from evidence + failure context (see generate_script.py's rewrite.md
branch). `re-investigate` calls no LLM at all; investigate.py gathers fresh
evidence.
"""

from pathlib import Path
from typing import Literal

from agent.llm.client import llm_client
from agent.llm.codeformat import extract_code
from agent.models.validation import FailureCategory
from agent.nodes import traced
from agent.state import AgentState

RepairAction = Literal["patch", "rewrite", "re-investigate"]

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "llm" / "prompts"

# PAGINATION_UNDERCOUNT is genuinely ambiguous per §7.3 (off-by-one bug vs.
# wrong approach) — default to "patch" since it's the cheaper, more targeted
# action.
ROUTING_TABLE: dict[FailureCategory, RepairAction] = {
    FailureCategory.SYNTAX_ERROR: "patch",
    FailureCategory.PAGINATION_UNDERCOUNT: "patch",
    FailureCategory.HTTP_FORBIDDEN: "re-investigate",
    FailureCategory.SCHEMA_DRIFT: "re-investigate",
    FailureCategory.ZERO_RESULTS_FILTER_MISMATCH: "re-investigate",
    FailureCategory.ZERO_RESULTS_PARSING_BUG: "patch",
    FailureCategory.CONTAINS_REGEX: "patch",
    FailureCategory.RUNTIME_ERROR: "patch",
    FailureCategory.TIMEOUT: "patch",
    FailureCategory.OTHER: "rewrite",
}


def route_repair(category: FailureCategory) -> RepairAction:
    return ROUTING_TABLE.get(category, "rewrite")


@traced
def repair_strategy(state: AgentState) -> AgentState:
    state["repair_attempt"] += 1
    state["total_attempts"] -= 1

    report = state["validation_report"]
    category = report.failure_category if report else None
    action = route_repair(category) if category else "rewrite"

    if action == "patch" and state.get("script_code"):
        template = (PROMPTS_DIR / "patch.md").read_text(encoding="utf-8")
        prompt = template.format(
            script_code=state["script_code"],
            failure_category=category.value if category else "unknown",
            failure_details=report.details if report else "",
            stderr_excerpt=(state.get("last_stderr") or "")[-2000:],
        )
        response = llm_client.complete(prompt, temperature=0.0)
        patched_code = extract_code(response.content)
        state["script_code"] = patched_code
        if state.get("script_path"):
            Path(state["script_path"]).write_text(patched_code, encoding="utf-8")

    return state

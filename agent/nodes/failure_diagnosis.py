"""Classify *why* validation failed into a FailureCategory (CLAUDE.md §7.1).

Most failures are already categorized deterministically by validate.py
(sandbox exit code, static no-regex check, schema/location/pagination
checks) — this node only escalates to an LLM call when the deterministic
checks genuinely couldn't classify it (failure_category is still None).
"""

from pathlib import Path

from agent.llm.client import llm_client
from agent.llm.codeformat import extract_json
from agent.models.validation import FailureCategory
from agent.nodes import traced
from agent.state import AgentState

PROMPT_PATH = Path(__file__).resolve().parent.parent / "llm" / "prompts" / "diagnosis.md"


@traced
def failure_diagnosis(state: AgentState) -> AgentState:
    report = state["validation_report"]
    if report is None:
        return state
    if report.failure_category is not None:
        return state  # already classified deterministically

    prompt_template = PROMPT_PATH.read_text(encoding="utf-8")
    prompt = prompt_template.format(
        domain=state["domain"],
        validation_report_json=report.model_dump_json(indent=2),
        stderr_excerpt=(state.get("last_stderr") or "")[-2000:],
    )

    try:
        response = llm_client.complete(prompt, temperature=0.0)
        parsed = extract_json(response.content)
        category_str = parsed.get("category") if parsed else None
        report.failure_category = (
            FailureCategory(category_str)
            if category_str in FailureCategory._value2member_map_
            else FailureCategory.OTHER
        )
    except Exception:
        report.failure_category = FailureCategory.OTHER

    return state

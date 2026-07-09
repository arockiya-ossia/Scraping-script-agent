"""InvestigationEvidence -> full scraper source (LLM call, no tools).

Doubles as the "rewrite" repair action (CLAUDE.md §7.3, OTHER/uncategorizable
-> generate_script): if there's a prior failed validation report, the
rewrite.md prompt is used instead of generate_script.md so the model sees
what went wrong and writes a genuinely different approach, not the same
script twice.
"""

import json
from pathlib import Path

from agent.llm.client import llm_client
from agent.llm.codeformat import extract_code
from agent.models.job_record import JobRecord
from agent.nodes import traced
from agent.state import AgentState
from config import settings

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "llm" / "prompts"


@traced
def generate_script(state: AgentState) -> AgentState:
    domain = state["domain"]
    evidence = state["evidence"]
    report = state.get("validation_report")

    is_rewrite = bool(state.get("script_code")) and report is not None and not report.passed

    if is_rewrite:
        template = (PROMPTS_DIR / "rewrite.md").read_text(encoding="utf-8")
        prompt = template.format(
            domain=domain,
            evidence_json=evidence.model_dump_json(indent=2),
            script_code=state["script_code"],
            failure_category=report.failure_category.value if report.failure_category else "unknown",
            failure_details=report.details,
        )
    else:
        template = (PROMPTS_DIR / "generate_script.md").read_text(encoding="utf-8")
        prompt = template.format(
            domain=domain,
            evidence_json=evidence.model_dump_json(indent=2),
            job_record_schema=json.dumps(JobRecord.model_json_schema(), indent=2),
        )

    response = llm_client.complete(prompt, temperature=0.0)
    script_code = extract_code(response.content)

    script_dir = settings.output_dir / domain
    script_dir.mkdir(parents=True, exist_ok=True)
    script_path = script_dir / "scraper.py"
    script_path.write_text(script_code, encoding="utf-8")

    state["script_code"] = script_code
    state["script_path"] = str(script_path)
    state["validation_report"] = None  # clear stale report for the fresh run
    return state

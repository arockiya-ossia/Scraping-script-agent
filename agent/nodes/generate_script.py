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
from agent.trace.sink import trace_sink
from config import settings

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "llm" / "prompts"


def _read_evidence_sample(state: AgentState) -> str:
    sample_path = state.get("evidence_sample_path")
    if sample_path and Path(sample_path).exists():
        return Path(sample_path).read_text(encoding="utf-8")[:8000]
    return "(no concrete sample captured during investigation)"


@traced
def generate_script(state: AgentState) -> AgentState:
    domain = state["domain"]
    evidence = state["evidence"]
    report = state.get("validation_report")
    run_id = state.get("run_id", "run")
    evidence_sample = _read_evidence_sample(state)

    is_rewrite = bool(state.get("script_code")) and report is not None and not report.passed

    job_record_example = json.dumps(JobRecord().model_dump(), indent=2)

    if is_rewrite:
        template = (PROMPTS_DIR / "rewrite.md").read_text(encoding="utf-8")
        prompt = template.format(
            domain=domain,
            evidence_json=evidence.model_dump_json(indent=2),
            evidence_sample=evidence_sample,
            script_code=state["script_code"],
            failure_category=report.failure_category.value if report.failure_category else "unknown",
            failure_details=report.details,
            job_record_example=job_record_example,
        )
        mode = "rewrite"
    else:
        template = (PROMPTS_DIR / "generate_script.md").read_text(encoding="utf-8")
        prompt = template.format(
            domain=domain,
            evidence_json=evidence.model_dump_json(indent=2),
            evidence_sample=evidence_sample,
            job_record_example=job_record_example,
        )
        mode = "fresh"

    trace_sink.emit(domain, run_id, type="tool_call", node="generate_script", tool="llm_client.complete", input={"mode": mode})
    response = llm_client.complete(prompt, temperature=0.0)
    trace_sink.emit(
        domain, run_id, type="tool_result", node="generate_script", tool="llm_client.complete",
        tokens_prompt=response.tokens_prompt, tokens_completion=response.tokens_completion,
    )
    script_code = extract_code(response.content)

    script_dir = settings.output_dir / domain
    script_dir.mkdir(parents=True, exist_ok=True)
    script_path = script_dir / "scraper.py"
    script_path.write_text(script_code, encoding="utf-8")

    state["script_revision"] = state.get("script_revision", 0) + 1
    revision_artifact = trace_sink.save_artifact(domain, f"scraper_v{state['script_revision']}.py", script_code)
    trace_sink.emit(
        domain, run_id, type="code_generated", node="generate_script",
        revision=state["script_revision"], mode=mode, path=str(script_path), artifact_ref=revision_artifact,
    )

    state["script_code"] = script_code
    state["script_path"] = str(script_path)
    state["validation_report"] = None  # clear stale report for the fresh run
    return state

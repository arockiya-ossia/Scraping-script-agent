"""Run the generated script in the sandbox, capture stdout/stderr/exit code
and the output file (CLAUDE.md §7.1, §8). exit_code/stderr/timed_out are
stashed on state so `validate` can classify SYNTAX_ERROR/RUNTIME_ERROR/
TIMEOUT before it even looks at the output file.
"""

import time
from pathlib import Path

from agent.nodes import traced
from agent.state import AgentState
from agent.tools import sandbox
from agent.trace.sink import trace_sink
from config import settings


@traced
def docker_execute(state: AgentState) -> AgentState:
    domain = state["domain"]
    run_id = state.get("run_id", "run")
    script_path = Path(state["script_path"])
    output_dir = settings.output_dir / domain

    sandbox.build_image()  # cheap: cached layers after the first real build

    started = time.perf_counter()
    result = sandbox.run_script(script_path, output_dir, domain)
    duration = round(time.perf_counter() - started, 2)

    stderr_artifact = trace_sink.save_artifact(domain, "stderr.txt", result.stderr) if result.stderr else None
    trace_sink.emit(
        domain, run_id, type="execution", node="docker_execute",
        exit_code=result.exit_code, duration=duration, timed_out=result.timed_out,
        artifact_ref=stderr_artifact,
    )

    state["last_stderr"] = result.stderr
    state["last_exit_code"] = result.exit_code
    state["last_timed_out"] = result.timed_out
    return state

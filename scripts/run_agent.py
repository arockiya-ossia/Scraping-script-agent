"""CLI entry point.

    python scripts/run_agent.py --domain swissre.com
"""

import argparse
import sys
import time

# Windows' console defaults to cp1252, which can't render job-description
# text (curly quotes, non-English names, etc.) — reconfigure before
# anything prints, so the CLI works without the caller having to set
# PYTHONIOENCODING=utf-8 manually every run.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from agent.graph import build_graph
from agent.models.evidence import InvestigationEvidence
from agent.state import AgentState
from agent.trace.sink import trace_sink
from config import settings


def run(domain: str) -> AgentState:
    trace_sink.console = True  # live progress in the terminal, not just the trace file
    graph = build_graph()
    initial_state: AgentState = {
        "domain": domain,
        "evidence": InvestigationEvidence(),
        "script_code": None,
        "script_path": None,
        "validation_report": None,
        "repair_attempt": 0,
        "total_attempts": settings.max_total_attempts,
        "max_total_attempts": settings.max_total_attempts,
        "status": "running",
        "trace_summary": [],
        "last_stderr": None,
        "last_exit_code": None,
        "last_timed_out": False,
        "evidence_sample_path": None,
        "script_revision": 0,
        "run_id": str(int(time.time())),
        "firecrawl_actions_attempted": False,
    }
    final_state = graph.invoke(initial_state, {"recursion_limit": 200})
    return final_state


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate and validate a job scraper for a domain.")
    parser.add_argument("--domain", required=True, help="Company domain, e.g. swissre.com")
    args = parser.parse_args()

    final_state = run(args.domain)
    print(f"domain={args.domain} status={final_state['status']}")


if __name__ == "__main__":
    main()

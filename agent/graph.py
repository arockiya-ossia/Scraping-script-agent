"""LangGraph StateGraph wiring per CLAUDE.md §7. Two loop-back points
(Evidence Check -> More Investigation, Validator FAIL -> Repair) both
decrement the same `total_attempts` global budget counter.
"""

from langgraph.graph import END, StateGraph

from agent.nodes.discover import discover
from agent.nodes.docker_execute import docker_execute
from agent.nodes.evidence_check import evidence_check
from agent.nodes.failure_diagnosis import failure_diagnosis
from agent.nodes.finalize import finalize
from agent.nodes.generate_script import generate_script
from agent.nodes.investigate import investigate
from agent.nodes.repair_strategy import repair_strategy, route_repair
from agent.nodes.validate import validate
from agent.state import AgentState


def _route_evidence(state: AgentState) -> str:
    # Pure read — the budget decrement already happened inside the
    # evidence_check node (LangGraph doesn't persist mutations made in a
    # conditional-edge function, only in a node's return value).
    if state["evidence"].is_sufficient():
        return "sufficient"
    if state["total_attempts"] <= 0:
        return "budget_exhausted"
    return "insufficient"


def _route_validation(state: AgentState) -> str:
    report = state["validation_report"]
    if report is not None and report.passed:
        return "pass"
    if state["total_attempts"] <= 0:
        return "budget_exhausted"
    return "fail"


def _route_repair(state: AgentState) -> str:
    report = state["validation_report"]
    category = report.failure_category if report else None
    if category is None:
        return "generate_script"
    action = route_repair(category)
    return {"patch": "docker_execute", "rewrite": "generate_script", "re-investigate": "investigate"}[action]


def build_graph():
    graph = StateGraph(AgentState)

    graph.add_node("discover", discover)
    graph.add_node("investigate", investigate)
    graph.add_node("evidence_check", evidence_check)
    graph.add_node("generate_script", generate_script)
    graph.add_node("docker_execute", docker_execute)
    graph.add_node("validate", validate)
    graph.add_node("failure_diagnosis", failure_diagnosis)
    graph.add_node("repair_strategy", repair_strategy)
    graph.add_node("finalize", finalize)

    graph.set_entry_point("discover")
    graph.add_edge("discover", "investigate")
    graph.add_edge("investigate", "evidence_check")

    graph.add_conditional_edges(
        "evidence_check",
        _route_evidence,
        {
            "sufficient": "generate_script",
            "insufficient": "investigate",
            "budget_exhausted": "finalize",
        },
    )

    graph.add_edge("generate_script", "docker_execute")
    graph.add_edge("docker_execute", "validate")

    graph.add_conditional_edges(
        "validate",
        _route_validation,
        {
            "pass": "finalize",
            "fail": "failure_diagnosis",
            "budget_exhausted": "finalize",
        },
    )

    graph.add_edge("failure_diagnosis", "repair_strategy")

    graph.add_conditional_edges(
        "repair_strategy",
        _route_repair,
        {
            "docker_execute": "docker_execute",
            "generate_script": "generate_script",
            "investigate": "investigate",
        },
    )

    graph.add_edge("finalize", END)

    return graph.compile()

"""Deterministic gate — evidence.is_sufficient(). No LLM call, no tools.
Never let a model self-assess its own certainty here (CLAUDE.md §6.2).
"""

from agent.nodes import traced
from agent.state import AgentState


@traced
def evidence_check(state: AgentState) -> AgentState:
    # Budget decrement lives in the node (not the conditional-edge function)
    # because LangGraph only persists state mutations returned by nodes —
    # a conditional edge is purely a routing read and any mutation there is
    # silently dropped, which caused an infinite loop before this fix.
    if not state["evidence"].is_sufficient() and state["total_attempts"] > 0:
        state["total_attempts"] -= 1
    return state

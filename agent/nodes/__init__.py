from functools import wraps

from agent.state import AgentState
from agent.trace.sink import trace_sink


def traced(node_fn):
    """Wraps a node function so entry/exit are always recorded — no node
    should rely on ad hoc print statements as its only record (CLAUDE.md §9).
    """

    @wraps(node_fn)
    def wrapper(state: AgentState) -> AgentState:
        run_id = state.get("run_id", "run")
        trace_sink.emit(state["domain"], run_id, type="node_enter", node=node_fn.__name__)
        new_state = node_fn(state)
        trace_sink.emit(new_state["domain"], run_id, type="node_exit", node=node_fn.__name__)
        return new_state

    return wrapper

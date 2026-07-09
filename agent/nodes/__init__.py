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
        trace_sink.emit(
            state["domain"], run_id, node=node_fn.__name__, event_type="node_enter", summary="entered"
        )
        new_state = node_fn(state)
        trace_sink.emit(
            state["domain"], run_id, node=node_fn.__name__, event_type="node_exit", summary="exited"
        )
        return new_state

    return wrapper

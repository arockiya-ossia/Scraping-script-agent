from typing import Optional

from pydantic import BaseModel, Field


def _now() -> float:
    import time

    return time.time()


class TraceEvent(BaseModel):
    timestamp: float = Field(default_factory=_now)
    domain: str
    node: str
    event_type: str  # "node_enter", "tool_call", "llm_call", "node_exit", "repair_decision"
    summary: str  # short human-readable line, kept in LangGraph state
    artifact_ref: Optional[str] = None  # path into artifacts/ for full payloads
    tokens_prompt: Optional[int] = None
    tokens_completion: Optional[int] = None

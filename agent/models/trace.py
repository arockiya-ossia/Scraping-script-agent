from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


def _now() -> float:
    import time

    return time.time()


class TraceEvent(BaseModel):
    """The persistent trace deliverable's per-line schema (CLAUDE.md §9).

    `extra="allow"` is deliberate: the different event `type`s (tool_call,
    tool_result, decision, evidence, code_generated, execution, validation,
    repair_decision, ...) carry genuinely different payload shapes. Forcing
    one giant flat schema with dozens of Optional fields would be worse than
    letting each call site attach exactly the fields relevant to its event
    type, while still requiring the fields every event needs regardless of
    type (timestamp, domain, type).
    """

    model_config = ConfigDict(extra="allow")

    timestamp: float = Field(default_factory=_now)
    domain: str
    type: str  # "node_enter", "tool_call", "tool_result", "decision", "evidence",
    # "code_generated", "execution", "validation", "repair_decision", "node_exit", ...
    node: Optional[str] = None
    summary: Optional[str] = None
    artifact_ref: Optional[str] = None
    tokens_prompt: Optional[int] = None
    tokens_completion: Optional[int] = None

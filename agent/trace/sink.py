import json
from pathlib import Path
from typing import Any, Optional

from config import settings
from agent.models.trace import TraceEvent


class TraceSink:
    """Append-only JSONL trace writer. Full events go to disk; only a short
    summary + artifact_ref is returned for LangGraph state (see CLAUDE.md §9).
    """

    def __init__(self, trace_dir: Path = settings.trace_dir):
        self.trace_dir = trace_dir
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        self._paths: dict[str, Path] = {}

    def _path_for(self, domain: str, run_id: str) -> Path:
        key = f"{domain}_{run_id}"
        if key not in self._paths:
            self._paths[key] = self.trace_dir / f"{key}.jsonl"
        return self._paths[key]

    def emit(
        self,
        domain: str,
        run_id: str,
        node: str,
        event_type: str,
        summary: str,
        artifact_ref: Optional[str] = None,
        tokens_prompt: Optional[int] = None,
        tokens_completion: Optional[int] = None,
        extra: Optional[dict[str, Any]] = None,
    ) -> dict:
        event = TraceEvent(
            domain=domain,
            node=node,
            event_type=event_type,
            summary=summary,
            artifact_ref=artifact_ref,
            tokens_prompt=tokens_prompt,
            tokens_completion=tokens_completion,
        )
        path = self._path_for(domain, run_id)
        record = event.model_dump()
        if extra:
            record["extra"] = extra
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
        return event.model_dump(exclude={"extra"})


trace_sink = TraceSink()

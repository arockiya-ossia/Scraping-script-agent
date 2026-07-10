import itertools
import json
from pathlib import Path
from typing import Any, Optional

from config import settings
from agent.models.trace import TraceEvent


class TraceSink:
    """Append-only JSONL trace writer — the full reasoning/tool-call
    deliverable (CLAUDE.md §9, Constraint #6). Each call site passes exactly
    the fields relevant to its event `type`; TraceEvent's `extra="allow"`
    lets those ride along without forcing one bloated universal schema.

    Full tool I/O that's too large to inline (HTML dumps, network captures,
    script revisions) goes through `save_artifact` and is referenced by path
    — never inlined into the trace line itself or into LangGraph state.
    """

    def __init__(self, trace_dir: Path = settings.trace_dir, artifacts_dir: Path = settings.artifacts_dir):
        self.trace_dir = trace_dir
        self.artifacts_dir = artifacts_dir
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        self._paths: dict[str, Path] = {}
        self._artifact_counters: dict[str, itertools.count] = {}

    def _path_for(self, domain: str, run_id: str) -> Path:
        key = f"{domain}_{run_id}"
        if key not in self._paths:
            self._paths[key] = self.trace_dir / f"{key}.jsonl"
        return self._paths[key]

    def emit(
        self,
        domain: str,
        run_id: str,
        type: str,
        node: Optional[str] = None,
        summary: Optional[str] = None,
        artifact_ref: Optional[str] = None,
        tokens_prompt: Optional[int] = None,
        tokens_completion: Optional[int] = None,
        **extra: Any,
    ) -> dict:
        event = TraceEvent(
            domain=domain,
            type=type,
            node=node,
            summary=summary,
            artifact_ref=artifact_ref,
            tokens_prompt=tokens_prompt,
            tokens_completion=tokens_completion,
            **extra,
        )
        path = self._path_for(domain, run_id)
        record = event.model_dump(exclude_none=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
        return record

    def save_artifact(self, domain: str, name: str, content: str) -> str:
        """Write a full payload (HTML, network capture JSON, a script
        revision, stderr dump) to artifacts/{domain}/ and return the path as
        a string suitable for a trace event's `artifact_ref`/`path` field.
        """
        domain_dir = self.artifacts_dir / domain
        domain_dir.mkdir(parents=True, exist_ok=True)
        counter = self._artifact_counters.setdefault(domain, itertools.count(1))
        n = next(counter)
        path = domain_dir / f"{n:03d}_{name}"
        path.write_text(content, encoding="utf-8")
        return str(path)


trace_sink = TraceSink()

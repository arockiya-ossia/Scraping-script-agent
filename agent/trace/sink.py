import itertools
import json
import sys
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

    `console` (off by default — tests shouldn't be spammed) mirrors a short,
    human-readable line to stdout for every event as it happens, so
    `python scripts/run_agent.py --domain X` shows live progress instead of
    going silent until the final status line.
    """

    def __init__(
        self,
        trace_dir: Path = settings.trace_dir,
        artifacts_dir: Path = settings.artifacts_dir,
        console: bool = False,
    ):
        self.trace_dir = trace_dir
        self.artifacts_dir = artifacts_dir
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        self._paths: dict[str, Path] = {}
        self._artifact_counters: dict[str, itertools.count] = {}
        self.console = console

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
        if self.console:
            self._print_console_line(record)
        return record

    def _print_console_line(self, record: dict) -> None:
        line = _format_console_line(record)
        if not line:
            return
        try:
            print(line, flush=True)
        except UnicodeEncodeError:
            # Windows console (cp1252) can't render some job-description
            # text — degrade rather than crash the run over a log line.
            sys.stdout.buffer.write((line + "\n").encode("utf-8", errors="replace"))
            sys.stdout.flush()

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


def _truncate(value: Any, limit: int = 160) -> str:
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _format_console_line(record: dict) -> Optional[str]:
    node = record.get("node") or "-"
    event_type = record.get("type")

    if event_type == "node_enter":
        return f">>> {node}"
    if event_type == "node_exit":
        return None  # node_enter already marked the transition; skip the noise
    if event_type == "tool_call":
        tool = record.get("tool", "?")
        detail = record.get("input")
        return f"    [{node}] -> {tool}({_truncate(detail) if detail else ''})"
    if event_type == "tool_result":
        tool = record.get("tool", "?")
        if record.get("error"):
            return f"    [{node}] <- {tool} ERROR: {_truncate(record['error'])}"
        bits = [f"{k}={v}" for k, v in record.items() if k not in ("timestamp", "domain", "type", "node", "tool")]
        return f"    [{node}] <- {tool}: {_truncate(', '.join(bits))}"
    if event_type == "decision":
        action = record.get("action", "?")
        rationale = record.get("rationale", "")
        return f"    [{node}] DECISION {action}: {_truncate(rationale)}"
    if event_type == "evidence":
        return (
            f"    [{node}] EVIDENCE source_type={record.get('source_type')} "
            f"pagination_confirmed={record.get('pagination_param_confirmed')} "
            f"sufficient={record.get('sufficient')} confidence={record.get('confidence')}"
        )
    if event_type == "code_generated":
        return f"    [{node}] CODE revision={record.get('revision')} mode={record.get('mode')} -> {record.get('path')}"
    if event_type == "execution":
        return (
            f"    [{node}] EXEC exit_code={record.get('exit_code')} "
            f"duration={record.get('duration')}s timed_out={record.get('timed_out')}"
        )
    if event_type == "validation":
        return (
            f"    [{node}] VALIDATE passed={record.get('passed')} rows={record.get('row_count')} "
            f"category={record.get('failure_category')}"
        )
    if event_type == "repair_decision":
        return (
            f"    [{node}] REPAIR action={record.get('action')} "
            f"category={record.get('failure_category')} attempt={record.get('repair_attempt')}"
        )
    return None


trace_sink = TraceSink()

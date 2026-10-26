"""Thread-safe run sessions and LangChain callback events for the web UI."""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Iterator

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import BaseMessage
from langchain_core.outputs import LLMResult


TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
MAX_EVENT_TEXT = 16_000


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _truncate(value: str, limit: int = MAX_EVENT_TEXT) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}\n… [{len(value) - limit:,} characters omitted]"


def json_safe(value: Any, *, limit: int = MAX_EVENT_TEXT) -> Any:
    """Convert LangChain payloads to bounded JSON values without leaking repr noise."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _truncate(value, limit)
    if isinstance(value, BaseMessage):
        return {
            "message_type": value.type,
            "content": content_text(value.content),
            "name": getattr(value, "name", None),
            "id": getattr(value, "id", None),
        }
    if isinstance(value, dict):
        items = list(value.items())
        result = {
            str(key): json_safe(item, limit=limit) for key, item in items[:100]
        }
        if len(items) > 100:
            result["__truncated__"] = f"{len(items) - 100} more fields omitted"
    elif isinstance(value, (list, tuple, set)):
        items = list(value)
        result = [json_safe(item, limit=limit) for item in items[:100]]
        if len(items) > 100:
            result.append(f"… {len(items) - 100} more items omitted")
    else:
        result = _truncate(str(value), limit)
    return result


def content_text(content: Any) -> str:
    if isinstance(content, str):
        return _truncate(content)
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text") or block.get("content")
                if text:
                    parts.append(str(text))
        return _truncate("\n".join(parts))
    return _truncate(str(content))


@dataclass
class RunSession:
    request: dict[str, Any]
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: str = field(default_factory=utc_now)
    status: str = "queued"
    events: list[dict[str, Any]] = field(default_factory=list)
    final_result: dict[str, Any] | None = None
    error: str | None = None
    cancel_requested: bool = False
    _condition: threading.Condition = field(default_factory=threading.Condition, repr=False)

    def publish(self, event_type: str, **payload: Any) -> dict[str, Any]:
        with self._condition:
            event = {
                "id": len(self.events) + 1,
                "type": event_type,
                "timestamp": utc_now(),
                **{key: json_safe(value) for key, value in payload.items()},
            }
            self.events.append(event)
            self._condition.notify_all()
            return event

    def set_status(self, status: str, *, error: str | None = None) -> None:
        with self._condition:
            self.status = status
            self.error = error
            self._condition.notify_all()

    def snapshot(self) -> dict[str, Any]:
        with self._condition:
            return {
                "id": self.id,
                "status": self.status,
                "created_at": self.created_at,
                "request": self.request,
                "event_count": len(self.events),
                "final_result": self.final_result,
                "error": self.error,
                "cancel_requested": self.cancel_requested,
            }

    def iter_events(self, after: int = 0, heartbeat_seconds: float = 15) -> Iterator[str]:
        cursor = max(0, after)
        while True:
            with self._condition:
                if cursor >= len(self.events) and self.status not in TERMINAL_STATUSES:
                    self._condition.wait(timeout=heartbeat_seconds)
                pending = self.events[cursor:]
                status = self.status
            if pending:
                for event in pending:
                    cursor = event["id"]
                    data = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
                    yield f"id: {cursor}\ndata: {data}\n\n"
                continue
            if status in TERMINAL_STATUSES:
                break
            yield ": heartbeat\n\n"


class RunRegistry:
    """Keeps a bounded in-memory history and serializes costly analysis runs."""

    def __init__(self, max_runs: int = 30) -> None:
        self.max_runs = max_runs
        self._runs: dict[str, RunSession] = {}
        self._lock = threading.Lock()

    def create(self, request: dict[str, Any]) -> RunSession:
        with self._lock:
            active = next(
                (run for run in self._runs.values() if run.status not in TERMINAL_STATUSES),
                None,
            )
            if active is not None:
                raise RuntimeError(active.id)
            session = RunSession(request=request)
            self._runs[session.id] = session
            completed = [
                run for run in self._runs.values() if run.status in TERMINAL_STATUSES
            ]
            for stale in sorted(completed, key=lambda item: item.created_at)[:-self.max_runs]:
                self._runs.pop(stale.id, None)
            session.publish("run.created", status="queued", request=request)
            return session

    def get(self, run_id: str) -> RunSession | None:
        with self._lock:
            return self._runs.get(run_id)

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            runs = list(self._runs.values())
        return [run.snapshot() for run in sorted(runs, key=lambda item: item.created_at, reverse=True)]


class LiveTraceCallback(BaseCallbackHandler):
    """Publish observable LangChain activity without exposing hidden reasoning traces."""

    def __init__(self, session: RunSession) -> None:
        super().__init__()
        self.session = session
        self._seen_events: set[tuple[str, str]] = set()
        self._lock = threading.Lock()

    @staticmethod
    def _run_id(kwargs: dict[str, Any]) -> str | None:
        value = kwargs.get("run_id")
        return str(value) if value is not None else None

    @staticmethod
    def _node(kwargs: dict[str, Any]) -> str | None:
        metadata = kwargs.get("metadata") or {}
        return metadata.get("langgraph_node") or metadata.get("node")

    def _first(self, event_type: str, kwargs: dict[str, Any]) -> bool:
        """Deduplicate a handler attached at both model and graph config levels."""
        run_id = self._run_id(kwargs)
        if run_id is None:
            return True
        key = (event_type, run_id)
        with self._lock:
            if key in self._seen_events:
                return False
            self._seen_events.add(key)
        return True

    def on_chain_start(
        self,
        serialized: dict[str, Any],
        inputs: dict[str, Any],
        **kwargs: Any,
    ) -> None:
        node = self._node(kwargs)
        run_id = self._run_id(kwargs)
        if not node or not run_id or not self._first("node.started", kwargs):
            return
        self.session.publish("node.started", node=node, run_id=run_id)

    def on_chain_end(self, outputs: dict[str, Any], **kwargs: Any) -> None:
        node = self._node(kwargs)
        run_id = self._run_id(kwargs)
        if node and run_id and self._first("node.completed", kwargs):
            self.session.publish("node.completed", node=node, run_id=run_id)

    def on_chain_error(self, error: BaseException, **kwargs: Any) -> None:
        if not self._first("node.failed", kwargs):
            return
        node = self._node(kwargs)
        self.session.publish(
            "node.failed",
            node=node,
            run_id=self._run_id(kwargs),
            error=str(error),
        )

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[BaseMessage]],
        **kwargs: Any,
    ) -> None:
        if not self._first("llm.started", kwargs):
            return
        model = serialized.get("name") or (serialized.get("id") or ["chat-model"])[-1]
        self.session.publish(
            "llm.started",
            model=model,
            node=self._node(kwargs),
            run_id=self._run_id(kwargs),
            message_count=sum(len(batch) for batch in messages),
        )

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        **kwargs: Any,
    ) -> None:
        if not self._first("llm.started", kwargs):
            return
        model = serialized.get("name") or (serialized.get("id") or ["llm"])[-1]
        self.session.publish(
            "llm.started",
            model=model,
            node=self._node(kwargs),
            run_id=self._run_id(kwargs),
            message_count=len(prompts),
        )

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        if not self._first("llm.completed", kwargs):
            return
        content = ""
        usage: dict[str, Any] = {}
        try:
            generation = response.generations[0][0]
            message = getattr(generation, "message", None)
            content = content_text(getattr(message, "content", "") or getattr(generation, "text", ""))
            usage = getattr(message, "usage_metadata", None) or response.llm_output or {}
        except (IndexError, TypeError, AttributeError):
            pass
        self.session.publish(
            "llm.completed",
            node=self._node(kwargs),
            run_id=self._run_id(kwargs),
            output=content,
            usage=usage,
        )

    def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        if not self._first("llm.failed", kwargs):
            return
        self.session.publish(
            "llm.failed",
            node=self._node(kwargs),
            run_id=self._run_id(kwargs),
            error=str(error),
        )

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        **kwargs: Any,
    ) -> None:
        if not self._first("tool.started", kwargs):
            return
        self.session.publish(
            "tool.started",
            tool=serialized.get("name") or "tool",
            node=self._node(kwargs),
            run_id=self._run_id(kwargs),
            input=json_safe(kwargs.get("inputs") or input_str),
        )

    def on_tool_end(self, output: Any, **kwargs: Any) -> None:
        if not self._first("tool.completed", kwargs):
            return
        self.session.publish(
            "tool.completed",
            node=self._node(kwargs),
            run_id=self._run_id(kwargs),
            output=json_safe(output),
        )

    def on_tool_error(self, error: BaseException, **kwargs: Any) -> None:
        if not self._first("tool.failed", kwargs):
            return
        self.session.publish(
            "tool.failed",
            node=self._node(kwargs),
            run_id=self._run_id(kwargs),
            error=str(error),
        )


def elapsed_ms(started_at: float) -> int:
    return round((time.monotonic() - started_at) * 1000)

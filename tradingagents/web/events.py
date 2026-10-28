"""Web 事件核心：线程安全地存储任务、分发主题并桥接 LangChain Callback。"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from fnmatch import fnmatchcase
from typing import Any, Callable, Iterator

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import BaseMessage
from langchain_core.outputs import LLMResult


# 进入这些状态后 SSE 生成器可以安全结束连接。
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
# 限制单个文本字段，防止工具返回超大内容拖垮内存和浏览器。
MAX_EVENT_TEXT = 16_000
# 事件信封版本供未来兼容性升级使用。
EVENT_SCHEMA_VERSION = 1
# 模块 logger 只记录订阅处理器自身的异常，不中断主分析。
logger = logging.getLogger(__name__)

# 统一事件和同步处理器的类型别名。
Event = dict[str, Any]
EventHandler = Callable[[Event], None]

# 为已知图节点提供稳定 ID、角色和多空方向，供主题路由使用。
AGENT_PROFILES: dict[str, dict[str, str]] = {
    "Market Analyst": {"id": "market_analyst", "role": "analyst"},
    "Sentiment Analyst": {"id": "sentiment_analyst", "role": "analyst"},
    "News Analyst": {"id": "news_analyst", "role": "analyst"},
    "Fundamentals Analyst": {"id": "fundamentals_analyst", "role": "analyst"},
    "Bull Researcher": {"id": "bull_researcher", "role": "researcher", "side": "bull"},
    "Bear Researcher": {"id": "bear_researcher", "role": "researcher", "side": "bear"},
    "Research Manager": {"id": "research_manager", "role": "manager"},
    "Trader": {"id": "trader", "role": "trader"},
    "Aggressive Analyst": {"id": "aggressive_analyst", "role": "risk", "side": "aggressive"},
    "Neutral Analyst": {"id": "neutral_analyst", "role": "risk", "side": "neutral"},
    "Conservative Analyst": {"id": "conservative_analyst", "role": "risk", "side": "conservative"},
    "Portfolio Manager": {"id": "portfolio_manager", "role": "manager"},
    "Memory Log": {"id": "memory_log", "role": "system"},
}

# 将事件名称后缀归一化成 start/update/end/error 四阶段。
EVENT_PHASES = {
    "created": "start",
    "started": "start",
    "ready": "update",
    "updated": "update",
    "cancellation_requested": "update",
    "completed": "end",
    "cancelled": "end",
    "failed": "error",
}


def utc_now() -> str:
    """返回带时区的 UTC ISO 时间，确保浏览器可无歧义解析。"""
    return datetime.now(UTC).isoformat()


def _truncate(value: str, limit: int = MAX_EVENT_TEXT) -> str:
    """保留字符串前部并明确标注被省略的字符数量。"""
    if len(value) <= limit:
        return value
    return f"{value[:limit]}\n… [{len(value) - limit:,} characters omitted]"


def json_safe(value: Any, *, limit: int = MAX_EVENT_TEXT) -> Any:
    """把任意 LangChain 载荷递归转成有大小上限的 JSON 安全值。"""
    # JSON 原生标量无需转换。
    if value is None or isinstance(value, (bool, int, float)):
        return value
    # 字符串只需执行长度限制。
    if isinstance(value, str):
        return _truncate(value, limit)
    # LangChain 消息只公开可观察字段，不暴露对象内部状态。
    if isinstance(value, BaseMessage):
        return {
            "message_type": value.type,
            "content": content_text(value.content),
            "name": getattr(value, "name", None),
            "id": getattr(value, "id", None),
        }
    # 字典最多保留一百个字段，并递归清理每个值。
    if isinstance(value, dict):
        items = list(value.items())
        result = {
            str(key): json_safe(item, limit=limit) for key, item in items[:100]
        }
        if len(items) > 100:
            result["__truncated__"] = f"{len(items) - 100} more fields omitted"
    # 序列同样限制项目数；set 在此处转为可序列化数组。
    elif isinstance(value, (list, tuple, set)):
        items = list(value)
        result = [json_safe(item, limit=limit) for item in items[:100]]
        if len(items) > 100:
            result.append(f"… {len(items) - 100} more items omitted")
    # 未知对象使用其字符串表示，避免 JSON 编码失败。
    else:
        result = _truncate(str(value), limit)
    return result


def content_text(content: Any) -> str:
    """从纯文本或多模态内容块中提取可在页面展示的文本。"""
    if isinstance(content, str):
        return _truncate(content)
    if isinstance(content, list):
        # OpenAI/Anthropic 等模型可能把内容表示为 block 数组。
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


def _slug(value: str) -> str:
    """把显示名称转换成适合事件主题的稳定 snake_case 标识。"""
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def describe_agent(name: str | None) -> dict[str, str] | None:
    """返回前后端订阅器共用的稳定 Agent 元数据。"""
    if not name:
        return None
    # 未登记的新节点仍获得通用 agent 角色，系统可以向前兼容。
    profile = {"id": _slug(name), "role": "agent"}
    profile.update(AGENT_PROFILES.get(name, {}))
    return {"name": name, **profile}


def _event_topics(event_type: str, agent: dict[str, str] | None) -> list[str]:
    """为单个事件生成类型、Agent、角色及多空方向等可匹配主题。"""
    # family/action 分别是点号前后的生命周期部分，例如 node/started。
    family, _, action = event_type.partition(".")
    # 原始类型始终是第一个主题，因此 tool.* 等通配订阅自然可用。
    topics = [event_type]
    if not agent:
        return topics

    # Agent 事件同时支持按具体节点和按角色订阅。
    agent_id = agent["id"]
    role = agent["role"]
    topics.extend(
        [
            "agent.activity",
            f"agent.{agent_id}.activity",
            f"agent.role.{role}.activity",
            f"agent.{agent_id}.{event_type}",
            f"agent.role.{role}.{event_type}",
        ]
    )
    # bull/bear/risk stance 仅对具有 side 的角色生成。
    side = agent.get("side")
    if side:
        topics.extend(
            [f"agent.side.{side}.activity", f"agent.side.{side}.{event_type}"]
        )
    # node 生命周期额外提供更直观的 agent.started/completed/failed 别名。
    if family == "node":
        topics.extend(
            [
                f"agent.{action}",
                f"agent.{agent_id}.{action}",
                f"agent.role.{role}.{action}",
            ]
        )
        if side:
            topics.append(f"agent.side.{side}.{action}")
    # dict 保序去重，避免同一处理器被一个事件重复命中。
    return list(dict.fromkeys(topics))


def build_event(
    session_id: str,
    event_id: int,
    event_type: str,
    payload: dict[str, Any],
) -> Event:
    """构造版本化事件信封，同时保留旧页面依赖的扁平载荷字段。"""
    # 所有外部对象先做 JSON 安全化和大小限制。
    safe_payload = {key: json_safe(value) for key, value in payload.items()}
    # LangChain run_id 默认就是该生命周期最合适的关联 ID。
    if "correlation_id" not in safe_payload and safe_payload.get("run_id"):
        safe_payload["correlation_id"] = safe_payload["run_id"]
    # node 或显式 agent_name 决定事件归属于哪个 Agent。
    agent_name = safe_payload.get("agent_name") or safe_payload.get("node")
    # 兼容曾经使用字符串 agent 字段的发布调用。
    if not agent_name and isinstance(safe_payload.get("agent"), str):
        agent_name = safe_payload.pop("agent")
        safe_payload["agent_name"] = agent_name
    agent = describe_agent(str(agent_name)) if agent_name else None
    # family 和 phase 让上层无需重复解析字符串后缀。
    family, _, action = event_type.partition(".")
    event: Event = {
        **safe_payload,
        "schema_version": EVENT_SCHEMA_VERSION,
        "id": event_id,
        "type": event_type,
        "family": family,
        "phase": EVENT_PHASES.get(action, "update"),
        "timestamp": utc_now(),
        "session_id": session_id,
        "topics": _event_topics(event_type, agent),
    }
    if agent:
        event["agent"] = agent
    return event


class EventDispatcher:
    """线程安全主题分发器；subscribe 返回 pi-agent 风格的取消函数。"""

    def __init__(self) -> None:
        # token 映射到“匹配模式 + 处理器”，便于精确取消订阅。
        self._handlers: dict[str, tuple[str, EventHandler]] = {}
        # 注册、取消与复制处理器列表时使用同一把锁。
        self._lock = threading.Lock()

    def subscribe(
        self,
        pattern: str,
        handler: EventHandler,
        *,
        token: str | None = None,
    ) -> Callable[[], None]:
        """注册 glob 主题处理器，并返回幂等取消函数。"""
        subscription_id = token or uuid.uuid4().hex
        with self._lock:
            self._handlers[subscription_id] = (pattern, handler)

        def unsubscribe() -> None:
            # pop(..., None) 让重复取消不会抛错。
            with self._lock:
                self._handlers.pop(subscription_id, None)

        return unsubscribe

    def unsubscribe(self, token: str) -> None:
        """供跨 Run 注册表按共享 token 移除处理器。"""
        with self._lock:
            self._handlers.pop(token, None)

    def dispatch(self, event: Event) -> None:
        """把事件同步发送给所有匹配处理器，并隔离处理器异常。"""
        # 锁内只复制快照，实际处理器在锁外运行，允许处理器继续注册事件。
        with self._lock:
            handlers = list(self._handlers.values())
        # 旧格式事件没有 topics 时仍可按 type 分发。
        topics = event.get("topics") or [event["type"]]
        for pattern, handler in handlers:
            # 一个处理器即使命中多个 topic，也只执行一次。
            if not any(fnmatchcase(topic, pattern) for topic in topics):
                continue
            try:
                handler(event)
            except Exception:
                logger.exception("TradingAgents web event handler failed for %s", pattern)


@dataclass
class RunSession:
    """一次分析任务的请求、状态、事件历史与订阅器。"""

    # 原始请求用于快照恢复和问题诊断。
    request: dict[str, Any]
    # UUID hex 同时作为 API 路径中的 run_id。
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    # 创建时间用于任务列表倒序和历史淘汰。
    created_at: str = field(default_factory=utc_now)
    # 新任务从 queued 开始，由 runner 切换到 running 和终态。
    status: str = "queued"
    # 事件按严格递增 id 追加，SSE 可从任意游标重放。
    events: list[dict[str, Any]] = field(default_factory=list)
    # 成功结果和错误分别服务于页面刷新恢复。
    final_result: dict[str, Any] | None = None
    error: str | None = None
    # 取消标记由 API 设置，由图流循环协作式检查。
    cancel_requested: bool = False
    # Condition 同时保护状态和事件，并唤醒等待中的 SSE 生成器。
    _condition: threading.Condition = field(default_factory=threading.Condition, repr=False)
    # 每个任务拥有独立事件分发器，避免不同任务处理器相互污染。
    _dispatcher: EventDispatcher = field(default_factory=EventDispatcher, repr=False)

    def publish(self, event_type: str, **payload: Any) -> dict[str, Any]:
        """原子追加事件、唤醒 SSE，然后在锁外通知本地订阅器。"""
        with self._condition:
            event = build_event(self.id, len(self.events) + 1, event_type, payload)
            self.events.append(event)
            self._condition.notify_all()
        self._dispatcher.dispatch(event)
        return event

    def subscribe(self, pattern: str, handler: EventHandler) -> Callable[[], None]:
        """注册同步处理器；处理器失败不会影响分析任务。"""
        return self._dispatcher.subscribe(pattern, handler)

    def set_status(self, status: str, *, error: str | None = None) -> None:
        """原子更新任务状态并唤醒可能等待终态的 SSE。"""
        with self._condition:
            self.status = status
            self.error = error
            self._condition.notify_all()

    def snapshot(self) -> dict[str, Any]:
        """返回不暴露锁和处理器的可 JSON 序列化任务快照。"""
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
        """按 SSE 格式重放游标后的事件，并阻塞等待后续事件。"""
        # 防止负游标造成 Python 切片从尾部读取。
        cursor = max(0, after)
        while True:
            with self._condition:
                # 没有新事件且任务未结束时，Condition 释放锁并等待发布通知。
                if cursor >= len(self.events) and self.status not in TERMINAL_STATUSES:
                    self._condition.wait(timeout=heartbeat_seconds)
                pending = self.events[cursor:]
                status = self.status
            # 在锁外做 JSON 编码和网络 yield，避免阻塞发布线程。
            if pending:
                for event in pending:
                    cursor = event["id"]
                    data = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
                    yield f"id: {cursor}\ndata: {data}\n\n"
                continue
            # 事件已经全部发送后，终态任务关闭 SSE 迭代器。
            if status in TERMINAL_STATUSES:
                break
            # 定期注释帧维持连接，但浏览器 onmessage 不会收到它。
            yield ": heartbeat\n\n"


class RunRegistry:
    """保存有界内存历史，并串行化成本较高的分析任务。"""

    def __init__(self, max_runs: int = 30) -> None:
        # max_runs 只限制已完成历史；活动任务始终保留。
        self.max_runs = max_runs
        self._runs: dict[str, RunSession] = {}
        self._handlers: dict[str, tuple[str, EventHandler]] = {}
        self._lock = threading.Lock()

    def create(self, request: dict[str, Any]) -> RunSession:
        """在没有活动任务时创建 session，并继承全局处理器。"""
        with self._lock:
            active = next(
                (run for run in self._runs.values() if run.status not in TERMINAL_STATUSES),
                None,
            )
            # 当前设计明确只允许一个高成本图同时执行。
            if active is not None:
                raise RuntimeError(active.id)
            session = RunSession(request=request)
            # 全局订阅使用相同 token 注入新任务，便于统一取消。
            for token, (pattern, handler) in self._handlers.items():
                session._dispatcher.subscribe(pattern, handler, token=token)
            self._runs[session.id] = session
            # 超出上限时只淘汰最旧的终态任务。
            completed = [
                run for run in self._runs.values() if run.status in TERMINAL_STATUSES
            ]
            for stale in sorted(completed, key=lambda item: item.created_at)[:-self.max_runs]:
                self._runs.pop(stale.id, None)
        # 在 registry 锁外发布，允许处理器安全调用 registry 的查询方法。
        session.publish("run.created", status="queued", request=request)
        return session

    def subscribe(self, pattern: str, handler: EventHandler) -> Callable[[], None]:
        """为当前及此进程未来创建的所有 session 注册处理器。"""
        token = uuid.uuid4().hex
        # 先登记全局模板，再补充到当前任务，避免漏掉并发创建的新任务。
        with self._lock:
            self._handlers[token] = (pattern, handler)
            runs = list(self._runs.values())
        for run in runs:
            run._dispatcher.subscribe(pattern, handler, token=token)

        def unsubscribe() -> None:
            # 同时移除全局模板和所有仍在内存中的 session 注册。
            with self._lock:
                self._handlers.pop(token, None)
                current_runs = list(self._runs.values())
            for run in current_runs:
                run._dispatcher.unsubscribe(token)

        return unsubscribe

    def get(self, run_id: str) -> RunSession | None:
        """按 ID 线程安全地查找任务。"""
        with self._lock:
            return self._runs.get(run_id)

    def list(self) -> list[dict[str, Any]]:
        """返回按创建时间从新到旧排列的任务快照。"""
        with self._lock:
            runs = list(self._runs.values())
        return [run.snapshot() for run in sorted(runs, key=lambda item: item.created_at, reverse=True)]


class LiveTraceCallback(BaseCallbackHandler):
    """发布可观察 LangChain 活动，但不暴露模型隐藏推理。"""

    def __init__(self, session: RunSession) -> None:
        super().__init__()
        self.session = session
        # 相同 callback 可能同时挂在模型和图配置上，用集合按 run_id 去重。
        self._seen_events: set[tuple[str, str]] = set()
        # 工具开始信息用于在结束事件中补回名称并计算耗时。
        self._tool_calls: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _run_id(kwargs: dict[str, Any]) -> str | None:
        """把 LangChain UUID 或字符串 run_id 统一转为字符串。"""
        value = kwargs.get("run_id")
        return str(value) if value is not None else None

    @staticmethod
    def _node(kwargs: dict[str, Any]) -> str | None:
        """从 LangGraph callback metadata 中读取当前节点名。"""
        metadata = kwargs.get("metadata") or {}
        return metadata.get("langgraph_node") or metadata.get("node")

    def _first(self, event_type: str, kwargs: dict[str, Any]) -> bool:
        """去重同时挂载在模型和图配置上的同一生命周期回调。"""
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
        """把 LangGraph 节点开始转换成 node.started。"""
        node = self._node(kwargs)
        run_id = self._run_id(kwargs)
        if not node or not run_id or not self._first("node.started", kwargs):
            return
        self.session.publish("node.started", node=node, run_id=run_id, correlation_id=run_id)

    def on_chain_end(self, outputs: dict[str, Any], **kwargs: Any) -> None:
        """把 LangGraph 节点完成转换成 node.completed。"""
        node = self._node(kwargs)
        run_id = self._run_id(kwargs)
        if node and run_id and self._first("node.completed", kwargs):
            self.session.publish("node.completed", node=node, run_id=run_id, correlation_id=run_id)

    def on_chain_error(self, error: BaseException, **kwargs: Any) -> None:
        """把节点异常转换成包含错误文本的 node.failed。"""
        if not self._first("node.failed", kwargs):
            return
        node = self._node(kwargs)
        self.session.publish(
            "node.failed",
            node=node,
            run_id=self._run_id(kwargs),
            correlation_id=self._run_id(kwargs),
            error=str(error),
        )

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[BaseMessage]],
        **kwargs: Any,
    ) -> None:
        """处理聊天模型输入批次的开始事件。"""
        if not self._first("llm.started", kwargs):
            return
        model = serialized.get("name") or (serialized.get("id") or ["chat-model"])[-1]
        self.session.publish(
            "llm.started",
            model=model,
            node=self._node(kwargs),
            run_id=self._run_id(kwargs),
            correlation_id=self._run_id(kwargs),
            message_count=sum(len(batch) for batch in messages),
        )

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        **kwargs: Any,
    ) -> None:
        """处理传统 prompt LLM 的开始事件。"""
        if not self._first("llm.started", kwargs):
            return
        model = serialized.get("name") or (serialized.get("id") or ["llm"])[-1]
        self.session.publish(
            "llm.started",
            model=model,
            node=self._node(kwargs),
            run_id=self._run_id(kwargs),
            correlation_id=self._run_id(kwargs),
            message_count=len(prompts),
        )

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        """提取首个 generation 的公开文本与 token 用量。"""
        if not self._first("llm.completed", kwargs):
            return
        content = ""
        usage: dict[str, Any] = {}
        # 不同 provider 的响应结构略有差异，缺字段时保持空结果而不破坏任务。
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
            correlation_id=self._run_id(kwargs),
            output=content,
            usage=usage,
        )

    def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        """发布模型调用失败事件。"""
        if not self._first("llm.failed", kwargs):
            return
        self.session.publish(
            "llm.failed",
            node=self._node(kwargs),
            run_id=self._run_id(kwargs),
            correlation_id=self._run_id(kwargs),
            error=str(error),
        )

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        **kwargs: Any,
    ) -> None:
        """记录工具名称与开始时刻，并发布可关联的 tool.started。"""
        if not self._first("tool.started", kwargs):
            return
        run_id = self._run_id(kwargs)
        tool = serialized.get("name") or "tool"
        if run_id:
            with self._lock:
                self._tool_calls[run_id] = (tool, time.monotonic())
        self.session.publish(
            "tool.started",
            tool=tool,
            node=self._node(kwargs),
            run_id=run_id,
            correlation_id=run_id,
            tool_call_id=run_id,
            input=json_safe(kwargs.get("inputs") or input_str),
        )

    def on_tool_end(self, output: Any, **kwargs: Any) -> None:
        """关联工具开始记录，补充耗时并发布 tool.completed。"""
        if not self._first("tool.completed", kwargs):
            return
        run_id = self._run_id(kwargs)
        tool = None
        duration_ms = None
        if run_id:
            with self._lock:
                started = self._tool_calls.pop(run_id, None)
            if started:
                tool, started_at = started
                duration_ms = elapsed_ms(started_at)
        self.session.publish(
            "tool.completed",
            tool=tool,
            node=self._node(kwargs),
            run_id=run_id,
            correlation_id=run_id,
            tool_call_id=run_id,
            duration_ms=duration_ms,
            is_error=False,
            output=json_safe(output),
        )

    def on_tool_error(self, error: BaseException, **kwargs: Any) -> None:
        """关联工具开始记录，并以 is_error=true 发布 tool.failed。"""
        if not self._first("tool.failed", kwargs):
            return
        run_id = self._run_id(kwargs)
        tool = None
        duration_ms = None
        if run_id:
            with self._lock:
                started = self._tool_calls.pop(run_id, None)
            if started:
                tool, started_at = started
                duration_ms = elapsed_ms(started_at)
        self.session.publish(
            "tool.failed",
            tool=tool,
            node=self._node(kwargs),
            run_id=run_id,
            correlation_id=run_id,
            tool_call_id=run_id,
            duration_ms=duration_ms,
            is_error=True,
            error=str(error),
        )


def elapsed_ms(started_at: float) -> int:
    """把单调时钟差转换成整数毫秒。"""
    return round((time.monotonic() - started_at) * 1000)

"""执行既有 TradingAgents 图，并把运行过程转换成浏览器可订阅的统一事件。"""

from __future__ import annotations

import time
from typing import Any

from tradingagents.agents.rating import run_rating
from tradingagents.default_config import build_default_config
from tradingagents.graph.trading_graph import TradingAgentsGraph

from .events import (
    LiveTraceCallback,
    RunSession,
    content_text,
    describe_agent,
    elapsed_ms,
    json_safe,
)


# LangGraph 状态字段到负责 Agent 和中文报告标题的映射。
REPORTS = {
    "market_report": ("Market Analyst", "市场技术分析"),
    "sentiment_report": ("Sentiment Analyst", "市场情绪分析"),
    "news_report": ("News Analyst", "新闻与宏观分析"),
    "fundamentals_report": ("Fundamentals Analyst", "基本面分析"),
    "investment_plan": ("Research Manager", "多空研究结论"),
    "trader_investment_plan": ("Trader", "交易计划"),
    "final_trade_decision": ("Portfolio Manager", "最终投资决策"),
}

# Web 表单中的分析师 key 到图节点显示名的映射。
ANALYST_NODES = {
    "market": "Market Analyst",
    "social": "Sentiment Analyst",
    "news": "News Analyst",
    "fundamentals": "Fundamentals Analyst",
}

# 不随用户勾选变化、每次分析都会参与的固定节点。
FIXED_NODES = [
    "Memory Log",
    "Bull Researcher",
    "Bear Researcher",
    "Research Manager",
    "Trader",
    "Aggressive Analyst",
    "Neutral Analyst",
    "Conservative Analyst",
    "Portfolio Manager",
]


class RunCancelled(Exception):
    """用于从图流循环安全跳转到取消收尾逻辑的内部异常。"""

    pass


def _message_event(message: Any) -> dict[str, Any]:
    """从 LangChain 消息中提取可公开展示的字段并转成事件载荷。"""
    # tool_calls 可能不存在或为 None，统一归一化为空数组。
    tool_calls = getattr(message, "tool_calls", None) or []
    # name 通常标识发出消息的 Agent，可用于构造 Agent 订阅主题。
    name = getattr(message, "name", None)
    return {
        "message_id": getattr(message, "id", None),
        "role": getattr(message, "type", type(message).__name__),
        "name": name,
        "agent_name": name,
        "content": content_text(getattr(message, "content", "")),
        "tool_calls": json_safe(tool_calls),
    }


def _stats(events: list[dict[str, Any]]) -> dict[str, int]:
    """从已发布事件计算最终结果卡片使用的调用和节点统计。"""
    return {
        "llm_calls": sum(event["type"] == "llm.started" for event in events),
        "tool_calls": sum(event["type"] == "tool.started" for event in events),
        "completed_nodes": sum(event["type"] == "node.completed" for event in events),
    }


def execute_run(session: RunSession) -> None:
    """在线程中运行一次完整分析，并保证所有成功、失败、取消路径都有终态事件。"""
    # 使用单调时钟统计耗时，避免系统时间调整造成负数或跳变。
    started_at = time.monotonic()
    # 请求已经过 FastAPI 校验和 provider/model 补全。
    request = session.request
    # graph 先设为空，finally 中只清理已经成功创建的实例。
    graph: TradingAgentsGraph | None = None
    # 流式 chunk 会逐步合并到最终状态。
    final_state: dict[str, Any] = {}
    # LangGraph 可能重复返回历史消息，集合用于防止重复发布。
    processed_messages: set[Any] = set()
    # 同一报告内容可能出现在多个 chunk 中，用内容快照去重。
    report_versions: dict[str, str] = {}

    try:
        # 先切换状态，令快照接口立即显示任务已运行。
        session.set_status("running")
        # 以项目默认配置为基础，只覆盖本次 Web 请求控制的字段。
        config = build_default_config()
        config.update(
            {
                "llm_provider": request["provider"],
                "quick_think_provider": None,
                "deep_think_provider": None,
                "quick_think_llm": request["quick_model"],
                "deep_think_llm": request["deep_model"],
                "backend_url": None,
                "quick_think_backend_url": None,
                "deep_think_backend_url": None,
                "max_debate_rounds": request["research_depth"],
                "max_risk_discuss_rounds": request["research_depth"],
                "output_language": request["output_language"],
            }
        )
        # Callback 把 LangChain 的节点、模型、工具生命周期转成统一 Web 事件。
        callback = LiveTraceCallback(session)
        # 复用项目原有图，不在 Web 层复制交易分析业务逻辑。
        graph = TradingAgentsGraph(
            request["analysts"],
            config=config,
            debug=False,
            callbacks=[callback],
        )
        # 页面用完整 Agent 清单初始化各阶段的 pending 状态。
        agent_nodes = [ANALYST_NODES[key] for key in request["analysts"]] + FIXED_NODES
        # run.started 是前端开始渲染决策流水线的边界事件。
        session.publish(
            "run.started",
            status="running",
            agents=agent_nodes,
            agent_catalog=[describe_agent(agent) for agent in agent_nodes],
            settings=graph.run_settings(),
        )

        # 将标的、日期、资产类型转换成 LangGraph 初始 state。
        initial_state = graph.create_run_state(
            request["ticker"],
            request["trade_date"],
            request["asset_type"],
        )
        # 把同一 Callback 注入实际 stream 调用，确保子 runnable 事件也可见。
        args = graph.propagator.get_graph_args(callbacks=[callback])
        # 创建或查找检查点，以支持项目原有的恢复机制。
        checkpoint_id = graph.begin_checkpoint(
            request["ticker"], request["trade_date"], request["asset_type"]
        )
        if checkpoint_id is not None:
            # LangGraph 通过 configurable.thread_id 关联检查点。
            args.setdefault("config", {}).setdefault("configurable", {})[
                "thread_id"
            ] = checkpoint_id
            session.publish("checkpoint.ready", resumed=bool(getattr(graph, "_resuming", False)))

        # 若存在可恢复检查点，图会在这里决定采用初始状态还是恢复输入。
        graph_input = graph.checkpoint_input(initial_state)
        # stream_run 每次产出新增消息列表与状态增量 chunk。
        for messages, chunk in graph.stream_run(graph_input, **args):
            # 取消是协作式的，只能在当前同步模型/工具调用返回后检查。
            if session.cancel_requested:
                raise RunCancelled("Run cancelled by user")

            for message in messages:
                # 优先使用 LangChain message id；无 id 时使用类型和内容构造稳定标识。
                identity = getattr(message, "id", None) or (
                    type(message).__name__,
                    content_text(getattr(message, "content", "")),
                )
                if identity in processed_messages:
                    continue
                # 先登记再发布，避免后续 chunk 重复输出同一消息。
                processed_messages.add(identity)
                event = _message_event(message)
                # 空消息不增加页面噪声，但带 tool_calls 的消息仍需要展示。
                if event["content"] or event["tool_calls"]:
                    session.publish("message.created", **event)

            # 部分 stream 步骤只携带消息而没有状态增量。
            if not chunk:
                continue
            # 累积状态用于最终决策、评分、持久化与结果汇总。
            final_state.update(chunk)
            # messages 已通过专门事件发布，不在 state.updated 中重复传输。
            changed_keys = [key for key in chunk if key != "messages"]
            session.publish(
                "state.updated",
                keys=changed_keys,
                elapsed_ms=elapsed_ms(started_at),
            )

            for key, (agent, title) in REPORTS.items():
                # 每个报告字段出现后立即发布，而不必等待完整图结束。
                value = chunk.get(key)
                # 非字符串、空白字符串和未生成字段都不是有效报告。
                if not isinstance(value, str) or not value.strip():
                    continue
                # 内容未变化时不重复触发 React Markdown 渲染。
                if report_versions.get(key) == value:
                    continue
                report_versions[key] = value
                session.publish(
                    "report.updated",
                    report_key=key,
                    title=title,
                    agent_name=agent,
                    content=value,
                )

        # 最终决策是一次成功分析的必要产物。
        if not final_state.get("final_trade_decision"):
            raise RuntimeError("The graph finished without a final trade decision")

        # 成功后写入项目原有的决策记忆，并清除已消费检查点。
        graph.record_decision(request["ticker"], request["trade_date"], final_state)
        graph.clear_checkpoint_on_success(
            request["ticker"], request["trade_date"], request["asset_type"]
        )
        # 组装页面结果区所需的稳定、紧凑响应结构。
        result = {
            "ticker": request["ticker"],
            "trade_date": request["trade_date"],
            "rating": str(run_rating(final_state)),
            "final_trade_decision": final_state["final_trade_decision"],
            "reports": {
                key: final_state.get(key, "") for key in REPORTS if final_state.get(key)
            },
            "elapsed_ms": elapsed_ms(started_at),
            "stats": _stats(session.events),
        }
        # 先保存快照结果，再发布完成事件，保证刷新页面也能恢复结果。
        session.final_result = result
        session.publish("run.completed", status="completed", result=result)
        session.set_status("completed")
    except RunCancelled as exc:
        # 用户取消是预期终态，不当作系统错误处理。
        session.publish(
            "run.cancelled",
            status="cancelled",
            reason=str(exc),
            elapsed_ms=elapsed_ms(started_at),
        )
        session.set_status("cancelled")
    except Exception as exc:
        # 任何未处理异常都转换成 run.failed，避免 SSE 无终态悬挂。
        message = str(exc) or type(exc).__name__
        session.publish(
            "run.failed",
            status="failed",
            error=message,
            error_type=type(exc).__name__,
            elapsed_ms=elapsed_ms(started_at),
        )
        session.set_status("failed", error=message)
    finally:
        # 无论成功、取消还是失败，都释放图持有的检查点上下文。
        if graph is not None:
            graph.end_checkpoint()

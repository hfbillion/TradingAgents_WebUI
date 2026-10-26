"""Execute the existing TradingAgents graph and translate it into UI events."""

from __future__ import annotations

import time
from typing import Any

from tradingagents.agents.rating import run_rating
from tradingagents.default_config import build_default_config
from tradingagents.graph.trading_graph import TradingAgentsGraph

from .events import LiveTraceCallback, RunSession, content_text, elapsed_ms, json_safe


REPORTS = {
    "market_report": ("Market Analyst", "市场技术分析"),
    "sentiment_report": ("Sentiment Analyst", "市场情绪分析"),
    "news_report": ("News Analyst", "新闻与宏观分析"),
    "fundamentals_report": ("Fundamentals Analyst", "基本面分析"),
    "investment_plan": ("Research Manager", "多空研究结论"),
    "trader_investment_plan": ("Trader", "交易计划"),
    "final_trade_decision": ("Portfolio Manager", "最终投资决策"),
}

ANALYST_NODES = {
    "market": "Market Analyst",
    "social": "Sentiment Analyst",
    "news": "News Analyst",
    "fundamentals": "Fundamentals Analyst",
}

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
    pass


def _message_event(message: Any) -> dict[str, Any]:
    tool_calls = getattr(message, "tool_calls", None) or []
    return {
        "message_id": getattr(message, "id", None),
        "role": getattr(message, "type", type(message).__name__),
        "name": getattr(message, "name", None),
        "content": content_text(getattr(message, "content", "")),
        "tool_calls": json_safe(tool_calls),
    }


def _stats(events: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "llm_calls": sum(event["type"] == "llm.started" for event in events),
        "tool_calls": sum(event["type"] == "tool.started" for event in events),
        "completed_nodes": sum(event["type"] == "node.completed" for event in events),
    }


def execute_run(session: RunSession) -> None:
    started_at = time.monotonic()
    request = session.request
    graph: TradingAgentsGraph | None = None
    final_state: dict[str, Any] = {}
    processed_messages: set[Any] = set()
    report_versions: dict[str, str] = {}

    try:
        session.set_status("running")
        config = build_default_config()
        config.update(
            {
                "max_debate_rounds": request["research_depth"],
                "max_risk_discuss_rounds": request["research_depth"],
                "output_language": request["output_language"],
            }
        )
        callback = LiveTraceCallback(session)
        graph = TradingAgentsGraph(
            request["analysts"],
            config=config,
            debug=False,
            callbacks=[callback],
        )
        agent_nodes = [ANALYST_NODES[key] for key in request["analysts"]] + FIXED_NODES
        session.publish(
            "run.started",
            status="running",
            agents=agent_nodes,
            settings=graph.run_settings(),
        )

        initial_state = graph.create_run_state(
            request["ticker"],
            request["trade_date"],
            request["asset_type"],
        )
        args = graph.propagator.get_graph_args(callbacks=[callback])
        checkpoint_id = graph.begin_checkpoint(
            request["ticker"], request["trade_date"], request["asset_type"]
        )
        if checkpoint_id is not None:
            args.setdefault("config", {}).setdefault("configurable", {})[
                "thread_id"
            ] = checkpoint_id
            session.publish("checkpoint.ready", resumed=bool(getattr(graph, "_resuming", False)))

        graph_input = graph.checkpoint_input(initial_state)
        for messages, chunk in graph.stream_run(graph_input, **args):
            if session.cancel_requested:
                raise RunCancelled("Run cancelled by user")

            for message in messages:
                identity = getattr(message, "id", None) or (
                    type(message).__name__,
                    content_text(getattr(message, "content", "")),
                )
                if identity in processed_messages:
                    continue
                processed_messages.add(identity)
                event = _message_event(message)
                if event["content"] or event["tool_calls"]:
                    session.publish("message.created", **event)

            if not chunk:
                continue
            final_state.update(chunk)
            changed_keys = [key for key in chunk if key != "messages"]
            session.publish(
                "state.updated",
                keys=changed_keys,
                elapsed_ms=elapsed_ms(started_at),
            )

            for key, (agent, title) in REPORTS.items():
                value = chunk.get(key)
                if not isinstance(value, str) or not value.strip():
                    continue
                if report_versions.get(key) == value:
                    continue
                report_versions[key] = value
                session.publish(
                    "report.updated",
                    report_key=key,
                    title=title,
                    agent=agent,
                    content=value,
                )

        if not final_state.get("final_trade_decision"):
            raise RuntimeError("The graph finished without a final trade decision")

        graph.record_decision(request["ticker"], request["trade_date"], final_state)
        graph.clear_checkpoint_on_success(
            request["ticker"], request["trade_date"], request["asset_type"]
        )
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
        session.final_result = result
        session.publish("run.completed", status="completed", result=result)
        session.set_status("completed")
    except RunCancelled as exc:
        session.publish(
            "run.cancelled",
            status="cancelled",
            reason=str(exc),
            elapsed_ms=elapsed_ms(started_at),
        )
        session.set_status("cancelled")
    except Exception as exc:
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
        if graph is not None:
            graph.end_checkpoint()

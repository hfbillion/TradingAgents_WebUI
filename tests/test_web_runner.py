from types import SimpleNamespace

from langchain_core.messages import AIMessage

from tradingagents.web import runner
from tradingagents.web.events import RunSession


class FakeGraph:
    latest_config = None

    def __init__(self, analysts, config, debug, callbacks):
        self.analysts = analysts
        self.config = config
        self.callbacks = callbacks
        FakeGraph.latest_config = config
        self.propagator = SimpleNamespace(get_graph_args=lambda callbacks: {})
        self.recorded = False
        self.ended = False

    def run_settings(self):
        return {"analysts": self.analysts}

    def create_run_state(self, ticker, trade_date, asset_type):
        return {"ticker": ticker, "trade_date": trade_date, "asset_type": asset_type}

    def begin_checkpoint(self, ticker, trade_date, asset_type):
        return None

    def checkpoint_input(self, state):
        return state

    def stream_run(self, graph_input, **args):
        yield [AIMessage(id="one", content="market complete")], {
            "market_report": "# Market\nStrong momentum",
        }
        yield [], {
            "final_trade_decision": "BUY with controlled position size",
        }

    def record_decision(self, ticker, trade_date, state):
        self.recorded = True

    def clear_checkpoint_on_success(self, ticker, trade_date, asset_type):
        return None

    def end_checkpoint(self):
        self.ended = True


def test_execute_run_publishes_reports_messages_and_final_result(monkeypatch):
    monkeypatch.setattr(runner, "TradingAgentsGraph", FakeGraph)
    monkeypatch.setattr(runner, "run_rating", lambda state: "BUY")
    session = RunSession(
        request={
            "ticker": "NVDA",
            "trade_date": "2026-10-20",
            "asset_type": "stock",
            "analysts": ["market"],
            "research_depth": 1,
            "output_language": "Chinese",
            "provider": "deepseek",
            "quick_model": "deepseek-flash",
            "deep_model": "deepseek-v4-pro",
        }
    )

    runner.execute_run(session)

    assert session.status == "completed"
    assert session.final_result["rating"] == "BUY"
    assert session.final_result["reports"]["market_report"].startswith("# Market")
    assert FakeGraph.latest_config["llm_provider"] == "deepseek"
    assert FakeGraph.latest_config["quick_think_llm"] == "deepseek-flash"
    assert FakeGraph.latest_config["deep_think_llm"] == "deepseek-v4-pro"
    event_types = [event["type"] for event in session.events]
    assert "message.created" in event_types
    assert "report.updated" in event_types
    assert event_types[-1] == "run.completed"

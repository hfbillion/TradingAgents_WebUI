from types import SimpleNamespace

from langchain_core.messages import AIMessage

from tradingagents.web import runner
from tradingagents.web.events import RunSession


class FakeGraph:
    def __init__(self, analysts, config, debug, callbacks):
        self.analysts = analysts
        self.config = config
        self.callbacks = callbacks
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
        }
    )

    runner.execute_run(session)

    assert session.status == "completed"
    assert session.final_result["rating"] == "BUY"
    assert session.final_result["reports"]["market_report"].startswith("# Market")
    event_types = [event["type"] for event in session.events]
    assert "message.created" in event_types
    assert "report.updated" in event_types
    assert event_types[-1] == "run.completed"

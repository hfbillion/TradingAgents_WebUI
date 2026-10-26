from tradingagents.web.events import LiveTraceCallback, RunRegistry, RunSession, json_safe


def test_session_replays_events_and_closes_after_terminal_status():
    session = RunSession(request={"ticker": "NVDA"})
    first = session.publish("run.started", status="running")
    session.publish("report.updated", content="hello")
    session.set_status("completed")

    chunks = list(session.iter_events(after=first["id"]))

    assert len(chunks) == 1
    assert '"type":"report.updated"' in chunks[0]
    assert chunks[0].startswith("id: 2\n")


def test_registry_allows_only_one_active_run():
    registry = RunRegistry()
    first = registry.create({"ticker": "NVDA"})

    try:
        registry.create({"ticker": "AAPL"})
    except RuntimeError as exc:
        assert str(exc) == first.id
    else:
        raise AssertionError("expected an active-run conflict")

    first.set_status("completed")
    second = registry.create({"ticker": "AAPL"})
    assert second.id != first.id


def test_json_safe_bounds_large_values():
    result = json_safe({"body": "x" * 20_000}, limit=500)

    assert isinstance(result, dict)
    assert len(result["body"]) < 600
    assert "omitted" in result["body"]


def test_callback_deduplicates_same_langchain_run_id():
    session = RunSession(request={"ticker": "NVDA"})
    callback = LiveTraceCallback(session)
    kwargs = {"run_id": "same-run", "metadata": {"langgraph_node": "Trader"}}

    callback.on_tool_start({"name": "prices"}, "NVDA", **kwargs)
    callback.on_tool_start({"name": "prices"}, "NVDA", **kwargs)

    assert [event["type"] for event in session.events] == ["tool.started"]

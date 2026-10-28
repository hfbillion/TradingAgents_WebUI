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


def test_event_envelope_classifies_agent_role_and_side():
    session = RunSession(request={"ticker": "NVDA"})

    event = session.publish("node.started", node="Bull Researcher", run_id="node-1")

    assert event["schema_version"] == 1
    assert event["session_id"] == session.id
    assert event["family"] == "node"
    assert event["phase"] == "start"
    assert event["correlation_id"] == "node-1"
    assert event["agent"] == {
        "name": "Bull Researcher",
        "id": "bull_researcher",
        "role": "researcher",
        "side": "bull",
    }
    assert "agent.started" in event["topics"]
    assert "agent.side.bull.node.started" in event["topics"]


def test_session_subscribers_support_topic_patterns_and_unsubscribe():
    session = RunSession(request={"ticker": "NVDA"})
    analyst_events = []
    bull_events = []
    stop_analysts = session.subscribe("agent.role.analyst.*", analyst_events.append)
    session.subscribe("agent.side.bull.*", bull_events.append)

    session.publish("node.started", node="Market Analyst")
    session.publish("node.started", node="Bull Researcher")
    stop_analysts()
    session.publish("node.completed", node="Market Analyst")

    assert [event["type"] for event in analyst_events] == ["node.started"]
    assert [event["type"] for event in bull_events] == ["node.started"]


def test_registry_subscriber_receives_events_from_future_sessions():
    registry = RunRegistry()
    events = []
    unsubscribe = registry.subscribe("run.*", events.append)

    registry.create({"ticker": "NVDA"})
    unsubscribe()

    assert [event["type"] for event in events] == ["run.created"]


def test_tool_lifecycle_has_stable_correlation_fields():
    session = RunSession(request={"ticker": "NVDA"})
    callback = LiveTraceCallback(session)
    kwargs = {"run_id": "tool-1", "metadata": {"langgraph_node": "Market Analyst"}}

    callback.on_tool_start({"name": "get_prices"}, "NVDA", **kwargs)
    callback.on_tool_end({"close": 100}, **kwargs)

    started, completed = session.events
    assert started["tool_call_id"] == completed["tool_call_id"] == "tool-1"
    assert completed["tool"] == "get_prices"
    assert completed["is_error"] is False
    assert completed["duration_ms"] >= 0

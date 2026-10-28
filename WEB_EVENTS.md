# Web event protocol

For the reasoning behind the transport, envelope, topic model, and frontend
state reduction, see [WEB_DESIGN.md](WEB_DESIGN.md).

The Web API exposes one versioned event stream for run, agent, model, tool,
message, report, state, and checkpoint activity. It follows the same basic
lifecycle and subscription ideas as pi-agent: events are discriminated by
`type`, correlated lifecycle events share an ID, and subscribing returns an
unsubscribe function.

## Event envelope

Every SSE message from `GET /api/runs/{id}/events` contains:

```json
{
  "schema_version": 1,
  "id": 12,
  "type": "tool.started",
  "family": "tool",
  "phase": "start",
  "timestamp": "2026-10-27T10:00:00+00:00",
  "session_id": "run-id",
  "correlation_id": "langchain-run-id",
  "topics": ["tool.started", "agent.activity", "agent.role.analyst.tool.started"],
  "agent": {
    "id": "market_analyst",
    "name": "Market Analyst",
    "role": "analyst"
  },
  "tool": "get_stock_data",
  "tool_call_id": "langchain-run-id",
  "input": {"symbol": "NVDA"}
}
```

Existing flat payload fields remain available. Consumers should switch on
`type`; `family`, `phase`, and `topics` are routing metadata.

## Useful subscriptions

| Pattern | Receives |
| --- | --- |
| `*` | every event |
| `run.*` | complete run lifecycle |
| `agent.*` | activity associated with any agent |
| `agent.started` | any LangGraph agent/node start |
| `agent.role.analyst.*` | market, sentiment, news, and fundamentals analysts |
| `agent.side.bull.*` | bull researcher activity |
| `agent.side.bear.*` | bear researcher activity |
| `agent.trader.*` | trader activity |
| `tool.started` | tool invocation start |
| `tool.*` | complete tool lifecycle |
| `llm.*` | model lifecycle |
| `report.updated` | incremental report updates |

## Browser registration

```ts
import { tradingEvents } from './events'

const stopBull = tradingEvents.subscribe('agent.side.bull.*', (event) => {
  console.log('bull activity', event)
})

const stopTools = tradingEvents.subscribe('tool.started', (event) => {
  console.log(event.tool_call_id, event.tool, event.input)
})

source.onmessage = (message) => tradingEvents.dispatch(JSON.parse(message.data))

// Component cleanup
stopBull()
stopTools()
```

`TradingEventBus.once(pattern, handler)` handles only the first matching event.
An overload of `subscribe(handler)` receives all events.

## Server registration

Register against one run:

```py
stop = session.subscribe("agent.role.analyst.*", handle_analyst_event)
```

Register against all current and future runs in the process:

```py
stop = registry.subscribe("tool.started", audit_tool_call)
```

Handlers run synchronously in the analysis worker thread. Handler exceptions are
logged and isolated from the run. Keep handlers fast; queue expensive work in
the handler if needed. Calling the returned function unregisters the handler.

## Tool correlation

`tool.started`, `tool.completed`, and `tool.failed` share `tool_call_id` and
`correlation_id`. Completion events also include `tool`, `duration_ms`, and
`is_error`, allowing a UI or audit handler to join the full tool lifecycle.

Only observable framework messages and outputs are published. Hidden model
reasoning is not included.

# TradingAgents Web UI

The web workspace adds a local React + Ant Design dashboard and a FastAPI service
without changing the CLI execution path. The API runs the existing
`TradingAgentsGraph.stream_run()` method and publishes observable LangChain events
over Server-Sent Events (SSE).

The stream includes:

- LangGraph node start, completion, and failure events
- LLM start, completion, token metadata, and visible output
- tool name, input, result, and failure events
- agent messages and tool-call requests
- report updates and the final portfolio decision

Hidden model reasoning is not exposed. The UI renders agent-visible messages and
outputs that the framework already produces.

See [WEB_EVENTS.md](WEB_EVENTS.md) for the versioned event envelope, topic
patterns, and browser/server subscription examples.

See [WEB_DESIGN.md](WEB_DESIGN.md) for the architecture, design rationale,
runtime sequence, security boundaries, trade-offs, and extension strategy.

## Install

From the repository root:

```bash
uv sync --extra web
cd web
npm install
npm run build
```

Keep the existing provider keys and `TRADINGAGENTS_*` settings in `.env`. The Web
UI lets you choose from providers whose credentials are available and selects the
first configured provider by default. For example, when only
`DEEPSEEK_API_KEY` is set, new analyses default to DeepSeek rather than OpenAI.
The API sends only provider names, model IDs, and credential status to the
browser; credentials and backend URLs never leave the server.

## Run the built application

```bash
uv run tradingagents-web
```

Open <http://127.0.0.1:8000>. FastAPI serves the compiled frontend from
`web/dist`.

## Frontend development

Run these in separate terminals:

```bash
uv run uvicorn tradingagents.web.app:app --reload --host 127.0.0.1 --port 8000
```

```bash
cd web
npm run dev
```

Vite proxies `/api` to port 8000. Set `VITE_API_BASE` only when the API is on a
different origin. Configure additional browser origins with the comma-separated
`TRADINGAGENTS_WEB_ORIGINS` environment variable.

## API overview

- `GET /api/meta` returns non-secret runtime/model metadata.
- `POST /api/runs` starts one analysis.
- `GET /api/runs/{id}/events` streams its ordered SSE event history and live events.
- `GET /api/runs/{id}` returns the current snapshot.
- `POST /api/runs/{id}/cancel` requests cancellation after the active model/tool call.

Only one analysis runs at a time to avoid mixing process-wide provider settings
and to make API cost explicit. A second request receives HTTP 409 with the active
run ID so the browser can reconnect to it.

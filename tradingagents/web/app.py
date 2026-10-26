"""FastAPI application for the TradingAgents live research desk."""

from __future__ import annotations

import os
import threading
from datetime import date
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator

from tradingagents import __version__
from tradingagents.default_config import build_default_config

from .events import RunRegistry, TERMINAL_STATUSES
from .runner import execute_run


ROOT_DIR = Path(__file__).resolve().parents[2]
WEB_DIST = ROOT_DIR / "web" / "dist"
ANALYSTS = ("market", "social", "news", "fundamentals")

app = FastAPI(
    title="TradingAgents Live Research API",
    version=__version__,
    description="Streams observable LangChain and LangGraph activity to the TradingAgents web UI.",
)
registry = RunRegistry()

origins = os.getenv(
    "TRADINGAGENTS_WEB_ORIGINS",
    "http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:4173,http://localhost:4173",
).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in origins if origin.strip()],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


class CreateRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticker: str = Field(min_length=1, max_length=24, pattern=r"^[A-Za-z0-9.=_^\-]+$")
    trade_date: date
    asset_type: Literal["stock", "crypto"] = "stock"
    analysts: list[Literal["market", "social", "news", "fundamentals"]] = Field(
        default_factory=lambda: list(ANALYSTS), min_length=1
    )
    research_depth: int = Field(default=1, ge=1, le=5)
    output_language: Literal["English", "Chinese"] = "Chinese"

    @field_validator("ticker", mode="before")
    @classmethod
    def normalize_ticker(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("trade_date")
    @classmethod
    def reject_future_date(cls, value: date) -> date:
        if value > date.today():
            raise ValueError("trade_date cannot be in the future")
        return value

    @field_validator("analysts")
    @classmethod
    def unique_analysts(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.get("/api/meta")
def meta() -> dict:
    config = build_default_config()
    return {
        "version": __version__,
        "analysts": list(ANALYSTS),
        "provider": config.get("llm_provider"),
        "quick_model": config.get("quick_think_llm"),
        "deep_model": config.get("deep_think_llm"),
        "output_language": config.get("output_language"),
    }


@app.get("/api/runs")
def list_runs() -> list[dict]:
    return registry.list()


@app.post("/api/runs", status_code=202)
def create_run(payload: CreateRunRequest) -> dict:
    request = payload.model_dump(mode="json")
    try:
        session = registry.create(request)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "An analysis is already running",
                "active_run_id": str(exc),
            },
        ) from exc
    worker = threading.Thread(
        target=execute_run,
        args=(session,),
        name=f"tradingagents-web-{session.id[:8]}",
        daemon=True,
    )
    worker.start()
    return session.snapshot()


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    session = registry.get(run_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return session.snapshot()


@app.post("/api/runs/{run_id}/cancel", status_code=202)
def cancel_run(run_id: str) -> dict:
    session = registry.get(run_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if session.status in TERMINAL_STATUSES:
        return session.snapshot()
    session.cancel_requested = True
    session.publish("run.cancellation_requested", status=session.status)
    return session.snapshot()


@app.get("/api/runs/{run_id}/events")
def run_events(
    run_id: str,
    after: int = 0,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    session = registry.get(run_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if last_event_id and last_event_id.isdigit():
        after = max(after, int(last_event_id))
    return StreamingResponse(
        session.iter_events(after=after),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


if (WEB_DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="web-assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str, request: Request):
    if path.startswith("api/"):
        raise HTTPException(status_code=404, detail="Not found")
    target = WEB_DIST / path
    if path and target.is_file() and WEB_DIST in target.resolve().parents:
        return FileResponse(target)
    index = WEB_DIST / "index.html"
    if index.is_file():
        return FileResponse(index)
    raise HTTPException(
        status_code=404,
        detail="Web UI is not built. Run `cd web && npm install && npm run build`.",
    )


def main() -> None:
    import uvicorn

    uvicorn.run(
        "tradingagents.web.app:app",
        host=os.getenv("TRADINGAGENTS_WEB_HOST", "127.0.0.1"),
        port=int(os.getenv("TRADINGAGENTS_WEB_PORT", "8000")),
        reload=False,
    )


if __name__ == "__main__":
    main()

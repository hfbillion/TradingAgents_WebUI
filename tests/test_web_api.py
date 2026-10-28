from datetime import date, timedelta

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient
from fastapi import HTTPException
from pydantic import ValidationError

from tradingagents.web.app import CreateRunRequest, _resolve_run_request, _runtime_meta, app


def test_health_endpoint():
    response = TestClient(app).get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_create_run_request_normalizes_ticker():
    payload = CreateRunRequest(
        ticker=" nvda ",
        trade_date=date.today(),
        analysts=["market", "market", "news"],
    )

    assert payload.ticker == "NVDA"
    assert payload.analysts == ["market", "news"]


def test_create_run_request_rejects_future_date():
    with pytest.raises(ValidationError):
        CreateRunRequest(
            ticker="NVDA",
            trade_date=date.today() + timedelta(days=1),
            analysts=["market"],
        )


def test_runtime_meta_prefers_a_configured_provider(monkeypatch):
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "XAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")

    runtime = _runtime_meta()

    assert runtime["default_provider"] == "deepseek"
    assert runtime["default_quick_model"] == "deepseek-flash"
    assert runtime["default_deep_model"] == "deepseek-v4-pro"
    assert all("key" not in item for item in runtime["providers"])


def test_run_request_rejects_provider_without_credential(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    payload = CreateRunRequest(
        ticker="NVDA",
        trade_date=date.today(),
        analysts=["market"],
        provider="openai",
        quick_model="gpt-6-luna",
        deep_model="gpt-6-sol",
    )

    with pytest.raises(HTTPException) as error:
        _resolve_run_request(payload)

    assert error.value.status_code == 422
    assert error.value.detail["code"] == "provider_not_configured"
    assert error.value.detail["env_var"] == "OPENAI_API_KEY"

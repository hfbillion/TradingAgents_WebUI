from datetime import date, timedelta

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient
from pydantic import ValidationError

from tradingagents.web.app import CreateRunRequest, app


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

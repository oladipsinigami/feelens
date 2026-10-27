"""Deployment wiring: health, and the two things that silently rot.

A health check that returns `{"ok": true}` is decoration. The failures that
actually matter on a deployed FeeLens are quiet ones — a rate snapshot that has
gone stale, or a missing model key that turns every "AI explanation" into a
template without anyone noticing. Both are reported here, so the answer to
"is this demo trustworthy right now?" is a request, not an assumption.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.agent import ExplanationAgent, FeeTools
from app.engine import load_fees, load_rates
from app.main import create_app

FIXTURES = Path(__file__).parent / "fixtures"


def _client(with_model: bool = False):
    fees = load_fees(FIXTURES / "fees.json")
    snapshot = load_rates(FIXTURES / "fx_snapshot.json")
    tools = FeeTools(fees, snapshot)
    agent = ExplanationAgent(tools, None)

    if with_model:
        class Ready:
            available = True
            models = ["fake/one"]

            def chat(self, messages, tools=None):
                raise AssertionError("health must never call the model")

        agent = ExplanationAgent(tools, Ready())

    return TestClient(
        create_app(FIXTURES / "fees.json", FIXTURES / "fx_snapshot.json", agent=agent)
    )


def test_health_reports_the_snapshot_date_and_its_age():
    body = _client().get("/api/health").json()

    assert body["status"] == "ok"
    assert body["fx_snapshot_date"] == "2026-10-02"
    assert isinstance(body["fx_age_days"], int)
    assert body["tier"] == "modeled"


def test_health_flags_the_frozen_fixture_as_stale():
    """The fixture is deliberately old; if this ever passes silently, the flag is broken."""
    body = _client().get("/api/health").json()

    assert body["fx_stale"] is True


def test_health_says_when_no_model_is_configured():
    assert _client(with_model=False).get("/api/health").json()["model_configured"] is False


def test_health_says_when_a_model_is_configured():
    assert _client(with_model=True).get("/api/health").json()["model_configured"] is True


def test_health_does_not_leak_the_key_or_the_fee_table():
    body = _client(with_model=True).get("/api/health").json()

    rendered = repr(body).lower()
    assert "key" not in rendered
    assert "rate_pct" not in rendered
    assert "3.49" not in rendered


def test_the_page_and_health_agree_on_the_fee_date():
    client = _client()

    assert client.get("/api/health").json()["fee_retrieved_date"] == (
        client.get("/api/config").json()["fee_retrieved_date"]
    )


@pytest.mark.parametrize("path", ["/api/health", "/api/config", "/"])
def test_the_endpoints_a_monitor_might_touch_all_answer(path):
    assert _client().get(path).status_code == 200

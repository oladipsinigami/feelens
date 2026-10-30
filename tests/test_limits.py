"""The two limits that keep a free model budget from being spent by strangers.

The fee engine has no budget. The model does: 50 requests a day, shared by
everyone who opens the link, two or three per explanation. These tests are about
the difference between "the numbers are wrong" and "the prose is withheld" — a
refused request must still return a correct quote and a guardrail-clean
explanation, because a limit on the model is not a limit on the product.
"""

import json
from datetime import date
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.agent import ExplanationAgent, FeeTools, OpenRouterClient
from app.agent.client import ChatResult, ToolCall
from app.agent.template import template_explanation
from app.engine import load_fees, load_rates
from app.limits import ModelBudget, VisitorLimiter
from app.main import _build_agent, _client_address, create_app

FIXTURES = Path(__file__).parent / "fixtures"

QUOTE_CALL = ToolCall(
    id="call_quote",
    name="get_quote",
    arguments={"amount": "5000.00", "corridor": "EUR", "payment": "invoice_paypal"},
)


def fees():
    return load_fees(FIXTURES / "fees.json")


def rates():
    return load_rates(FIXTURES / "fx_snapshot.json")


class ScriptedModel:
    """Calls get_quote once, then restates whatever the tool returned."""

    available = True
    models = ["fake/model"]

    def __init__(self):
        self.requests = 0

    def chat(self, messages, tools=None):
        self.requests += 1
        if not any(m.get("role") == "tool" for m in messages):
            return ChatResult(content="", tool_calls=[QUOTE_CALL], model="fake/model")
        for message in reversed(messages):
            if message.get("role") == "tool":
                payload = json.loads(message["content"])
                if "current" in payload:
                    return ChatResult(
                        content=json.dumps(template_explanation(payload)),
                        tool_calls=[],
                        model="fake/model",
                    )
        raise AssertionError("no quote in the transcript")


def app_with(model=None, limiter=None, budget=None):
    model = model or ScriptedModel()
    agent = ExplanationAgent(FeeTools(fees(), rates()), model)
    return (
        TestClient(
            create_app(
                FIXTURES / "fees.json",
                FIXTURES / "fx_snapshot.json",
                agent=agent,
                limiter=limiter,
                budget=budget,
            )
        ),
        model,
    )


def explain(client, amount="5000.00", corridor="EUR", payment="invoice_paypal", headers=None):
    response = client.post(
        "/api/explain",
        json={"amount": amount, "corridor": corridor, "payment": payment},
        headers=headers or {},
    )
    assert response.status_code == 200
    return response.json()["explanation"]


# --- the visitor window ---------------------------------------------------


def test_a_visitor_may_explain_up_to_the_limit_and_then_is_told_to_wait():
    limiter = VisitorLimiter(max_explanations=2, window_seconds=600, clock=lambda: 0)

    assert limiter.take("1.1.1.1").allowed
    assert limiter.take("1.1.1.1").allowed
    refused = limiter.take("1.1.1.1")

    assert refused.allowed is False
    assert refused.retry_after > 0
    assert "2 explanations per 10 minutes" in refused.note
    assert "unaffected" in refused.note


def test_the_refusal_reads_like_english_at_a_limit_of_one():
    limiter = VisitorLimiter(max_explanations=1, window_seconds=600, clock=lambda: 0)
    limiter.take("1.1.1.1")

    note = limiter.take("1.1.1.1").note

    assert "1 explanation per 10 minutes" in note
    assert "1 explanations" not in note


def test_the_window_slides_so_the_allowance_comes_back():
    now = [0.0]
    limiter = VisitorLimiter(max_explanations=1, window_seconds=60, clock=lambda: now[0])

    assert limiter.take("1.1.1.1").allowed
    assert limiter.take("1.1.1.1").allowed is False

    now[0] = 61.0
    assert limiter.take("1.1.1.1").allowed


def test_one_visitor_cannot_spend_another_visitors_allowance():
    limiter = VisitorLimiter(max_explanations=1, window_seconds=60, clock=lambda: 0)

    assert limiter.take("1.1.1.1").allowed
    assert limiter.take("1.1.1.1").allowed is False
    assert limiter.take("2.2.2.2").allowed


def test_the_visitor_table_does_not_grow_without_bound():
    """A public link attracts scanners; their addresses must be forgettable."""
    now = [0.0]
    limiter = VisitorLimiter(
        max_explanations=1, window_seconds=60, max_visitors=8, clock=lambda: now[0]
    )

    for index in range(200):
        limiter.take(f"10.0.0.{index}")

    assert len(limiter._seen) <= 8


# --- the visitor's day ----------------------------------------------------


def test_a_window_alone_does_not_bound_a_day():
    """The finding this limit exists for.

    Six explanations every ten minutes is unlimited over an afternoon, and the
    whole day's model budget is about seventeen explanations. Without a daily
    cap one visitor can spend it and leave every judge after them on the
    template — on the one feature the submission is about.
    """
    now = [0.0]
    limiter = VisitorLimiter(
        max_explanations=1, window_seconds=60, max_per_day=2, clock=lambda: now[0]
    )

    assert limiter.take("1.1.1.1").allowed
    now[0] = 61.0  # the window has slid; the day has not
    assert limiter.take("1.1.1.1").allowed
    now[0] = 122.0
    refused = limiter.take("1.1.1.1")

    assert refused.allowed is False
    assert "2 of 2 model explanations per visitor per day" in refused.note
    assert "00:00 UTC" in refused.note
    assert "unaffected" in refused.note


def test_a_window_refusal_does_not_spend_the_days_allowance():
    """Charging a refusal would end the day early for no reason.

    The window refuses before anything is counted, so a visitor who hammers the
    button is throttled without losing the explanations they had left.
    """
    now = [0.0]
    limiter = VisitorLimiter(
        max_explanations=1, window_seconds=600, max_per_day=2, clock=lambda: now[0]
    )

    assert limiter.take("1.1.1.1").allowed
    for _ in range(10):
        assert limiter.take("1.1.1.1").allowed is False
    assert limiter._per_day["1.1.1.1"] == 1

    now[0] = 601.0
    assert limiter.take("1.1.1.1").allowed
    assert limiter._per_day["1.1.1.1"] == 2

    now[0] = 1202.0  # the window is clear, so only the day can refuse
    refused = limiter.take("1.1.1.1")
    assert refused.allowed is False
    assert "2 of 2" in refused.note


def test_the_days_allowance_resets_at_utc_midnight():
    today = [date(2026, 10, 28)]
    limiter = VisitorLimiter(
        max_explanations=6,
        window_seconds=60,
        max_per_day=1,
        clock=lambda: 0,
        day_clock=lambda: today[0],
    )

    assert limiter.take("1.1.1.1").allowed
    assert limiter.take("1.1.1.1").allowed is False

    today[0] = date(2026, 10, 29)
    assert limiter.take("1.1.1.1").allowed


def test_the_day_is_per_visitor_not_per_process():
    limiter = VisitorLimiter(
        max_explanations=6, window_seconds=60, max_per_day=1, clock=lambda: 0
    )

    assert limiter.take("1.1.1.1").allowed
    assert limiter.take("1.1.1.1").allowed is False
    assert limiter.take("2.2.2.2").allowed


def test_no_daily_cap_by_default():
    """A local run should not be throttled by a limit that exists for a link."""
    limiter = VisitorLimiter(max_explanations=1, window_seconds=60, clock=lambda: 0)

    assert limiter.max_per_day is None
    assert limiter.snapshot()["per_visitor_per_day"] is None


# --- the account's day ----------------------------------------------------


def test_the_budget_resets_on_a_new_utc_day():
    today = [date(2026, 10, 28)]
    budget = ModelBudget(limit=4, clock=lambda: today[0])

    budget.record(4)
    assert budget.exhausted

    today[0] = date(2026, 10, 29)
    assert budget.remaining == 4
    assert budget.spent == 0


def test_an_exhausted_budget_says_what_was_spent_and_when_it_returns():
    budget = ModelBudget(limit=3, clock=lambda: date(2026, 10, 28))
    budget.record(3)

    decision = budget.decision()

    assert decision.allowed is False
    assert "3 of its 3 model requests" in decision.note
    assert "00:00 UTC" in decision.note


def test_a_cap_of_zero_switches_the_model_off_without_calling_it_spent():
    """`MODEL_DAILY_REQUEST_CAP=0` means off, not "spent 0 of 0"."""
    budget = ModelBudget(limit=0, clock=lambda: date(2026, 10, 28))

    decision = budget.decision()

    assert decision.allowed is False
    assert "switched off" in decision.note
    assert "spent" not in decision.note


def test_the_budget_counts_requests_not_explanations():
    """One explanation is two or three requests; counting explanations is a guess."""
    budget = ModelBudget(limit=10, clock=lambda: date(2026, 10, 28))

    budget.record()
    budget.record()
    budget.record()

    assert budget.spent == 3
    assert budget.remaining == 7


# --- through HTTP ---------------------------------------------------------


def test_the_seventh_explanation_from_one_visitor_is_a_template_not_an_error():
    limiter = VisitorLimiter(max_explanations=6, window_seconds=600, clock=lambda: 0)
    client, model = app_with(limiter=limiter)

    # Distinct amounts, because a repeated quote is a cached answer and costs
    # nothing — the limit exists for the model, not for the page.
    for index in range(6):
        assert explain(client, amount=f"{1000 + index}.00")["source"] == "model"
    after_six = model.requests

    seventh = explain(client, amount="2000.00")

    assert seventh["source"] == "template"
    assert "6 explanations per 10 minutes" in seventh["note"]
    assert seventh["checks"]["ok"] is True, "the figures are still correct"
    assert model.requests == after_six, "the model was consulted anyway"


def test_a_rate_limited_visitor_still_gets_a_cached_answer():
    """Refusing an answer the app already holds would be a limit on the page."""
    limiter = VisitorLimiter(max_explanations=1, window_seconds=600, clock=lambda: 0)
    client, model = app_with(limiter=limiter)

    first = explain(client)
    assert first["source"] == "model"

    explain(client, amount="1234.00")  # spends the allowance
    again = explain(client)  # the first quote, now over the limit

    assert again["source"] == "model"
    assert again["cached"] is True


def test_a_visitor_who_spends_the_day_gets_the_template_and_a_reason():
    """The day cap, through HTTP, at the same address.

    Distinct amounts, because a repeated quote is a cached answer and costs
    nothing — the cap exists for the model, not for the page.
    """
    limiter = VisitorLimiter(
        max_explanations=6, window_seconds=600, max_per_day=2, clock=lambda: 0
    )
    client, model = app_with(limiter=limiter)

    assert explain(client, amount="1000.00")["source"] == "model"
    assert explain(client, amount="1001.00")["source"] == "model"
    after_two = model.requests

    third = explain(client, amount="1002.00")

    assert third["source"] == "template"
    assert "2 of 2 model explanations per visitor per day" in third["note"]
    assert third["checks"]["ok"] is True, "the figures are still correct"
    assert model.requests == after_two, "the model was consulted anyway"


def test_a_cached_answer_does_not_spend_the_days_allowance():
    """A cached answer is free, so it must not be charged to the budget.

    Otherwise the common demo case — the same quote opened twice — would spend
    two of a visitor's three explanations and withhold prose that costs nothing.
    """
    limiter = VisitorLimiter(
        max_explanations=6, window_seconds=600, max_per_day=1, clock=lambda: 0
    )
    client, model = app_with(limiter=limiter)

    assert explain(client)["source"] == "model"
    after_one = model.requests

    for _ in range(3):
        again = explain(client)
        assert again["source"] == "model"
        assert again["cached"] is True
    assert model.requests == after_one

    # The single real explanation is still the only one spent, so a *different*
    # quote is refused by the day cap rather than by the window.
    refused = explain(client, amount="4321.00")
    assert refused["source"] == "template"
    assert "1 of 1 model explanations per visitor per day" in refused["note"]


def test_an_exhausted_daily_budget_returns_the_template_and_says_why():
    budget = ModelBudget(limit=1, clock=lambda: date(2026, 10, 28))
    budget.record()
    client, model = app_with(budget=budget)

    explanation = explain(client)

    assert explanation["source"] == "template"
    assert "daily allowance is spent" in explanation["note"]
    assert explanation["checks"]["ok"] is True
    assert model.requests == 0


def test_a_spoofed_forwarded_for_header_does_not_mint_a_new_allowance():
    """Render appends the real peer, so the last entry is the one to trust.

    Reading the first entry would let anyone send a fresh `X-Forwarded-For` per
    request and never hit a limit at all.
    """
    limiter = VisitorLimiter(max_explanations=1, window_seconds=600, clock=lambda: 0)
    client, _ = app_with(limiter=limiter)

    real = "203.0.113.7"
    assert explain(client, amount="1000.00", headers={"X-Forwarded-For": f"1.1.1.1, {real}"})[
        "source"
    ] == "model"
    assert explain(client, amount="1001.00", headers={"X-Forwarded-For": f"2.2.2.2, {real}"})[
        "source"
    ] == "template"
    assert explain(client, amount="1002.00", headers={"X-Forwarded-For": "203.0.113.8"})[
        "source"
    ] == "model"


def test_the_client_address_falls_back_to_the_socket_peer():
    class NoHeader:
        headers = {}
        client = type("Peer", (), {"host": "198.51.100.4"})()

    assert _client_address(NoHeader()) == "198.51.100.4"


def test_the_limits_are_visible_to_a_judge():
    client, _ = app_with()

    limits = client.get("/api/config").json()["explanation"]["limits"]

    assert limits["per_visitor"]["explanations_per_visitor"] == 6
    assert limits["per_visitor"]["per_visitor_per_day"] == 3
    assert limits["daily_model_requests"]["requests_per_day"] == 44
    assert "template" in limits["when_exceeded"]

    health = client.get("/api/health").json()
    assert health["model_budget"]["remaining_allowance"] == 44


def test_the_daily_cap_can_be_switched_off_for_a_local_run(monkeypatch):
    monkeypatch.setenv("MODEL_EXPLAINS_PER_VISITOR_PER_DAY", "0")
    client = TestClient(
        create_app(FIXTURES / "fees.json", FIXTURES / "fx_snapshot.json")
    )

    limits = client.get("/api/config").json()["explanation"]["limits"]

    assert limits["per_visitor"]["per_visitor_per_day"] is None


# --- the wiring -----------------------------------------------------------


def test_the_real_client_reports_every_request_to_the_budget(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    budget = ModelBudget(limit=10)

    agent = _build_agent(fees(), rates(), budget)

    assert agent.client._on_request == budget.record


def test_the_client_calls_the_hook_once_per_http_request_including_the_fallback():
    seen = []

    def handler(request):
        model = json.loads(request.content)["model"]
        if "primary" in model:
            return httpx.Response(429, json={"error": "slow down"})
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
                ]
            },
        )

    client = OpenRouterClient(
        "test-key",
        "primary/model:free",
        "fallback/model:free",
        transport=httpx.MockTransport(handler),
        sleep=lambda _seconds: None,
        on_request=lambda: seen.append(1),
    )

    client.chat([{"role": "user", "content": "price this"}])

    assert len(seen) == 2, "both the failed attempt and the fallback are real requests"

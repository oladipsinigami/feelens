"""The explanation endpoint, end to end through HTTP.

Two invariants are worth more than the rest of this file:

- the prose and the table are built from the *same* payload, so the explanation
  cannot describe a different invoice from the one on screen;
- with no key, the endpoint still answers, and still passes its own guardrail.
  A hackathon demo that dies because a free tier is rate-limited is not a demo.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.agent import ExplanationAgent, FeeTools
from app.agent.client import ChatResult, ToolCall
from app.agent.tools import TOOL_SCHEMAS
from app.engine import load_fees, load_rates
from app.main import create_app

FIXTURES = Path(__file__).parent / "fixtures"

QUOTE_CALL = ToolCall(
    id="call_quote",
    name="get_quote",
    arguments={"amount": "5000.00", "corridor": "EUR", "payment": "invoice_paypal"},
)


class ScriptedClient:
    available = True
    models = ["fake/one"]

    def __init__(self, script):
        self.script = list(script)
        self.requests = []

    def chat(self, messages, tools=None):
        self.requests.append({"messages": messages, "tools": tools})
        step = self.script[min(len(self.requests) - 1, len(self.script) - 1)]
        return step(messages) if callable(step) else step


def _agent(script=None):
    tools = FeeTools(
        load_fees(FIXTURES / "fees.json"),
        load_rates(FIXTURES / "fx_snapshot.json"),
    )
    client = ScriptedClient(script) if script else None
    return ExplanationAgent(tools, client)


@pytest.fixture(scope="module")
def offline():
    return TestClient(
        create_app(FIXTURES / "fees.json", FIXTURES / "fx_snapshot.json", agent=_agent())
    )


def post(client, path, **overrides):
    body = {"amount": "5000.00", "corridor": "EUR", "payment": "invoice_paypal"}
    body.update(overrides)
    return client.post(path, json=body)


# --- offline behaviour ----------------------------------------------------


def test_config_reports_honestly_that_no_model_is_configured(offline):
    body = offline.get("/api/config").json()

    assert body["explanation"]["model_configured"] is False
    assert body["explanation"]["fallback"] == "deterministic template"
    assert "tool" in body["explanation"]["guardrail"]


def test_explain_still_answers_with_no_key(offline):
    body = post(offline, "/api/explain").json()

    assert body["explanation"]["source"] == "template"
    assert body["explanation"]["text"]
    assert body["explanation"]["checks"]["ok"] is True


def test_the_prose_and_the_table_describe_the_same_invoice(offline):
    quote = post(offline, "/api/quote").json()
    explain = post(offline, "/api/explain").json()

    assert explain["current"] == quote["current"]
    assert explain["optimized"] == quote["optimized"]
    assert explain["savings"] == quote["savings"]


def test_the_template_names_the_net_the_page_shows(offline):
    body = post(offline, "/api/explain").json()

    net = body["current"]["net_received"]
    assert net in body["explanation"]["text"].replace(",", "")


def test_an_unknown_corridor_is_refused_the_same_way_as_a_quote(offline):
    assert post(offline, "/api/explain", corridor="JPY").status_code == 422


def test_a_nonsense_amount_is_refused_before_any_model_is_called(offline):
    assert post(offline, "/api/explain", amount="lots").status_code == 422


# --- the model path, through HTTP ----------------------------------------


def _good_answer(messages):
    for message in reversed(messages):
        if message.get("role") == "tool":
            payload = json.loads(message["content"])
            if "current" in payload:
                return ChatResult(
                    content=json.dumps(
                        {
                            "explanation": (
                                f"PayPal keeps {payload['current']['total_fees']} USD of "
                                f"the {payload['current']['gross_received']} USD it "
                                f"collects, an effective rate of "
                                f"{payload['current']['effective_rate_pct']}%."
                            ),
                            "cited_rules": ["invoice_checkout"],
                            "caveats": [],
                        }
                    ),
                    tool_calls=[],
                    model="fake/one",
                )
    raise AssertionError("no tool result")


def test_a_scripted_model_answer_is_served_with_its_checks():
    client = TestClient(
        create_app(
            FIXTURES / "fees.json",
            FIXTURES / "fx_snapshot.json",
            agent=_agent(
                [
                    ChatResult(content="", tool_calls=[QUOTE_CALL], model="fake/one"),
                    _good_answer,
                ]
            ),
        )
    )

    body = post(client, "/api/explain").json()["explanation"]

    assert body["source"] == "model"
    assert body["checks"]["ok"] is True
    assert body["checks"]["numbers_checked"] >= 3
    assert body["trace"][0]["name"] == "get_quote"


def test_a_model_that_invents_a_figure_never_reaches_the_page():
    bad = ChatResult(
        content=json.dumps(
            {
                "explanation": "PayPal keeps about 450 USD, which is roughly 9%.",
                "cited_rules": ["invoice_checkout"],
            }
        ),
        tool_calls=[],
        model="fake/one",
    )
    client = TestClient(
        create_app(
            FIXTURES / "fees.json",
            FIXTURES / "fx_snapshot.json",
            agent=_agent([ChatResult(content="", tool_calls=[QUOTE_CALL], model="fake/one"), bad, bad]),
        )
    )

    body = post(client, "/api/explain").json()["explanation"]

    assert body["source"] == "template"
    assert "450" not in body["text"]
    assert "failed validation" in body["note"]


def test_the_config_reports_the_model_when_one_is_configured():
    client = TestClient(
        create_app(
            FIXTURES / "fees.json",
            FIXTURES / "fx_snapshot.json",
            agent=_agent([ChatResult(content="{}", tool_calls=[], model="fake/one")]),
        )
    )

    body = client.get("/api/config").json()["explanation"]

    assert body["model_configured"] is True
    assert body["models"] == ["fake/one"]


def test_the_quote_tool_hands_over_no_bare_fee_rate_to_add_up():
    """The quote payload states totals, not operands.

    `get_fee_rule` does expose a rate, deliberately: it is how the model
    explains *why* a line exists, and it comes with the source URL and the date
    it was checked. The quote itself never carries a bare rate for the model to
    combine with another one.
    """
    tools = FeeTools(
        load_fees(FIXTURES / "fees.json"), load_rates(FIXTURES / "fx_snapshot.json")
    )
    quote = tools.get_quote("5000.00", "EUR", "invoice_paypal")

    def keys(node):
        if isinstance(node, dict):
            for key, value in node.items():
                yield key
                yield from keys(value)
        elif isinstance(node, list):
            for value in node:
                yield from keys(value)

    assert "rate_pct" not in set(keys(quote))
    assert "rate_pct" in tools.get_fee_rule("invoice_checkout")
    assert set(item["function"]["name"] for item in TOOL_SCHEMAS) == {
        "get_quote",
        "get_fee_rule",
        "list_corridors",
        "get_fx_snapshot",
    }

"""The loop, driven by a scripted model.

No network, no key, no spend. Each test scripts what the model does — call a
tool, answer without one, hallucinate, fail to return JSON — and asserts that
the loop ends somewhere safe. The important assertion in this file is not that
the happy path works; it is that *every* unhappy path ends in the deterministic
template rather than in unvalidated prose.
"""

import json

import pytest

from app.agent.client import ChatResult, LLMUnavailable, ToolCall
from app.agent.loop import MODEL, TEMPLATE, ExplanationAgent
from app.agent.prompts import SYSTEM_PROMPT
from app.agent.template import template_explanation
from app.agent.tools import FeeTools

QUOTE_CALL = ToolCall(
    id="call_quote",
    name="get_quote",
    arguments={"amount": "5000.00", "corridor": "EUR", "payment": "invoice_paypal"},
)


def answer(content: str) -> ChatResult:
    return ChatResult(content=content, tool_calls=[], model="fake/model")


def calls_tool(call=QUOTE_CALL) -> ChatResult:
    return ChatResult(content="", tool_calls=[call], model="fake/model")


def echo_tool_result(messages) -> ChatResult:
    """A model that faithfully restates the tool output. The good citizen."""
    for message in reversed(messages):
        if message.get("role") == "tool":
            payload = json.loads(message["content"])
            if "current" in payload:
                return answer(json.dumps(template_explanation(payload)))
    raise AssertionError("no successful tool result in the transcript")


class ScriptedClient:
    """Plays a fixed list of responses. Records every request it was sent."""

    available = True

    def __init__(self, script):
        self.script = list(script)
        self.requests = []

    def chat(self, messages, tools=None):
        self.requests.append({"messages": [dict(m) for m in messages], "tools": tools})
        step = self.script[min(len(self.requests) - 1, len(self.script) - 1)]
        return step(messages) if callable(step) else step


@pytest.fixture
def tools(fees, rates):
    return FeeTools(fees, rates)


def run(tools, script):
    client = ScriptedClient(script)
    agent = ExplanationAgent(tools, client)
    return agent.explain("5000.00", "EUR", "invoice_paypal"), client


# --- the prompt cannot leak the rates -------------------------------------


def test_the_system_prompt_contains_no_fee_rates():
    """A model cannot add numbers it was never shown."""
    for rate in ("3.49", "2.99", "1.50", "4.00", "3.00", "0.49"):
        assert rate not in SYSTEM_PROMPT, rate


# --- happy path -----------------------------------------------------------


def test_a_model_answer_built_from_a_tool_result_is_accepted(tools):
    explanation, client = run(tools, [calls_tool(), echo_tool_result])

    assert explanation.source == MODEL
    assert explanation.checks["ok"] is True
    assert "get_quote" in [step["name"] for step in explanation.trace]
    assert explanation.cited_rules
    assert explanation.model == "fake/model"


def test_the_model_is_offered_the_tools_on_the_first_call(tools):
    _, client = run(tools, [calls_tool(), echo_tool_result])

    offered = [item["function"]["name"] for item in client.requests[0]["tools"]]
    assert offered == ["get_quote", "get_fee_rule", "list_corridors", "get_fx_snapshot"]


def test_the_tool_result_is_fed_back_to_the_model_verbatim(tools):
    _, client = run(tools, [calls_tool(), echo_tool_result])

    tool_message = [m for m in client.requests[1]["messages"] if m["role"] == "tool"][0]
    payload = json.loads(tool_message["content"])
    assert payload["current"]["total_fees"]
    assert tool_message["tool_call_id"] == "call_quote"


# --- the guardrail is a backstop, and it works ----------------------------


def test_an_invented_figure_is_rejected_and_repaired(tools):
    bad = answer(
        json.dumps(
            {
                "explanation": "PayPal keeps about 450 USD, roughly 9% of the invoice.",
                "cited_rules": ["invoice_checkout"],
            }
        )
    )

    explanation, client = run(tools, [calls_tool(), bad, echo_tool_result])

    assert explanation.source == MODEL
    assert explanation.checks["ok"] is True
    repair_request = client.requests[2]["messages"][-1]["content"]
    assert "450" in repair_request


def test_a_model_that_will_not_stop_inventing_figures_is_replaced_by_the_template(tools):
    bad = answer(
        json.dumps(
            {
                "explanation": "PayPal keeps about 450 USD.",
                "cited_rules": ["invoice_checkout"],
            }
        )
    )

    explanation, client = run(tools, [calls_tool(), bad, bad])

    assert explanation.source == TEMPLATE
    assert "failed validation" in explanation.note
    assert "450" not in explanation.text


def test_a_model_that_answers_without_a_tool_is_not_shown(tools):
    """It has no numbers, so whatever it wrote is a guess, however fluent."""
    explanation, _ = run(
        tools,
        [
            answer(
                json.dumps(
                    {
                        "explanation": "PayPal takes a slice of your invoice.",
                        "cited_rules": ["invoice_checkout"],
                    }
                )
            )
        ],
    )

    assert explanation.source == TEMPLATE
    assert "without consulting a tool" in explanation.note


def test_a_reply_that_is_not_json_is_repaired_not_shown(tools):
    prose = answer("PayPal keeps 447.65 USD, which is 8.00% of your invoice.")

    explanation, client = run(tools, [calls_tool(), prose, echo_tool_result])

    assert explanation.source == MODEL
    assert "JSON object" in client.requests[2]["messages"][-1]["content"]


def test_json_inside_a_code_fence_is_still_read(tools):
    fenced = answer(
        "```json\n"
        + json.dumps(template_explanation(tools.get_quote("5000.00", "EUR", "invoice_paypal")))
        + "\n```"
    )

    explanation, _ = run(tools, [calls_tool(), fenced])

    assert explanation.source == MODEL


# --- nothing here may raise -----------------------------------------------


def test_a_dead_model_falls_back_without_an_error(tools):
    class Dead:
        available = True

        def chat(self, messages, tools=None):
            raise LLMUnavailable("HTTP 429")

    explanation = ExplanationAgent(tools, Dead()).explain("5000.00", "EUR", "invoice_paypal")

    assert explanation.source == TEMPLATE
    assert "HTTP 429" in explanation.note
    assert explanation.checks["ok"] is True


def test_no_client_at_all_still_explains(tools):
    explanation = ExplanationAgent(tools, None).explain("5000.00", "EUR", "invoice_paypal")

    assert explanation.source == TEMPLATE
    assert explanation.text
    assert explanation.checks["ok"] is True


def test_a_model_stuck_in_a_tool_loop_is_stopped(tools):
    explanation, client = run(tools, [calls_tool()])

    assert explanation.source == TEMPLATE
    assert "step limit" in explanation.note
    assert len(client.requests) == 4


def test_a_bad_tool_call_is_returned_to_the_model_as_an_error(tools):
    """A model that asks for an unpriced corridor must be told, not crashed."""
    bad_call = ToolCall(
        id="call_bad",
        name="get_quote",
        arguments={"amount": "5000.00", "corridor": "JPY", "payment": "direct"},
    )

    explanation, client = run(
        tools, [calls_tool(bad_call), calls_tool(QUOTE_CALL), echo_tool_result]
    )

    tool_message = [m for m in client.requests[1]["messages"] if m["role"] == "tool"][0]
    assert "JPY" in json.loads(tool_message["content"])["error"]
    assert explanation.source == MODEL
    assert [step["arguments"]["corridor"] for step in explanation.trace] == ["JPY", "EUR"]


def test_an_unpricable_request_says_so_instead_of_explaining_nothing(tools):
    explanation = ExplanationAgent(tools, None).explain("5000.00", "JPY", "direct")

    assert explanation.source == TEMPLATE
    assert "JPY" in explanation.text
    assert explanation.checks["ok"] is False


def test_the_trace_records_what_the_agent_actually_asked_for(tools):
    explanation, _ = run(tools, [calls_tool(), echo_tool_result])

    assert explanation.trace[0]["name"] == "get_quote"
    assert explanation.trace[0]["arguments"]["corridor"] == "EUR"


def test_every_explanation_serialises_to_json(tools):
    explanation, _ = run(tools, [calls_tool(), echo_tool_result])

    json.dumps(explanation.as_dict())

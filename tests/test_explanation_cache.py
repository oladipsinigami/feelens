"""The explanation cache, which exists because 50 requests a day is the budget.

A free OpenRouter account gets 50 model requests per day and one explanation
costs two or three, so the same quote asked twice must not cost twice. Only
validated model answers are stored: a template is cheap to rebuild, and caching
it would pin the page to the template after the model recovered.
"""

import json
from pathlib import Path

from app.agent.client import ChatResult, LLMUnavailable, ToolCall
from app.agent.loop import MODEL, TEMPLATE, ExplanationAgent
from app.agent.template import template_explanation
from app.agent.tools import FeeTools

FIXTURES = Path(__file__).parent / "fixtures"

QUOTE_CALL = ToolCall(
    id="call_quote",
    name="get_quote",
    arguments={"amount": "5000.00", "corridor": "EUR", "payment": "invoice_paypal"},
)


class ScriptedModel:
    """Two steps: ask for the quote, then restate the tool result faithfully."""

    available = True

    def __init__(self, fail_first: bool = False):
        self.requests = []
        self.fail_first = fail_first

    def chat(self, messages, tools=None):
        self.requests.append(messages)

        if self.fail_first and len(self.requests) == 1:
            raise LLMUnavailable("HTTP 429")

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


def tools():
    from app.engine import load_fees, load_rates

    return FeeTools(load_fees(FIXTURES / "fees.json"), load_rates(FIXTURES / "fx_snapshot.json"))


def agent(model, cache_size=256):
    return ExplanationAgent(tools(), model, cache_size=cache_size)


def test_the_same_quote_is_explained_once():
    model = ScriptedModel()
    subject = agent(model)

    first = subject.explain("5000.00", "EUR", "invoice_paypal")
    requests_after_first = len(model.requests)
    second = subject.explain("5000.00", "EUR", "invoice_paypal")

    assert first.source == MODEL
    assert second.source == MODEL
    assert len(model.requests) == requests_after_first, "the model was called twice"
    assert second.text == first.text


def test_a_reused_answer_says_so():
    subject = agent(ScriptedModel())

    subject.explain("5000.00", "EUR", "invoice_paypal")
    second = subject.explain("5000.00", "EUR", "invoice_paypal")

    assert second.cached is True
    assert second.as_dict()["cached"] is True


def test_a_different_question_is_a_different_answer():
    model = ScriptedModel()
    subject = agent(model)

    subject.explain("5000.00", "EUR", "invoice_paypal")
    before = len(model.requests)
    subject.explain("5000.00", "EUR", "invoice_paypal", question="why so high?")

    assert len(model.requests) > before


def test_a_different_corridor_is_a_different_answer():
    model = ScriptedModel()
    subject = agent(model)

    subject.explain("5000.00", "EUR", "invoice_paypal")
    before = len(model.requests)
    subject.explain("5000.00", "GBP", "invoice_paypal")

    assert len(model.requests) > before


def test_a_template_answer_is_not_cached():
    """The model recovered; the next request must be allowed to use it."""
    model = ScriptedModel(fail_first=True)
    subject = agent(model)

    first = subject.explain("5000.00", "EUR", "invoice_paypal")
    second = subject.explain("5000.00", "EUR", "invoice_paypal")

    assert first.source == TEMPLATE
    assert second.source == MODEL


def test_the_cache_does_not_grow_without_bound():
    subject = agent(ScriptedModel(), cache_size=2)

    for corridor in ("EUR", "GBP", "PHP", "AUD"):
        subject.explain("5000.00", corridor, "invoice_paypal")

    assert len(subject._cache) <= 2

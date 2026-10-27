"""The fallback explanation is held to the same standard as the model's.

If the template can drift from the numbers, then "the model never invents a
figure" is only half a promise — the other half is the text we show when the
model is unavailable, which is most of the time on a free tier.
"""

import pytest

from app.agent.guardrails import validate
from app.agent.template import template_explanation
from app.agent.tools import FeeTools
from app.corridors import CORRIDORS, PAYMENT_OPTIONS

COMBINATIONS = [
    (corridor["invoice_currency"], option["id"])
    for corridor in CORRIDORS
    for option in PAYMENT_OPTIONS
]


@pytest.fixture
def tools(fees, rates):
    return FeeTools(fees, rates)


@pytest.mark.parametrize("corridor,payment", COMBINATIONS)
def test_the_template_passes_the_guardrail_it_will_be_judged_by(tools, corridor, payment):
    quote = tools.get_quote("5000.00", corridor, payment)
    answer = template_explanation(quote)

    verdict = validate(answer, [quote])

    assert verdict.ok, verdict.violations


@pytest.mark.parametrize("corridor,payment", COMBINATIONS)
def test_the_template_cites_the_rules_it_describes(tools, corridor, payment):
    quote = tools.get_quote("5000.00", corridor, payment)
    answer = template_explanation(quote)

    assert answer["cited_rules"]
    for rule_id in answer["cited_rules"]:
        assert rule_id in {line["rule_id"] for line in quote["current"]["lines"]}


def test_the_template_states_the_effective_rate_and_the_net(tools):
    quote = tools.get_quote("5000.00", "EUR", "invoice_paypal")
    text = template_explanation(quote)["explanation"]

    # The prose groups thousands; the payload does not. Compare the value.
    assert quote["current"]["net_received"] in text.replace(",", "")
    assert quote["current"]["effective_rate_pct"] in text


def test_the_template_does_not_claim_to_be_a_quote(tools):
    quote = tools.get_quote("5000.00", "EUR", "invoice_paypal")
    text = template_explanation(quote)["explanation"].lower()

    assert "live" not in text
    assert "guaranteed" not in text


def test_the_template_mentions_the_savings_path_and_its_caveat(tools):
    quote = tools.get_quote("5000.00", "EUR", "invoice_paypal")
    answer = template_explanation(quote)

    assert quote["savings"] in answer["explanation"]
    assert any("4.00%" in caveat for caveat in answer["caveats"])


def test_the_template_omits_a_savings_sentence_when_there_is_no_saving(tools):
    """A USD invoice has no receiver-side conversion to remove."""
    quote = tools.get_quote("5000.00", "EUR", "invoice_paypal")
    quote["savings"] = "0.00"
    quote["optimized"]["net_received"] = quote["current"]["net_received"]

    text = template_explanation(quote)["explanation"]

    assert "instead would leave you" not in text


def test_a_zero_fixed_fee_is_not_described_as_a_fixed_fee(tools):
    """`direct` has no fixed component; saying 'plus a 0.00 fixed fee' is noise."""
    quote = tools.get_quote("5000.00", "EUR", "direct")
    text = template_explanation(quote)["explanation"]

    assert "0.00 USD fixed" not in text

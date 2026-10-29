"""What the quote is allowed to imply, not just what it computes.

The arithmetic here was never the weak part. The weak part was the story around
it: a "saving" that was really a cost moved to the client, a fee with nothing to
compare it against, a peer-to-peer product recommended for business income with
no mention of what it gives up, and a per-invoice figure that reads as trivial
next to a year of them.

Each of those is now a number the engine produces and the payload carries, so
the template and the page can state it without inventing anything. These tests
hold that line: every figure in the prose must exist in the payload, and the
payload must never contain the one thing the model is forbidden to receive — a
bare rate to add up.
"""

from decimal import ROUND_HALF_UP, Decimal

import pytest

from app.agent.guardrails import allowed_numbers, validate
from app.agent.template import template_explanation
from app.agent.tools import FeeTools
from app.corridors import PAYMENT_OPTIONS
from app.engine import NOMINAL_RAIL_PCT, Transaction, compare_paths
from app.serialize import quote_payload


@pytest.fixture
def tools(fees, rates):
    return FeeTools(fees, rates)


def usd_invoice_transaction():
    return Transaction(
        amount=Decimal("5000"),
        invoice_currency="USD",
        payer_currency="USD",
        payer_region="DE",
        receive_currency="USD",
        region="US",
        account_type="business",
        transaction_type="invoice",
        payment_method="paypal_checkout",
    )


# --- the client's side of the "cheaper" route -----------------------------


def test_the_route_that_saves_you_money_is_priced_on_the_other_side_too(tools):
    quote = tools.get_quote("5000.00", "EUR", "invoice_paypal")
    client = quote["client_side"]

    assert client["payer_converts"] is True
    assert client["payer_currency"] == "EUR"
    assert client["invoice_currency"] == "USD"
    assert client["total"] == "224.50"
    assert Decimal(client["total"]) > 0


def test_the_client_side_is_the_same_engine_pricing_the_spread(tools):
    """Not a prose estimate: the 4.00% rule, on the payer, from fees.json."""
    quote = tools.get_quote("5000.00", "EUR", "invoice_paypal")
    client = quote["client_side"]

    assert [line["rule_id"] for line in client["lines"]] == [
        "fx_spread_payer_other_currency"
    ]
    assert all(line["borne_by"] == "payer" for line in client["lines"])
    assert all(line["tier"] == "modeled" for line in client["lines"])


def test_the_client_cost_is_not_subtracted_from_your_net(tools):
    """Two sides of one payment. Merging them would inflate your apparent cost."""
    quote = tools.get_quote("5000.00", "EUR", "invoice_paypal")

    assert quote["current"]["payer_side_total"] == "0.00"
    assert Decimal(quote["client_side"]["total"]) > 0
    assert quote["current"]["net_received"] == str(
        Decimal(quote["current"]["gross_received"])
        - Decimal(quote["current"]["total_fees"])
    )


def test_net_savings_is_the_saving_minus_what_the_client_now_pays(tools):
    quote = tools.get_quote("5000.00", "EUR", "invoice_paypal")
    net = quote["net_savings"]

    expected = Decimal(quote["savings"]) - Decimal(quote["client_side"]["total"])
    assert Decimal(net["amount"]) == expected
    assert Decimal(net["magnitude"]) == abs(expected)
    assert net["direction"] == ("worse_off" if expected < 0 else "better_off")


def test_the_magnitude_is_positive_so_prose_never_writes_a_minus_sign(tools):
    """`-56.13` in prose is read by the guardrail as the unsourced token `56.13`."""
    quote = tools.get_quote("5000.00", "EUR", "invoice_paypal")
    net = quote["net_savings"]

    assert not net["magnitude"].startswith("-")
    assert net["direction"] == "worse_off"
    assert Decimal(net["magnitude"]) in allowed_numbers([quote])


def test_no_client_side_when_nothing_moves(fees, rates):
    comparison = compare_paths(usd_invoice_transaction(), fees, rates)
    payload = quote_payload(
        comparison,
        amount=Decimal("5000"),
        corridor={"invoice_currency": "USD", "client_country": "DE"},
        payment=PAYMENT_OPTIONS[1],
        disclaimer="test",
        tier="modeled",
    )

    assert payload["client_side"] is None
    assert Decimal(payload["net_savings"]["amount"]) == 0
    assert payload["net_savings"]["direction"] == "even"


# --- the annual figure ----------------------------------------------------


def test_the_annual_figure_is_exactly_twelve_invoices(tools):
    quote = tools.get_quote("5000.00", "EUR", "invoice_paypal")
    year = quote["savings_if_monthly_for_a_year"]

    assert year["months"] == 12
    assert Decimal(year["receiver_savings"]) == Decimal(quote["savings"]) * 12
    assert Decimal(year["client_cost"]) == Decimal(quote["client_side"]["total"]) * 12
    assert Decimal(year["net"]["amount"]) == Decimal(quote["net_savings"]["amount"]) * 12


def test_the_annual_figure_keeps_the_pair_direction(tools):
    """A year of a cost moved is still a cost moved, only larger."""
    quote = tools.get_quote("5000.00", "EUR", "invoice_paypal")
    year = quote["savings_if_monthly_for_a_year"]

    assert year["net"]["direction"] == quote["net_savings"]["direction"]
    assert Decimal(year["net"]["magnitude"]) == Decimal(
        quote["net_savings"]["magnitude"]
    ) * 12


# --- the rail benchmark ---------------------------------------------------


def test_the_rail_benchmark_is_one_percent_of_the_same_gross(tools):
    quote = tools.get_quote("5000.00", "EUR", "invoice_paypal")
    rail = quote["rail_comparison"]
    gross = Decimal(quote["current"]["gross_received"])

    assert NOMINAL_RAIL_PCT == Decimal("1.00")
    assert Decimal(rail["fee"]) == (gross * NOMINAL_RAIL_PCT / 100).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    assert Decimal(rail["net"]) == gross - Decimal(rail["fee"])


def test_the_rail_benchmark_is_labelled_hypothetical(tools):
    quote = tools.get_quote("5000.00", "EUR", "invoice_paypal")
    basis = quote["rail_comparison"]["basis"].lower()

    assert "hypothetical" in basis
    assert "not a quote" in basis


def test_the_quote_payload_still_hands_over_no_bare_rate(tools):
    """The rail is a benchmark, not a fee, and must not arrive as an operand."""
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
    assert "1.00%" in quote["rail_comparison"]["basis"]


# --- the product caveat ---------------------------------------------------


def test_direct_send_carries_a_seller_protection_caveat():
    direct = next(option for option in PAYMENT_OPTIONS if option["id"] == "direct")
    caveat = direct["caveat"].lower()

    assert "seller protection" in caveat
    assert "dispute" in caveat


def test_only_the_peer_to_peer_product_carries_that_caveat():
    for option in PAYMENT_OPTIONS:
        if option["id"] == "direct":
            assert option.get("caveat")
        else:
            assert not option.get("caveat"), option["id"]


def test_the_caveat_reaches_the_quote_and_the_template(tools):
    quote = tools.get_quote("5000.00", "EUR", "direct")
    answer = template_explanation(quote)

    assert quote["payment"]["caveat"]
    assert any("Seller Protection" in caveat for caveat in answer["caveats"])
    assert validate(answer, [quote]).ok


# --- the template says the unwelcome part too -----------------------------


def test_the_template_states_the_client_cost_and_the_net(tools):
    quote = tools.get_quote("5000.00", "EUR", "invoice_paypal")
    answer = template_explanation(quote)
    text = answer["explanation"]

    assert quote["client_side"]["total"] in text
    assert quote["net_savings"]["magnitude"] in text
    assert "cost moved rather than removed" in text
    assert validate(answer, [quote]).ok, validate(answer, [quote]).violations


def test_the_template_states_the_annual_figure(tools):
    quote = tools.get_quote("5000.00", "EUR", "invoice_paypal")
    text = template_explanation(quote)["explanation"]

    # The prose groups thousands; the payload does not. Compare the value.
    annual = quote["savings_if_monthly_for_a_year"]["receiver_savings"]
    assert annual in text.replace(",", "")
    assert "repeats monthly" in text


def test_the_template_states_the_rail_benchmark_without_calling_it_a_quote(tools):
    quote = tools.get_quote("5000.00", "EUR", "invoice_paypal")
    text = template_explanation(quote)["explanation"]

    assert quote["rail_comparison"]["fee"] in text
    assert "not a quote" in text
    assert "guaranteed" not in text.lower()


def test_a_zero_saving_does_not_produce_a_client_or_annual_sentence(tools):
    quote = tools.get_quote("5000.00", "EUR", "invoice_paypal")
    quote["savings"] = "0.00"

    text = template_explanation(quote)["explanation"]

    assert "repeats monthly" not in text
    assert "cost moved" not in text

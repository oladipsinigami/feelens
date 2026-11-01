"""A payment smaller than the fees against it is refused, not priced.

The finding: a 1 INR invoice into a USD account was priced at 0.49 USD of fees
against a 0.01 USD gross, so the app printed a net of **-0.48 USD** and an
effective rate of **4900%**. Every individual step was right — the fixed fee is
per transaction and does not shrink — but the answer was nonsense, and it was
presented with the same confidence as a real quote.

The fee schedule FeeLens cites does not say how PayPal handles a payment below
its own fixed fee, so the honest answer is to refuse and name the reason. Two
properties are asserted here:

- the refusal happens at the engine, so no surface can accidentally price it;
- a product with no fixed fee still prices at the same size, so the refusal is
  never the only answer a user has.

Frozen fixtures, no network.
"""

from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.engine import load_fees, load_rates
from app.engine.models import PaymentTooSmallError, Transaction
from app.engine import calculate_effective_cost
from app.main import create_app

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def fees():
    return load_fees(FIXTURES / "fees.json")


@pytest.fixture(scope="module")
def rates():
    return load_rates(FIXTURES / "fx_snapshot.json")


def tx(amount, currency="INR", payment_method="paypal_checkout", transaction_type="invoice"):
    return Transaction(
        amount=Decimal(amount),
        invoice_currency=currency,
        payer_currency=currency,
        payer_region="IN",
        receive_currency="USD",
        region="US",
        account_type="business",
        transaction_type=transaction_type,
        payment_method=payment_method,
    )


# --- the engine refuses ---------------------------------------------------


def test_an_invoice_smaller_than_its_fixed_fee_is_refused(fees, rates):
    """1 INR is about one cent; the fixed fee alone is 49 of them."""
    with pytest.raises(PaymentTooSmallError) as raised:
        calculate_effective_cost(tx("1.00"), "current", fees, rates)

    message = str(raised.value)
    assert "fixed" in message
    assert "negative" in message
    assert "increase the amount" in message


def test_the_refusal_says_what_the_payment_and_the_fees_would_be(fees, rates):
    """A refusal that does not say the numbers is a refusal nobody can act on."""
    with pytest.raises(PaymentTooSmallError) as raised:
        calculate_effective_cost(tx("10.00"), "current", fees, rates)

    message = str(raised.value)
    # Fixture INR rate 0.010382, so 10 INR is 0.10 USD — the live snapshot says
    # 0.11. The assertion is against the frozen fixture on purpose.
    assert "0.10 USD" in message, "the gross the invoice would collect"
    assert "0.49 USD" in message, "the fixed fee that causes it"


def test_the_refusal_does_not_claim_to_know_paypal_behaviour(fees, rates):
    """The honest part: the schedule is silent, so the message says so."""
    with pytest.raises(PaymentTooSmallError) as raised:
        calculate_effective_cost(tx("1.00"), "current", fees, rates)

    assert "does not say" in str(raised.value)


def test_a_product_without_a_fixed_fee_still_prices_at_the_same_size(fees, rates):
    """The refusal must not become "this corridor is unsupported".

    A direct send has no fixed fee, so 1 INR costs nothing in percentage terms
    and the net is not negative. If this ever starts raising, the guard has been
    written against the amount rather than against the arithmetic.
    """
    breakdown = calculate_effective_cost(
        tx("1.00", payment_method="goods_services_transfer", transaction_type="goods_services"),
        "current",
        fees,
        rates,
    )

    assert breakdown.net_received >= 0
    assert breakdown.total_fees == Decimal("0.00")


def test_an_invoice_just_above_the_threshold_still_prices(fees, rates):
    """100 INR clears the fixed fee, and must not be caught by the guard."""
    breakdown = calculate_effective_cost(tx("100.00"), "current", fees, rates)

    assert breakdown.net_received > 0
    assert breakdown.effective_rate_pct < 100


# --- through HTTP ---------------------------------------------------------


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app(FIXTURES / "fees.json", FIXTURES / "fx_snapshot.json"))


def test_the_api_answers_422_with_the_reason_not_a_negative_number(client):
    response = client.post(
        "/api/quote",
        json={"amount": "10.00", "corridor": "INR", "payment": "invoice_paypal"},
    )

    assert response.status_code == 422
    assert "fixed" in response.json()["detail"]


def test_the_comparison_reports_one_unavailable_product_and_prices_the_rest(client):
    """One refused product must not hide the two that are available."""
    body = client.post("/api/products", json={"amount": "10.00", "corridor": "INR"}).json()
    rows = {row["payment"]["id"]: row for row in body["products"]}

    assert rows["direct"]["priced"] is True
    assert rows["invoice_paypal"]["priced"] is False
    assert "fixed" in rows["invoice_paypal"]["reason"]
    assert rows["invoice_card"]["priced"] is False

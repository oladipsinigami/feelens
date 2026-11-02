"""The invoice currency is an input of its own, not a property of the client.

The finding: the form only let the currency follow "Where is your client?", so a
freelancer invoicing a German client had to invoice in EUR. Plenty of them
invoice in USD regardless — the client's country decides the cross-border fee,
not the currency the invoice is written in — and the app could not price it.

What these tests defend:

- the invoice currency is independent of the client's corridor;
- a USD invoice has no receiver-side conversion, because there is nothing to
  convert, while the cross-border surcharge still applies;
- an invoice in the client's own currency is unchanged, so the five original
  corridors keep the totals they were verified with;
- a currency the snapshot cannot convert is refused with the reason, never
  priced with an invented rate;
- the client-side assumption is stated when the two currencies differ, because
  the payer spread is real and belongs to the client.

Frozen fixtures, no network.
"""

from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.agent.template import template_explanation
from app.agent.tools import FeeTools
from app.engine import load_fees, load_rates
from app.main import create_app

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def fees():
    return load_fees(FIXTURES / "fees.json")


@pytest.fixture(scope="module")
def rates():
    return load_rates(FIXTURES / "fx_snapshot.json")


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app(FIXTURES / "fees.json", FIXTURES / "fx_snapshot.json"))


@pytest.fixture(scope="module")
def tools(fees, rates):
    return FeeTools(fees, rates)


def quote(client, invoice_currency=None, corridor="EUR", payment="direct"):
    body = {"amount": "5000.00", "corridor": corridor, "payment": payment}
    if invoice_currency is not None:
        body["invoice_currency"] = invoice_currency
    return client.post("/api/quote", json=body)


# --- the two currencies are independent ------------------------------------


def test_a_usd_invoice_to_a_euro_client_removes_only_the_receiver_conversion(client):
    """The client is still in Germany; only the invoice currency changed.

    The 3.00% receiver-side spread exists because EUR has to become USD. A USD
    invoice has nothing to convert, so that line must disappear. The 1.50%
    cross-border surcharge keys off the country, not the currency, so it stays.
    """
    body = quote(client, invoice_currency="USD").json()
    current = body["current"]
    rules = [line["rule_id"] for line in current["lines"]]

    assert body["invoice_currency"] == "USD"
    assert current["gross_received"] == "5000.00"
    assert "fx_spread_balance_conversion" not in rules
    assert "intl_surcharge_us" in rules
    # 2.99% base + 1.50% cross-border on 5000.00 USD.
    assert current["total_fees"] == "224.50"
    assert current["net_received"] == "4775.50"


def test_an_invoice_in_the_clients_currency_is_unchanged(client):
    """The original corridor behaviour must survive the new input."""
    body = quote(client).json()

    assert body["invoice_currency"] == "EUR"
    assert body["current"]["gross_received"] == "5612.50"
    assert any(
        line["rule_id"] == "fx_spread_balance_conversion"
        for line in body["current"]["lines"]
    )


def test_the_default_invoice_currency_is_the_clients_currency(client):
    for corridor in ("EUR", "GBP", "PHP", "AUD", "INR"):
        body = quote(client, corridor=corridor).json()
        assert body["invoice_currency"] == corridor


def test_the_payload_names_both_currencies(client):
    """A reader must be able to tell the client's money from the invoice's."""
    body = quote(client, invoice_currency="USD").json()

    assert body["invoice_currency"] == "USD"
    assert body["corridor"]["client_currency"] == "EUR"


# --- refusals ---------------------------------------------------------------


def test_a_currency_the_snapshot_cannot_convert_is_refused(client):
    """No rate means no total. The alternative is inventing the rate."""
    response = quote(client, invoice_currency="JPY")

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "No ECB rate for JPY" in detail
    assert "cannot be costed" in detail


def test_the_refusal_names_the_currencies_that_can_be_priced(client):
    detail = quote(client, invoice_currency="JPY").json()["detail"]

    for code in ("USD", "EUR", "GBP", "PHP", "AUD", "INR"):
        assert code in detail


# --- the client's side ------------------------------------------------------


def test_the_client_conversion_assumption_is_stated_when_currencies_differ(client):
    """The net is the receiver's side; the payer spread belongs to the client."""
    body = quote(client, invoice_currency="USD").json()

    assumptions = [line for line in body["assumptions"] if "client holds" in line]
    assert len(assumptions) == 1
    assert "USD" in assumptions[0]
    assert "EUR" in assumptions[0]
    assert "4.00%" in assumptions[0]
    assert "not deducted from your net" in assumptions[0]


def test_no_client_conversion_assumption_when_the_currencies_match(client):
    body = quote(client).json()

    assert not [line for line in body["assumptions"] if "client holds" in line]


# --- the rest of the surfaces agree ----------------------------------------


def test_the_products_endpoint_prices_the_requested_currency(client):
    body = client.post(
        "/api/products",
        json={"amount": "5000.00", "corridor": "EUR", "invoice_currency": "USD"},
    ).json()

    rows = {row["payment"]["id"]: row for row in body["products"]}
    assert body["invoice_currency"] == "USD"
    assert rows["direct"]["total_fees"] == "224.50"
    # 3.49% + 0.49 fixed + 1.50% cross-border.
    assert rows["invoice_paypal"]["total_fees"] == "249.99"


def test_the_agent_tool_prices_the_requested_currency(tools):
    priced = tools.get_quote("5000.00", "EUR", "direct", "USD")

    assert priced["invoice_currency"] == "USD"
    assert priced["current"]["gross_received"] == "5000.00"
    assert priced["current"]["total_fees"] == "224.50"


def test_the_template_speaks_in_the_invoice_currency(tools):
    """Prose that says EUR while the table says USD is a contradiction."""
    priced = tools.get_quote("5000.00", "EUR", "direct", "USD")
    text = template_explanation(priced)["explanation"]

    assert "5,000.00 USD" in text
    assert "5,000.00 EUR" not in text

"""The HTTP layer, tested against the frozen fixtures.

`create_app()` takes explicit data paths so these tests never read the live fee
config or FX snapshot. Otherwise a legitimate rate refresh would break the suite
and the tempting repair would be to edit the expected numbers.

What these tests defend:
- money leaves the API as a string, never a float
- every line is labelled `modeled` and carries a source
- a bad request is refused with a reason, not priced anyway
"""

from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.engine.models import MODELLED
from app.main import create_app

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def client():
    app = create_app(FIXTURES / "fees.json", FIXTURES / "fx_snapshot.json")
    return TestClient(app)


def quote(client, **overrides):
    body = {"amount": "5000.00", "corridor": "EUR", "payment": "invoice_paypal"}
    body.update(overrides)
    return client.post("/api/quote", json=body)


# --- config ---------------------------------------------------------------


def test_config_lists_exactly_the_five_corridors(client):
    corridors = client.get("/api/config").json()["corridors"]

    assert [c["invoice_currency"] for c in corridors] == ["EUR", "GBP", "PHP", "AUD", "INR"]
    assert all(c["client_country"] for c in corridors)


def test_config_offers_three_payment_products_not_two(client):
    """Invoice is not one price: PayPal splits it by how the client funds it."""
    options = client.get("/api/config").json()["payment_options"]

    assert [o["id"] for o in options] == ["direct", "invoice_paypal", "invoice_card"]


def test_config_publishes_the_date_the_rates_were_checked(client):
    body = client.get("/api/config").json()

    assert body["fee_retrieved_date"] == "2026-10-26"
    assert body["fx"]["snapshot_date"] == "2026-10-02"
    assert "not financial advice" in body["disclaimer"].lower()


# --- quoting --------------------------------------------------------------


def test_invoice_quote_matches_the_engine(client):
    body = quote(client).json()

    # Sum of the rounded lines: 195.88 + 84.19 + 168.38 + 0.49 at the frozen
    # fixture rate of 1.1225.
    assert body["current"]["total_fees"] == "448.94"
    assert body["current"]["net_received"] == "5163.56"
    assert body["current"]["total_fees_currency"] == "USD"


def test_card_invoice_is_priced_below_a_paypal_invoice(client):
    paypal = quote(client, payment="invoice_paypal").json()["current"]
    card = quote(client, payment="invoice_card").json()["current"]

    assert card["total_fees"] == "420.87"
    assert Decimal(card["total_fees"]) < Decimal(paypal["total_fees"])


def test_direct_send_is_cheaper_than_a_paypal_invoice(client):
    direct = quote(client, payment="direct").json()["current"]
    invoice = quote(client, payment="invoice_paypal").json()["current"]

    assert direct["total_fees"] == "420.38"
    # 0.50% of 5,000 EUR = 25.00 EUR, plus the 0.49 USD fixed fee: 28.55 USD.
    # Each line is rounded before it is summed, so the two totals can differ
    # from that by a cent; the claim is the rate, not the rounding.
    difference = Decimal(invoice["total_fees"]) - Decimal(direct["total_fees"])
    assert abs(difference - Decimal("28.55")) <= Decimal("0.01")


def test_every_line_is_modelled_and_sourced(client):
    """No line may claim to be live, and none may be unsourced."""
    body = quote(client).json()

    for line in body["current"]["lines"]:
        assert line["tier"] == "modeled"
        assert line["source_url"].startswith("https://")
        assert line["rule_id"]


def test_top_level_tier_is_the_engine_constant(client):
    """Compare against the constant, so renaming the tier cannot leave the API lying."""
    assert quote(client).json()["tier"] == MODELLED


# --- money never becomes a float -----------------------------------------


def test_money_is_serialised_as_a_string(client):
    body = quote(client).json()

    for key in ("total_fees", "net_received", "gross_received", "effective_rate_pct"):
        assert isinstance(body["current"][key], str), key
    for line in body["current"]["lines"]:
        assert isinstance(line["amount"], str)
        assert isinstance(line["fixed_amount"], str)


def test_fractional_cent_rounding_is_stable(client):
    """A third of a cent must not drift between two identical requests."""
    first = quote(client, amount="3333.33").json()["current"]
    second = quote(client, amount="3333.33").json()["current"]

    assert first == second


# --- the savings path -----------------------------------------------------


def test_savings_path_is_offered_and_carries_its_cost(client):
    body = quote(client).json()

    assert body["optimized"]["path_id"] == "invoice_in_holding_currency"
    assert Decimal(body["savings"]) > 0
    assert any("4.00%" in line for line in body["assumptions"])
    assert Decimal(body["optimized"]["net_received"]) > Decimal(body["current"]["net_received"])


# --- refusals -------------------------------------------------------------


def test_unknown_payment_method_is_refused_with_a_reason(client):
    response = quote(client, payment="wire_transfer")

    assert response.status_code == 422
    assert "wire_transfer" in response.json()["detail"]


def test_unknown_corridor_is_refused_with_the_known_list(client):
    response = quote(client, corridor="JPY")

    assert response.status_code == 422
    assert "JPY" in response.json()["detail"]


@pytest.mark.parametrize("amount", ["0", "-5", "abc", "", "nan", "Infinity"])
def test_a_nonsense_amount_is_refused(client, amount):
    response = quote(client, amount=amount)

    assert response.status_code == 422


def test_amount_with_thousands_separator_is_accepted(client):
    response = quote(client, amount="5,000.00")

    assert response.status_code == 200
    assert response.json()["amount"] == "5000.00"


# --- the page -------------------------------------------------------------


def test_the_page_is_served(client):
    response = client.get("/")

    assert response.status_code == 200
    assert "FeeLens" in response.text


def test_static_assets_are_served(client):
    for path in ("/static/style.css", "/static/app.js"):
        assert client.get(path).status_code == 200, path

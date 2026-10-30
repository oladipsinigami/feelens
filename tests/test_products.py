"""The three-product comparison endpoint.

This endpoint is the project's thesis made checkable: the same amount, the same
client and the same day priced through every way the client can pay. If it ever
disagrees with `/api/quote` for the same product, the page is showing two
different answers for one question, so that is the first thing tested.

No network. Frozen fixtures, as everywhere else.
"""

from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

FIXTURES = Path(__file__).parent / "fixtures"
BODY = {"amount": "5000.00", "corridor": "EUR"}


@pytest.fixture(scope="module")
def client():
    app = create_app(FIXTURES / "fees.json", FIXTURES / "fx_snapshot.json")
    return TestClient(app)


def products(client, **overrides):
    return client.post("/api/products", json={**BODY, **overrides})


def by_id(body):
    return {row["payment"]["id"]: row for row in body["products"]}


# --- what it returns ------------------------------------------------------


def test_every_product_is_priced(client):
    body = products(client).json()

    assert [row["payment"]["id"] for row in body["products"]] == [
        "direct",
        "invoice_paypal",
        "invoice_card",
    ]
    assert all(row["priced"] for row in body["products"])


def test_comparison_agrees_with_the_single_quote(client):
    """Two endpoints, one engine. A difference here is a bug on the page.

    The comparison exists so a reader does not have to click three times; it
    must not become a second source of truth while doing so.
    """
    comparison = by_id(products(client).json())

    for payment_id, row in comparison.items():
        quote = client.post(
            "/api/quote",
            json={**BODY, "payment": payment_id},
        ).json()["current"]

        assert row["total_fees"] == quote["total_fees"], payment_id
        assert row["net_received"] == quote["net_received"], payment_id
        assert row["effective_rate_pct"] == quote["effective_rate_pct"], payment_id


def test_the_spread_is_the_gap_between_the_extremes(client):
    body = products(client).json()

    fees = [Decimal(row["total_fees"]) for row in body["products"]]
    assert Decimal(body["spread"]["amount"]) == max(fees) - min(fees)
    assert body["spread"]["currency"] == "USD"


def test_the_cheapest_and_dearest_are_named_from_the_numbers(client):
    body = products(client).json()
    rows = {row["payment"]["id"]: Decimal(row["total_fees"]) for row in body["products"]}

    assert rows[body["cheapest"]] == min(rows.values())
    assert rows[body["dearest"]] == max(rows.values())


def test_the_direct_product_carries_its_caveat(client):
    """A cheaper number without its caveat is a recommendation we did not make.

    Direct send is not the invoicing product and may not carry Seller
    Protection. If the comparison ever drops that, it turns a product
    difference into a price difference.
    """
    direct = by_id(products(client).json())["direct"]

    assert direct["payment"]["caveat"]
    assert "Seller Protection" in direct["payment"]["caveat"]


def test_every_row_is_modelled_and_carries_a_tier(client):
    body = products(client).json()

    assert body["tier"] == "modeled"
    assert all(row["tier"] == "modeled" for row in body["products"])


def test_money_is_a_string_not_a_float(client):
    body = products(client).json()

    for row in body["products"]:
        assert isinstance(row["total_fees"], str)
        assert isinstance(row["net_received"], str)


# --- refusals -------------------------------------------------------------


def test_unknown_corridor_is_refused_with_the_known_list(client):
    response = products(client, corridor="XYZ")

    assert response.status_code == 422
    assert "EUR" in response.json()["detail"]


def test_non_positive_amount_is_refused(client):
    response = products(client, amount="0")

    assert response.status_code == 422


def test_a_non_number_is_refused_with_the_shape_it_wants(client):
    response = products(client, amount="about five thousand")

    assert response.status_code == 422
    assert "decimal string" in response.json()["detail"]


def test_a_corridor_with_a_tiny_invoice_still_prices_every_product(client):
    """The INR case is where a fixed fee dominates; it must not error out."""
    body = products(client, amount="200.00", corridor="INR").json()

    assert all(row["priced"] for row in body["products"])
    assert Decimal(body["spread"]["amount"]) > 0

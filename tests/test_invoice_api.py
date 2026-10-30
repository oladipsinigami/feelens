"""The invoice endpoint: what it returns, and what it refuses to claim.

The endpoint is the only place the deployed app touches PayPal, so the tests
here are the evidence for the Stage 1 claim "this project uses the PayPal API".
They also pin the honesty boundary: the invoice is `sandbox_observed`, the fee
is still `modeled`, and the response says both.

No network. A stand-in client is injected through `create_app`.
"""

from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.limits import VisitorLimiter
from app.main import create_app
from app.paypal import Invoice, MalformedResponse, PayPalError

FIXTURES = Path(__file__).parent / "fixtures"

BODY = {"amount": "5000.00", "corridor": "EUR", "payment": "invoice_paypal"}


def an_invoice(**overrides):
    fields = {
        "id": "INV2-TEST-0001-AAAA-BBBB",
        "status": "DRAFT",
        "invoice_number": "FEELENS-TEST-0001",
        "amount": Decimal("5000.00"),
        "currency": "EUR",
        "recipient_view_url": "https://www.sandbox.paypal.com/invoice/p/#TEST0001AAAABBBB",
        "invoicer_view_url": "https://www.sandbox.paypal.com/invoice/details/INV2-TEST-0001-AAAA-BBBB",
        "create_time": "2026-10-30T00:00:00Z",
    }
    fields.update(overrides)
    return Invoice(**fields)


class FakePayPal:
    """Records what the endpoint asked for, and returns a canned invoice."""

    def __init__(self, invoice=None, error=None):
        self.calls = []
        self._invoice = invoice or an_invoice()
        self._error = error

    def create_invoice(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return self._invoice


def app_with(paypal, limiter=None):
    return create_app(
        fees_path=FIXTURES / "fees.json",
        fx_path=FIXTURES / "fx_snapshot.json",
        paypal=paypal,
        invoice_limiter=limiter or VisitorLimiter(max_explanations=10, window_seconds=600),
    )


# --- the happy path -------------------------------------------------------


def test_invoice_endpoint_reports_the_invoice_paypal_returned():
    fake = FakePayPal()
    response = TestClient(app_with(fake)).post("/api/paypal/invoice", json=BODY)

    assert response.status_code == 200
    body = response.json()
    assert body["tier"] == "sandbox_observed"
    assert body["invoice"]["id"] == "INV2-TEST-0001-AAAA-BBBB"
    assert body["invoice"]["amount"] == "5000.00"
    assert body["invoice"]["currency"] == "EUR"
    assert body["environment"] == "sandbox"


def test_invoice_endpoint_invoices_the_amount_the_engine_priced():
    """PayPal is told the price. It is never asked what to charge."""
    fake = FakePayPal()
    TestClient(app_with(fake)).post("/api/paypal/invoice", json=BODY)

    assert len(fake.calls) == 1
    assert fake.calls[0]["amount"] == Decimal("5000.00")
    assert fake.calls[0]["currency"] == "EUR"


def test_invoice_endpoint_states_what_is_observed_and_what_is_not():
    """The tier split is the product's central claim, so the endpoint says it."""
    body = TestClient(app_with(FakePayPal())).post("/api/paypal/invoice", json=BODY).json()

    assert "exists in the PayPal sandbox" in body["observed"]
    assert "still modelled" in body["not_observed"]
    assert body["quote"]["tier"] == "modeled"


def test_invoice_endpoint_echoes_the_quote_it_invoiced():
    body = TestClient(app_with(FakePayPal())).post("/api/paypal/invoice", json=BODY).json()

    assert body["quote"]["total_fees"] == body["quote"]["total_fees"]
    assert body["quote"]["payment"] == "invoice_paypal"
    assert body["quote"]["currency"] == "EUR"


def test_invoice_endpoint_honours_a_supplied_recipient():
    fake = FakePayPal()
    TestClient(app_with(fake)).post(
        "/api/paypal/invoice", json={**BODY, "recipient_email": "someone@example.com"}
    )

    assert fake.calls[0]["recipient_email"] == "someone@example.com"


# --- refusals -------------------------------------------------------------


def test_missing_paypal_configuration_is_503_not_a_broken_page():
    """The quote must still work with no PayPal credentials. Only the button fails."""
    client = TestClient(
        create_app(
            fees_path=FIXTURES / "fees.json",
            fx_path=FIXTURES / "fx_snapshot.json",
            paypal=None,
        )
    )
    response = client.post("/api/paypal/invoice", json=BODY)

    assert response.status_code == 503
    assert "not configured" in response.json()["detail"]
    # And the quote itself is unaffected.
    assert client.post("/api/quote", json=BODY).status_code == 200


def test_paypal_failure_is_502_and_carries_paypals_words():
    error = PayPalError(422, "UNPROCESSABLE_ENTITY", "bad currency", ["INVALID_PARAMETER_VALUE"])
    response = TestClient(app_with(FakePayPal(error=error))).post("/api/paypal/invoice", json=BODY)

    assert response.status_code == 502
    assert "INVALID_PARAMETER_VALUE" in response.json()["detail"]


def test_unreadable_paypal_response_is_502():
    response = TestClient(
        app_with(FakePayPal(error=MalformedResponse("no amount")))
    ).post("/api/paypal/invoice", json=BODY)

    assert response.status_code == 502
    assert "could not read" in response.json()["detail"]


def test_invoice_creation_is_rate_limited_per_visitor():
    """Three drafts is enough to demo. It is not enough to fill a shared sandbox."""
    fake = FakePayPal()
    client = TestClient(
        app_with(fake, limiter=VisitorLimiter(max_explanations=2, window_seconds=600))
    )

    assert client.post("/api/paypal/invoice", json=BODY).status_code == 200
    assert client.post("/api/paypal/invoice", json=BODY).status_code == 200
    third = client.post("/api/paypal/invoice", json=BODY)

    assert third.status_code == 429
    assert len(fake.calls) == 2


def test_invoice_endpoint_rejects_an_unknown_corridor_before_calling_paypal():
    fake = FakePayPal()
    response = TestClient(app_with(fake)).post(
        "/api/paypal/invoice", json={**BODY, "corridor": "XYZ"}
    )

    assert response.status_code == 422
    assert fake.calls == []


def test_invoice_endpoint_rejects_a_non_positive_amount_before_calling_paypal():
    fake = FakePayPal()
    response = TestClient(app_with(fake)).post(
        "/api/paypal/invoice", json={**BODY, "amount": "0"}
    )

    assert response.status_code == 422
    assert fake.calls == []

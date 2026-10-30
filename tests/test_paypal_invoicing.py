"""The Invoicing call, tested against a recorded sandbox response.

This is the only part of FeeLens that makes PayPal create something, so it is
the only part where "we use the PayPal API" is a claim that can be checked. The
tests here prove the client sends what it says it sends and refuses to report an
invoice it did not read back.

Nothing here reaches the network. `httpx.MockTransport` stands in for PayPal.
The response shapes are recorded from a live sandbox call on 2026-10-30, with
the id and timestamps replaced.

What this does NOT prove: that PayPal's production assessment matches the
model. Creating an invoice does not charge it, so every fee stays `modeled`.
"""

import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from app.paypal import Invoice, MalformedResponse, PayPalClient, PayPalError

FIXTURES = Path(__file__).parent / "fixtures" / "paypal"


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def token_ok():
    return httpx.Response(200, json=fixture("token_response.json"))


def created_ok():
    return httpx.Response(201, json=fixture("invoice_create_response.json"))


def fetched_ok(**overrides):
    body = fixture("invoice_created.json")
    body.pop("_meta", None)
    body.update(overrides)
    return httpx.Response(200, json=body)


class Recorder:
    def __init__(self, responses):
        self.requests = []
        self.responses = list(responses)

    def __call__(self, request):
        self.requests.append(request)
        return self.responses.pop(0)


def make_client(handler):
    return PayPalClient(
        client_id="test-client-id",
        client_secret="test-secret",
        transport=httpx.MockTransport(handler),
    )


def create(recorder, **kwargs):
    client = make_client(recorder)
    defaults = {
        "amount": Decimal("5000.00"),
        "currency": "EUR",
        "recipient_email": "sb-buyer@business.example.com",
    }
    defaults.update(kwargs)
    return client.create_invoice(**defaults)


# --- the happy path -------------------------------------------------------


def test_create_invoice_returns_a_read_back_invoice():
    """The returned object must be the one PayPal sent, not the one we asked for."""
    recorder = Recorder([token_ok(), created_ok(), fetched_ok()])
    invoice = create(recorder)

    assert isinstance(invoice, Invoice)
    assert invoice.id == "INV2-TEST-0001-AAAA-BBBB"
    assert invoice.status == "DRAFT"
    assert invoice.amount == Decimal("5000.00")
    assert invoice.currency == "EUR"
    assert invoice.tier == "sandbox_observed"
    assert invoice.recipient_view_url.startswith("https://www.sandbox.paypal.com/")


def test_create_invoice_sends_the_amount_the_engine_priced():
    recorder = Recorder([token_ok(), created_ok(), fetched_ok()])
    create(recorder, amount=Decimal("1234.56"), currency="GBP")

    sent = json.loads(recorder.requests[1].content)
    assert sent["detail"]["currency_code"] == "GBP"
    assert sent["items"][0]["unit_amount"] == {"currency_code": "GBP", "value": "1234.56"}
    assert sent["primary_recipients"][0]["billing_info"]["email_address"] == (
        "sb-buyer@business.example.com"
    )


def test_create_invoice_is_a_draft_and_is_never_sent():
    """A button press must not email a buyer. Sending is a separate, deliberate call."""
    recorder = Recorder([token_ok(), created_ok(), fetched_ok()])
    create(recorder)

    paths = [request.url.path for request in recorder.requests]
    assert paths == [
        "/v1/oauth2/token",
        "/v2/invoicing/invoices",
        "/v2/invoicing/invoices/INV2-TEST-0001-AAAA-BBBB",
    ]
    assert not any(path.endswith("/send") for path in paths)


def test_invoice_number_is_left_to_paypal_by_default():
    """A shared sandbox has many visitors; PayPal's auto-increment cannot collide."""
    recorder = Recorder([token_ok(), created_ok(), fetched_ok()])
    create(recorder)

    sent = json.loads(recorder.requests[1].content)
    assert "invoice_number" not in sent["detail"]


def test_invoice_number_is_sent_when_supplied():
    recorder = Recorder([token_ok(), created_ok(), fetched_ok()])
    create(recorder, invoice_number="FEELENS-0001")

    sent = json.loads(recorder.requests[1].content)
    assert sent["detail"]["invoice_number"] == "FEELENS-0001"


# --- refusing to report what it did not receive ---------------------------


def test_create_response_without_a_usable_id_is_rejected():
    """A 2xx with no id is not an invoice. Guessing one would be worse than failing."""
    recorder = Recorder([token_ok(), httpx.Response(201, json={"rel": "self", "href": ""})])

    with pytest.raises(MalformedResponse) as raised:
        create(recorder)

    assert "invoice id" in str(raised.value)


def test_create_response_with_a_foreign_id_prefix_is_rejected():
    recorder = Recorder(
        [
            token_ok(),
            httpx.Response(
                201,
                json={"rel": "self", "href": "https://api.sandbox.paypal.com/v2/invoicing/invoices/abc"},
            ),
        ]
    )

    with pytest.raises(MalformedResponse):
        create(recorder)


def test_invoice_without_an_amount_is_rejected():
    """An invoice whose value we cannot read is not evidence of the value."""
    recorder = Recorder([token_ok(), created_ok(), fetched_ok(amount={})])

    with pytest.raises(MalformedResponse) as raised:
        create(recorder)

    assert "amount" in str(raised.value)


def test_paypal_error_is_raised_with_its_issue_codes():
    recorder = Recorder(
        [
            token_ok(),
            httpx.Response(
                422,
                json={
                    "name": "UNPROCESSABLE_ENTITY",
                    "message": "The requested action could not be performed.",
                    "details": [{"issue": "INVALID_PARAMETER_VALUE", "description": "bad currency"}],
                },
            ),
        ]
    )

    with pytest.raises(PayPalError) as raised:
        create(recorder)

    assert raised.value.status == 422
    assert "INVALID_PARAMETER_VALUE" in str(raised.value)


# --- the model ------------------------------------------------------------


def test_invoice_parses_the_recipient_view_url():
    """The URL is what a judge can click. Losing it loses the demo."""
    body = fixture("invoice_created.json")
    body.pop("_meta", None)
    invoice = Invoice.from_create(body)

    assert invoice.recipient_view_url == body["detail"]["metadata"]["recipient_view_url"]
    assert invoice.invoicer_view_url == body["detail"]["metadata"]["invoicer_view_url"]


def test_invoice_falls_back_to_the_requested_number_when_paypal_omits_one():
    body = fixture("invoice_created.json")
    body.pop("_meta", None)
    body["detail"].pop("invoice_number", None)

    invoice = Invoice.from_create(body, fallback_number="FEELENS-0001")

    assert invoice.invoice_number == "FEELENS-0001"

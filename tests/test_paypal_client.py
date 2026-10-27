"""The PayPal client, tested against recorded sandbox responses.

Nothing here reaches the network. `httpx.MockTransport` stands in for PayPal, so
the tests exercise real response shapes without a sandbox round-trip and without
a buyer clicking approve.

What this does NOT prove: that a live capture returns these numbers. That needs
the buyer approval step, and until it happens every calibration figure is still
`documented`, not `observed`.
"""

import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from app.paypal import (
    MalformedResponse,
    PayPalClient,
    PayPalError,
    reconcile,
)

FIXTURES = Path(__file__).parent / "fixtures" / "paypal"


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def make_client(handler, clock=None):
    kwargs = {"transport": httpx.MockTransport(handler)}
    if clock is not None:
        kwargs["clock"] = clock
    return PayPalClient(
        client_id="test-client-id",
        client_secret="test-secret",
        **kwargs,
    )


def token_ok():
    return httpx.Response(200, json=fixture("token_response.json"))


class Recorder:
    """Records every request so assertions can read what was actually sent."""

    def __init__(self, responses):
        self.requests = []
        self.responses = list(responses)

    def __call__(self, request):
        self.requests.append(request)
        return self.responses.pop(0)


# --- token handling -------------------------------------------------------


def test_token_is_fetched_once_and_reused():
    recorder = Recorder([token_ok(), token_ok()])
    client = make_client(recorder)

    client.token()
    client.token()

    assert len(recorder.requests) == 1
    assert recorder.requests[0].url.path == "/v1/oauth2/token"


def test_token_is_refreshed_after_it_expires():
    """The recorded fixture says expires_in=32400, so the boundary is ~32_400s."""
    now = [0.0]
    recorder = Recorder([token_ok(), token_ok()])
    client = make_client(recorder, clock=lambda: now[0])

    client.token()
    now[0] = 32_500.0
    client.token()

    assert len(recorder.requests) == 2


def test_token_is_not_refreshed_while_still_valid():
    now = [0.0]
    recorder = Recorder([token_ok(), token_ok()])
    client = make_client(recorder, clock=lambda: now[0])

    client.token()
    now[0] = 30_000.0
    client.token()

    assert len(recorder.requests) == 1


def test_token_request_uses_basic_auth_and_client_credentials_grant():
    recorder = Recorder([token_ok()])
    make_client(recorder).token()

    request = recorder.requests[0]
    assert request.headers["authorization"].startswith("Basic ")
    assert b"grant_type=client_credentials" in request.content


def test_bad_credentials_raise_with_paypals_own_message():
    def handler(_request):
        return httpx.Response(
            401,
            json={
                "error": "invalid_client",
                "error_description": "Client Authentication failed",
            },
        )

    with pytest.raises(PayPalError) as raised:
        make_client(handler).token()

    assert raised.value.status == 401
    assert "invalid_client" in str(raised.value)


def test_client_refuses_a_live_base_url_without_an_explicit_opt_in():
    """FeeLens has no business creating orders against a real account."""
    with pytest.raises(ValueError) as raised:
        PayPalClient(
            client_id="test-client-id",
            client_secret="test-secret",
            base_url="https://api-m.paypal.com",
        )

    assert "allow_live" in str(raised.value)


def test_client_accepts_the_sandbox_host():
    client = PayPalClient(
        client_id="test-client-id",
        client_secret="test-secret",
        base_url="https://api-m.sandbox.paypal.com",
    )

    assert client.base_url == "https://api-m.sandbox.paypal.com"


# --- create order ---------------------------------------------------------


def test_create_order_sends_the_invoice_amount_in_the_invoice_currency():
    recorder = Recorder([token_ok(), httpx.Response(201, json=fixture("order_created.json"))])
    client = make_client(recorder)

    client.create_order(amount=Decimal("5000.00"), currency="EUR")

    body = json.loads(recorder.requests[1].content)
    assert body["intent"] == "CAPTURE"
    assert body["purchase_units"][0]["amount"] == {
        "currency_code": "EUR",
        "value": "5000.00",
    }


def test_create_order_sends_an_idempotency_key():
    recorder = Recorder([token_ok(), httpx.Response(201, json=fixture("order_created.json"))])
    make_client(recorder).create_order(amount=Decimal("5000.00"), currency="EUR")

    assert recorder.requests[1].headers.get("paypal-request-id")


def test_approval_url_is_found_by_href_not_by_rel():
    """PayPal's docs disagree about rel ('approve' vs 'payer-action').

    The recorded sandbox response says 'approve'; the create-order example in the
    docs says 'payer-action'. Matching the href makes the parser immune to both.
    """
    body = fixture("order_created.json")
    for link in body["links"]:
        if "checkoutnow" in link["href"]:
            link["rel"] = "payer-action"

    recorder = Recorder([token_ok(), httpx.Response(201, json=body)])
    order = make_client(recorder).create_order(amount=Decimal("5000.00"), currency="EUR")

    assert "checkoutnow" in order.approve_url


def test_order_without_an_approval_link_fails_loudly():
    body = fixture("order_created.json")
    body["links"] = [link for link in body["links"] if "checkoutnow" not in link["href"]]

    recorder = Recorder([token_ok(), httpx.Response(201, json=body)])

    with pytest.raises(PayPalError) as raised:
        make_client(recorder).create_order(amount=Decimal("5000.00"), currency="EUR")

    assert "approval" in str(raised.value).lower()


# --- capture --------------------------------------------------------------


def test_capture_reads_paypals_own_breakdown():
    recorder = Recorder(
        [token_ok(), httpx.Response(201, json=fixture("capture_cross_currency.json"))]
    )
    client = make_client(recorder)

    calibration = client.capture_order("9AB12345CD678901E")

    assert calibration.gross.value == Decimal("5000.00")
    assert calibration.gross.currency == "EUR"
    assert calibration.paypal_fee.value == Decimal("250.00")
    assert calibration.net.value == Decimal("4750.00")
    assert calibration.receivable.value == Decimal("5225.00")
    assert calibration.receivable.currency == "USD"
    assert calibration.exchange_rate == Decimal("1.100000")
    assert calibration.tier == "sandbox_observed"


def test_same_currency_capture_has_no_receivable_amount():
    recorder = Recorder(
        [token_ok(), httpx.Response(201, json=fixture("capture_same_currency.json"))]
    )
    calibration = make_client(recorder).capture_order("5O190127TN364715T")

    assert calibration.receivable is None
    assert calibration.exchange_rate is None
    assert calibration.net.value == Decimal("4750.00")


def test_capture_before_the_buyer_approves_explains_itself():
    def handler(request):
        if request.url.path == "/v1/oauth2/token":
            return token_ok()
        return httpx.Response(
            422,
            json={
                "name": "UNPROCESSABLE_ENTITY",
                "message": "The requested action could not be performed.",
                "details": [
                    {
                        "issue": "ORDER_NOT_APPROVED",
                        "description": "Payer has not yet approved the Order for payment.",
                    }
                ],
            },
        )

    with pytest.raises(PayPalError) as raised:
        make_client(handler).capture_order("54C27001MP346874K")

    message = str(raised.value)
    assert "ORDER_NOT_APPROVED" in message
    assert "approve" in message.lower()


def test_capture_response_without_a_breakdown_is_malformed_not_silent():
    body = fixture("capture_same_currency.json")
    del body["purchase_units"][0]["payments"]["captures"][0]["seller_receivable_breakdown"]

    recorder = Recorder([token_ok(), httpx.Response(201, json=body)])

    with pytest.raises(MalformedResponse) as raised:
        make_client(recorder).capture_order("5O190127TN364715T")

    assert "seller_receivable_breakdown" in str(raised.value)


# --- reconciliation -------------------------------------------------------


def test_reconcile_flags_a_model_that_matches_paypal():
    recorder = Recorder(
        [token_ok(), httpx.Response(201, json=fixture("capture_cross_currency.json"))]
    )
    calibration = make_client(recorder).capture_order("9AB12345CD678901E")

    result = reconcile(modeled_fee=Decimal("250.00"), modeled_currency="EUR", observed=calibration)

    assert result.difference == Decimal("0.00")
    assert result.verdict == "matches_model"


def test_reconcile_says_which_direction_the_model_is_wrong():
    recorder = Recorder(
        [token_ok(), httpx.Response(201, json=fixture("capture_cross_currency.json"))]
    )
    calibration = make_client(recorder).capture_order("9AB12345CD678901E")

    under = reconcile(modeled_fee=Decimal("200.00"), modeled_currency="EUR", observed=calibration)
    over = reconcile(modeled_fee=Decimal("300.00"), modeled_currency="EUR", observed=calibration)

    assert under.verdict == "model_understates"
    assert over.verdict == "model_overstates"
    assert under.difference == Decimal("50.00")
    assert over.difference == Decimal("-50.00")


def test_reconcile_refuses_to_compare_across_currencies():
    recorder = Recorder(
        [token_ok(), httpx.Response(201, json=fixture("capture_cross_currency.json"))]
    )
    calibration = make_client(recorder).capture_order("9AB12345CD678901E")

    with pytest.raises(ValueError) as raised:
        reconcile(modeled_fee=Decimal("250.00"), modeled_currency="USD", observed=calibration)

    assert "USD" in str(raised.value) and "EUR" in str(raised.value)

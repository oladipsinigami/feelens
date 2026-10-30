"""Thin PayPal REST client.

Deliberately thin: no SDK, no retry policy, no abstraction layer that hides the
JSON. FeeLens needs a handful of calls — authenticate, create an order, capture
it, create an invoice — and every number that matters comes straight out of
PayPal's own response.

The invoicing call is the one the deployed app actually uses: a modelled fee is
a claim, but an invoice that exists in PayPal with that amount on it is a fact,
and the difference is the whole reason the three tiers exist.

Two safety properties are enforced here rather than left to callers:

- The client will not talk to the live host unless `allow_live=True` is passed
  explicitly. Every default path in FeeLens is sandbox.
- A 2xx response missing a field we cannot compute without raises
  `MalformedResponse` instead of yielding a zero. A silently-zeroed fee is
  worse than a crash, because it looks like an answer.

Docs:
- OAuth:     https://developer.paypal.com/api/rest/authentication/
- Orders:    https://developer.paypal.com/docs/api/orders/v2/
- Invoicing: https://developer.paypal.com/docs/api/invoicing/v2/
"""

from __future__ import annotations

import os
import time
import uuid
from decimal import ROUND_HALF_UP, Decimal

import httpx

from .models import Calibration, Invoice, MalformedResponse, Order, PayPalError

SANDBOX_BASE_URL = "https://api-m.sandbox.paypal.com"
LIVE_BASE_URL = "https://api-m.paypal.com"

# Refresh a minute early: a token that expires mid-flight turns into a 401 that
# looks like a credentials problem.
TOKEN_SKEW_SECONDS = 60

_CENT = Decimal("0.01")

# PayPal's issue codes are precise but terse. These turn the ones FeeLens will
# actually hit into something a human can act on.
_ISSUE_HINTS = {
    "ORDER_NOT_APPROVED": (
        "the buyer has not approved this order in the browser yet — open the "
        "approve URL, log in as a sandbox personal account, then capture"
    ),
    "ORDER_EXPIRED": "this order expired — create a new one",
    "INSTRUMENT_DECLINED": "the sandbox buyer's funding source was declined",
    "ORDER_ALREADY_CAPTURED": "this order was already captured; reuse that calibration",
}


class PayPalClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        base_url: str | None = None,
        transport: httpx.BaseTransport | None = None,
        clock=None,
        timeout: float = 30.0,
        allow_live: bool = False,
    ):
        resolved = (base_url or os.getenv("PAYPAL_BASE_URL") or SANDBOX_BASE_URL).rstrip("/")
        if "sandbox" not in resolved and not allow_live:
            raise ValueError(
                f"refusing to use base_url {resolved}: it is not a sandbox host. "
                "Pass allow_live=True if you genuinely mean to move real money."
            )

        self.base_url = resolved
        self._client_id = client_id
        self._client_secret = client_secret
        self._clock = clock or time.monotonic
        self._http = httpx.Client(
            base_url=resolved,
            transport=transport,
            timeout=timeout,
        )
        self._token: str | None = None
        self._token_expires_at = 0.0

    # --- lifecycle --------------------------------------------------------

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "PayPalClient":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # --- auth -------------------------------------------------------------

    def token(self) -> str:
        """Return a bearer token, reusing it until shortly before it expires."""
        now = self._clock()
        if self._token is not None and now < self._token_expires_at:
            return self._token

        response = self._http.post(
            "/v1/oauth2/token",
            data={"grant_type": "client_credentials"},
            auth=(self._client_id, self._client_secret),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if response.status_code != 200:
            raise self._error(response)

        body = response.json()
        access_token = body.get("access_token")
        if not access_token:
            raise MalformedResponse("token response has no access_token")

        expires_in = body.get("expires_in")
        if not isinstance(expires_in, int):
            # Caching an unknown lifetime risks using a dead token later.
            raise MalformedResponse("token response has no integer expires_in")

        self._token = access_token
        self._token_expires_at = now + max(expires_in - TOKEN_SKEW_SECONDS, 0)
        return self._token

    # --- orders -----------------------------------------------------------

    def create_order(self, amount: Decimal, currency: str, description: str = "FeeLens sandbox calibration") -> Order:
        payload = {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "reference_id": "feelens-calibration",
                    "description": description,
                    "amount": {
                        "currency_code": currency,
                        "value": _amount(amount),
                    },
                }
            ],
            "application_context": {
                "brand_name": "FeeLens",
                "user_action": "PAY_NOW",
                "shipping_preference": "NO_SHIPPING",
            },
        }
        response = self._http.post(
            "/v2/checkout/orders",
            json=payload,
            headers=self._auth_headers(),
        )
        if response.status_code not in (200, 201):
            raise self._error(response)

        body = response.json()
        order_id = body.get("id")
        if not order_id:
            raise MalformedResponse("create-order response has no id")

        return Order(
            id=order_id,
            status=body.get("status", ""),
            approve_url=self._approve_url(body),
            raw=body,
        )

    def capture_order(self, order_id: str) -> Calibration:
        response = self._http.post(
            f"/v2/checkout/orders/{order_id}/capture",
            headers=self._auth_headers(),
        )
        if response.status_code not in (200, 201):
            raise self._error(response)

        return Calibration.from_capture(response.json())

    # --- invoicing --------------------------------------------------------

    def create_invoice(
        self,
        amount: Decimal,
        currency: str,
        recipient_email: str,
        invoice_number: str = "",
        item_name: str = "Freelance services",
        note: str = "Created by FeeLens from a modelled fee quote.",
    ) -> Invoice:
        """Create a draft invoice in PayPal and read it back.

        Two calls, deliberately. `POST /v2/invoicing/invoices` returns only a
        `self` link — no id, no amount, no buyer URL — so the invoice has to be
        read back before it is evidence of anything. Returning the create
        response would let the app display an invoice number it never actually
        received.

        A draft is created, not sent. Sending would email a real (sandbox)
        buyer, and a demo that emails strangers on a button press is not a demo
        anyone should ship.
        """
        payload = {
            "detail": {
                "currency_code": currency,
                "note": note,
                "payment_term": {"term_type": "DUE_ON_RECEIPT"},
            },
            "invoicer": {"business_name": "FeeLens"},
            "primary_recipients": [
                {"billing_info": {"email_address": recipient_email}}
            ],
            "items": [
                {
                    "name": item_name,
                    "quantity": "1",
                    "unit_amount": {
                        "currency_code": currency,
                        "value": _amount(amount),
                    },
                }
            ],
        }
        if invoice_number:
            # Left to PayPal when absent: its auto-increment cannot collide with
            # another visitor's invoice, and a shared sandbox has many visitors.
            payload["detail"]["invoice_number"] = invoice_number

        created = self._http.post(
            "/v2/invoicing/invoices",
            json=payload,
            headers=self._auth_headers(),
        )
        if created.status_code not in (200, 201):
            raise self._error(created)

        invoice_id = _id_from_self_link(created)
        fetched = self._http.get(
            f"/v2/invoicing/invoices/{invoice_id}",
            headers={"Authorization": f"Bearer {self.token()}"},
        )
        if fetched.status_code != 200:
            raise self._error(fetched)

        return Invoice.from_create(fetched.json(), fallback_number=invoice_number)

    # --- internals --------------------------------------------------------

    def _auth_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.token()}",
            "Content-Type": "application/json",
            "PayPal-Request-Id": str(uuid.uuid4()),
        }

    @staticmethod
    def _approve_url(body: dict) -> str:
        """Find the buyer-facing URL by href, not by rel.

        The create-order docs example labels this link `payer-action`; the live
        sandbox response labels it `approve`. Matching `checkoutnow` in the href
        works against both, and against whatever they rename it to next.
        """
        for link in body.get("links") or []:
            href = link.get("href", "")
            if "checkoutnow" in href:
                return href
        raise PayPalError(
            status=200,
            name="NO_APPROVAL_LINK",
            message=(
                "order was created but carries no approval link, so a buyer "
                "cannot approve it"
            ),
            issues=[f"links returned: {[link.get('rel') for link in body.get('links') or []]}"],
        )

    @staticmethod
    def _error(response: httpx.Response) -> PayPalError:
        try:
            body = response.json()
        except ValueError:
            return PayPalError(
                status=response.status_code,
                name="NON_JSON_ERROR",
                message=response.text[:500] or "empty error body",
            )

        # Auth failures use error/error_description; API failures use name/message.
        name = body.get("name") or body.get("error") or "HTTP_ERROR"
        message = body.get("message") or body.get("error_description") or ""

        issues = []
        hints = []
        for detail in body.get("details") or []:
            issue = detail.get("issue", "")
            description = detail.get("description", "")
            issues.append(f"{issue}: {description}".strip(": "))
            if issue in _ISSUE_HINTS:
                hints.append(_ISSUE_HINTS[issue])

        return PayPalError(
            status=response.status_code,
            name=name,
            message=" ".join(part for part in (message, *hints) if part),
            issues=issues,
        )


def _amount(amount: Decimal) -> str:
    """PayPal wants a decimal string with the currency's precision, never a float."""
    return str(Decimal(amount).quantize(_CENT, rounding=ROUND_HALF_UP))


def _id_from_self_link(response: httpx.Response) -> str:
    """Pull the invoice id out of the create response's `self` href.

    The create endpoint answers with `{"rel": "self", "href": ".../invoices/INV2-..."}`
    rather than the invoice itself. The id is the last path segment; anything
    else means the response is not the shape this client was written for, and
    guessing would be worse than failing.
    """
    try:
        href = (response.json() or {}).get("href") or ""
    except ValueError:
        raise MalformedResponse("create-invoice response was not JSON")

    invoice_id = href.rstrip("/").rsplit("/", 1)[-1]
    if not invoice_id.startswith("INV2-"):
        raise MalformedResponse(
            f"create-invoice response has no usable invoice id (href={href!r})"
        )
    return invoice_id


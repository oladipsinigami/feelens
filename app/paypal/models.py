"""Types returned by the PayPal client.

The distinction that matters here: a `Calibration` carries PayPal's own numbers.
It is the only place in FeeLens where a figure is not modelled, so it carries a
tier that says exactly that and nothing in the UI may present it as anything
else.
"""

from dataclasses import dataclass, field
from decimal import Decimal

SANDBOX_OBSERVED = "sandbox_observed"


class PayPalError(RuntimeError):
    """PayPal returned an error status. Carries the issue codes, not just a code."""

    def __init__(self, status, name, message, issues=None):
        self.status = status
        self.name = name
        self.message = message
        self.issues = list(issues or [])
        super().__init__(self._render())

    def _render(self):
        head = f"PayPal {self.status} {self.name}: {self.message}".strip()
        if self.issues:
            head += " [" + "; ".join(self.issues) + "]"
        return head


class MalformedResponse(ValueError):
    """A 2xx response that is missing a field we cannot compute without."""


@dataclass(frozen=True)
class Money:
    value: Decimal
    currency: str


@dataclass(frozen=True)
class Order:
    id: str
    status: str
    approve_url: str
    raw: dict


@dataclass(frozen=True)
class Invoice:
    """A real PayPal invoice, created through the Invoicing API.

    This is the one object FeeLens produces that exists inside PayPal. Its
    `tier` is `sandbox_observed` because the *invoice* was observed — PayPal
    returned an id and a page a buyer can open. The *fee* on that invoice is
    still modelled, and the payload must say so. Confusing the two would be the
    exact dishonesty the three tiers exist to prevent.
    """

    id: str
    status: str
    invoice_number: str
    amount: Decimal
    currency: str
    recipient_view_url: str
    invoicer_view_url: str
    create_time: str
    tier: str = SANDBOX_OBSERVED
    raw: dict = field(default_factory=dict, compare=False)

    @classmethod
    def from_create(cls, body: dict, fallback_number: str = "") -> "Invoice":
        """Parse a create/get invoice response.

        `POST /v2/invoicing/invoices` returns only a self link, so the caller
        re-reads the invoice and passes that body here. Both shapes are accepted
        because the difference is a PayPal implementation detail, not ours.
        """
        invoice_id = body.get("id")
        if not invoice_id:
            raise MalformedResponse("invoice response has no id")

        detail = body.get("detail") or {}
        amount = body.get("amount") or {}
        currency = amount.get("currency_code") or detail.get("currency_code")
        value = amount.get("value")
        if not currency or value is None:
            raise MalformedResponse(
                "invoice response has no amount/currency_code; an invoice whose "
                "value we cannot read is not evidence of anything"
            )

        metadata = detail.get("metadata") or {}
        recipient_url = metadata.get("recipient_view_url") or ""
        invoicer_url = metadata.get("invoicer_view_url") or ""

        return cls(
            id=invoice_id,
            status=body.get("status", ""),
            invoice_number=detail.get("invoice_number") or fallback_number,
            amount=Decimal(str(value)),
            currency=currency,
            recipient_view_url=recipient_url,
            invoicer_view_url=invoicer_url,
            create_time=metadata.get("create_time", ""),
            raw=body,
        )


@dataclass(frozen=True)
class Calibration:
    order_id: str
    capture_id: str
    status: str
    gross: Money
    paypal_fee: Money
    net: Money
    receivable: Money | None = None
    exchange_rate: Decimal | None = None
    tier: str = SANDBOX_OBSERVED

    @classmethod
    def from_capture(cls, body: dict) -> "Calibration":
        units = body.get("purchase_units") or []
        if not units:
            raise MalformedResponse("capture response has no purchase_units")

        captures = (units[0].get("payments") or {}).get("captures") or []
        if not captures:
            raise MalformedResponse("capture response has no completed captures")

        capture = captures[0]
        breakdown = capture.get("seller_receivable_breakdown")
        if not breakdown:
            raise MalformedResponse(
                "capture has no seller_receivable_breakdown, so there is nothing "
                "to calibrate against"
            )
        for key in ("gross_amount", "net_amount"):
            if key not in breakdown:
                raise MalformedResponse(
                    f"capture seller_receivable_breakdown is missing {key}"
                )

        rate = breakdown.get("exchange_rate")
        return cls(
            order_id=body.get("id", ""),
            capture_id=capture.get("id", ""),
            status=body.get("status", ""),
            gross=_money(breakdown["gross_amount"]),
            paypal_fee=_money(breakdown.get("paypal_fee") or {"value": "0", "currency_code": breakdown["gross_amount"]["currency_code"]}),
            net=_money(breakdown["net_amount"]),
            receivable=_money(breakdown["receivable_amount"]) if breakdown.get("receivable_amount") else None,
            exchange_rate=Decimal(rate["value"]) if rate and rate.get("value") else None,
        )


def _money(node: dict) -> Money:
    return Money(value=Decimal(node["value"]), currency=node["currency_code"])

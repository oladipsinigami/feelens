"""The only functions the explanation agent may call.

The agent gets no arithmetic. Every figure it is able to utter arrives through
one of these tools, already computed by `app.engine`. There is deliberately no
`calculate` tool and no raw fee table in the prompt: if the model could see
`3.49` and `1.50` it would eventually add them, and the whole premise of this
project is that it must not.

Each tool returns a JSON-safe dict. A tool that cannot answer returns
`{"error": ...}` rather than a guess, so the model has something true to say
instead of a plausible number.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from app.corridors import (
    ACCOUNT_TYPE,
    CORRIDORS,
    DISCLAIMER,
    PAYMENT_OPTIONS,
    RECEIVE_CURRENCY,
    RECEIVE_REGION,
    corridor as find_corridor,
    payment_option as find_payment,
)
from app.engine import compare_paths
from app.engine.models import (
    MODELLED,
    Transaction,
    UnsupportedCombinationError,
    UnsupportedCurrencyError,
)
from app.serialize import quote_payload

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_quote",
            "description": (
                "Price one invoice through one payment product. Returns the fee "
                "lines, totals, the cheaper alternative if one exists, and the "
                "assumptions. This is the only source of numbers. Call it before "
                "stating any figure."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "amount": {
                        "type": "string",
                        "description": "Invoice amount as a decimal string, e.g. '5000.00'.",
                    },
                    "corridor": {
                        "type": "string",
                        "description": "Invoice currency, one of EUR, GBP, PHP, AUD, INR.",
                    },
                    "payment": {
                        "type": "string",
                        "description": (
                            "One of 'direct' (client sends money), "
                            "'invoice_paypal' (invoice paid from a PayPal account), "
                            "'invoice_card' (invoice paid by card or wallet)."
                        ),
                    },
                },
                "required": ["amount", "corridor", "payment"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_fee_rule",
            "description": (
                "Look up the published rule behind one fee line: its rate, its "
                "fixed fee by currency, what it applies to, when PayPal's page "
                "was last checked, and any modelling note. Use it when you need "
                "to explain why a line exists."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "rule_id": {
                        "type": "string",
                        "description": "A rule id from a get_quote result, e.g. 'invoice_checkout'.",
                    }
                },
                "required": ["rule_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_corridors",
            "description": (
                "List the client countries, currencies and payment products this "
                "build can price. Use it before suggesting a corridor that may "
                "not be covered."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_fx_snapshot",
            "description": (
                "The exchange-rate snapshot in use: its date, how old it is, "
                "whether it has crossed the staleness threshold, and the source. "
                "Use it whenever conversion is part of the answer."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


class FeeTools:
    """Tool implementations, bound to one loaded fee config and FX snapshot."""

    def __init__(self, fees, snapshot, disclaimer: str = DISCLAIMER):
        self._fees = fees
        self._snapshot = snapshot
        self._disclaimer = disclaimer
        self._cache: dict = {}
        self.calls: list = []

    # -- dispatch ---------------------------------------------------------

    def dispatch(self, name: str, arguments: dict) -> dict:
        """Run a tool by name. Never raises: the loop must survive a bad call."""
        arguments = arguments or {}
        handler = {
            "get_quote": self.get_quote,
            "get_fee_rule": self.get_fee_rule,
            "list_corridors": self.list_corridors,
            "get_fx_snapshot": self.get_fx_snapshot,
        }.get(name)

        self.calls.append({"name": name, "arguments": arguments})

        if handler is None:
            return {"error": f"no tool named {name!r}"}

        try:
            return handler(**arguments)
        except TypeError as exc:
            return {"error": f"bad arguments for {name}: {exc}"}

    # -- tools ------------------------------------------------------------

    def get_quote(self, amount: str, corridor: str, payment: str) -> dict:
        key = (str(amount), corridor, payment)
        if key in self._cache:
            return self._cache[key]

        try:
            chosen_corridor = find_corridor(corridor)
        except KeyError:
            return {
                "error": (
                    f"unknown corridor {corridor!r}. "
                    f"Known: {[c['invoice_currency'] for c in CORRIDORS]}"
                )
            }
        try:
            chosen_payment = find_payment(payment)
        except KeyError:
            return {
                "error": (
                    f"unknown payment product {payment!r}. "
                    f"Known: {[p['id'] for p in PAYMENT_OPTIONS]}"
                )
            }

        try:
            parsed = Decimal(str(amount).strip().replace(",", ""))
        except (InvalidOperation, ValueError):
            return {"error": f"amount {amount!r} is not a number"}
        if not parsed.is_finite() or parsed <= 0:
            return {"error": "amount must be a finite number greater than zero"}

        tx = Transaction(
            amount=parsed,
            invoice_currency=chosen_corridor["invoice_currency"],
            payer_currency=chosen_corridor["invoice_currency"],
            payer_region=chosen_corridor["client_country"],
            receive_currency=RECEIVE_CURRENCY,
            region=RECEIVE_REGION,
            account_type=ACCOUNT_TYPE,
            transaction_type=chosen_payment["transaction_type"],
            payment_method=chosen_payment["payment_method"],
        )

        try:
            comparison = compare_paths(tx, self._fees, self._snapshot)
        except (UnsupportedCurrencyError, UnsupportedCombinationError) as exc:
            return {"error": str(exc)}

        payload = quote_payload(
            comparison,
            amount=parsed,
            corridor=chosen_corridor,
            payment=chosen_payment,
            disclaimer=self._disclaimer,
            tier=MODELLED,
            transaction=tx,
        )
        self._cache[key] = payload
        return payload

    def get_fee_rule(self, rule_id: str) -> dict:
        for rule in self._fees.rules:
            if rule.id == rule_id:
                return {
                    "rule_id": rule.id,
                    "label": rule.label,
                    "type": rule.type,
                    "rate_pct": str(rule.rate_pct),
                    "fixed": {code: str(value) for code, value in rule.fixed.items()},
                    "applies_to": {
                        "regions": list(rule.regions),
                        "account_types": list(rule.account_types),
                        "payment_methods": list(rule.payment_methods),
                        "transaction_types": list(rule.transaction_types),
                        "conditions": list(rule.conditions),
                    },
                    "borne_by": rule.borne_by,
                    "source_url": rule.source_url,
                    "verified": rule.verified,
                    "retrieved_date": self._fees.retrieved_date,
                    "notes": rule.notes,
                }
        return {
            "error": (
                f"no rule {rule_id!r}. Known: {[r.id for r in self._fees.rules]}"
            )
        }

    def list_corridors(self) -> dict:
        return {
            "corridors": [
                {
                    "invoice_currency": item["invoice_currency"],
                    "client_country": item["client_country"],
                    "country": item["country"],
                    "currency_name": item["currency_name"],
                }
                for item in CORRIDORS
            ],
            "payment_products": [
                {
                    "id": item["id"],
                    "label": item["label"],
                    "description": item["description"],
                }
                for item in PAYMENT_OPTIONS
            ],
            "receive_currency": RECEIVE_CURRENCY,
            "receive_region": RECEIVE_REGION,
        }

    def get_fx_snapshot(self) -> dict:
        age = self._fx_age_days()
        return {
            "snapshot_date": self._snapshot["retrieved_date"],
            "base": self._snapshot.get("base"),
            "age_days": age,
            "stale": age > 4,
            "source": self._snapshot.get("source", ""),
            "source_urls": list(self._snapshot.get("source_urls", [])),
            "note": (
                "ECB reference rates. This is not a PayPal conversion rate; "
                "PayPal's own spread is priced separately as a fee line."
            ),
        }

    def _fx_age_days(self) -> int:
        from datetime import date

        retrieved = date.fromisoformat(self._snapshot["retrieved_date"])
        return (date.today() - retrieved).days

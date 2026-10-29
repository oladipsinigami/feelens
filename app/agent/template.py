"""The explanation FeeLens gives when no model is available.

This is not a degraded mode. It is the floor: the product must be able to
explain a quote with no API key, no network and no spend, and every sentence
here is assembled from figures the engine already produced. It also serves as
the fallback when the model's answer fails the guardrail, which is why it is
held to the same standard — `tests/test_template.py` runs it through the same
validator the model's output has to pass.

If this text and the model's text ever disagree about a number, the engine is
right and the model is wrong. That is the whole architecture in one sentence.
"""

from __future__ import annotations

from decimal import Decimal


def _group(value: str) -> str:
    """5148.85 -> 5,148.85, without turning the value into a float."""
    raw = str(value)
    sign = "-" if raw.startswith("-") else ""
    whole, _, fraction = raw.lstrip("-").partition(".")
    grouped = f"{int(whole):,}" if whole.isdigit() else whole
    return f"{sign}{grouped}.{fraction}" if fraction else f"{sign}{grouped}"


def _money(value: str, currency: str) -> str:
    return f"{_group(value)} {currency}"


def _is_zero(value) -> bool:
    """Compare as a decimal, never as a float — the whole point of the engine."""
    return Decimal(str(value)) == 0


def _charge(line: dict) -> str:
    fixed = line["fixed_amount"]
    if fixed and not _is_zero(fixed):
        return (
            f"{line['label']} at {_money(line['amount'], line['currency'])} plus "
            f"{_money(fixed, line['fixed_currency'])} fixed ({line['rule_id']})"
        )
    return f"{line['label']} at {_money(line['amount'], line['currency'])} ({line['rule_id']})"


def _join(items: list) -> str:
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} and {items[-1]}"


def template_explanation(quote: dict) -> dict:
    if "error" in quote and "current" not in quote:
        return {
            "explanation": str(quote["error"]),
            "cited_rules": [],
            "caveats": [],
        }

    current = quote["current"]
    corridor = quote["corridor"]
    payment = quote["payment"]

    invoice_currency = corridor["invoice_currency"]
    receive_currency = current["net_received_currency"]

    parts = [
        f"You are invoicing {_money(quote['amount'], invoice_currency)} and being "
        f"paid as {payment['spoken']}. PayPal keeps "
        f"{_money(current['total_fees'], receive_currency)} of the "
        f"{_money(current['gross_received'], receive_currency)} it collects, so you "
        f"receive {_money(current['net_received'], receive_currency)} — an effective "
        f"rate of {current['effective_rate_pct']}%."
    ]

    if current["lines"]:
        parts.append(f"The charges are {_join([_charge(line) for line in current['lines']])}.")

    if current["payer_lines"]:
        names = _join([line["rule_id"] for line in current["payer_lines"]])
        parts.append(
            "A separate conversion charge falls on your client and is not deducted "
            f"from your net ({names})."
        )

    if invoice_currency != receive_currency:
        age = current["fx_age_days"]
        parts.append(
            f"The conversion uses the ECB reference rate for "
            f"{current['fx_snapshot_date']}, which is {age} "
            f"{'day' if age == 1 else 'days'} old."
        )

    rail = quote.get("rail_comparison")
    if rail:
        parts.append(
            f"{rail['basis']} On this invoice it would take "
            f"{_money(rail['fee'], rail['currency'])} where PayPal takes "
            f"{_money(current['total_fees'], receive_currency)}, leaving "
            f"{_money(rail['net'], rail['currency'])} rather than "
            f"{_money(current['net_received'], receive_currency)}."
        )

    savings = quote["savings"]
    if savings and not _is_zero(savings):
        optimized = quote["optimized"]
        parts.append(
            f"Invoicing in {optimized['net_received_currency']} instead would leave "
            f"you {_money(optimized['net_received'], receive_currency)}, "
            f"{_money(savings, quote['savings_currency'])} more."
        )

        client = quote.get("client_side")
        if client and client.get("payer_converts") and not _is_zero(client["total"]):
            parts.append(
                "That saving is not free: paying a "
                f"{client['invoice_currency']} invoice from a "
                f"{client['payer_currency']} balance costs your client "
                f"{_money(client['total'], client['currency'])} in conversion "
                "spread, so the saving is a cost moved rather than removed."
            )

        net = quote.get("net_savings") or {}
        magnitude = net.get("magnitude", "0")
        if net.get("direction") == "worse_off" and not _is_zero(magnitude):
            parts.append(
                f"Net of that, the two of you are "
                f"{_money(magnitude, net['currency'])} worse off on this invoice."
            )
        elif net.get("direction") == "better_off" and not _is_zero(magnitude):
            parts.append(
                f"Even after that, the two of you are "
                f"{_money(magnitude, net['currency'])} better off on this invoice."
            )

        annual = quote.get("savings_if_monthly_for_a_year") or {}
        if annual and not _is_zero(annual.get("receiver_savings", "0")):
            savings_currency = quote.get("savings_currency", receive_currency)
            line = (
                f"If this invoice repeats monthly, that is "
                f"{_money(annual['receiver_savings'], savings_currency)} "
                "more for you over a year"
            )
            if not _is_zero(annual.get("client_cost", "0")):
                line += (
                    f" and {_money(annual['client_cost'], savings_currency)} "
                    "more for your client"
                )
            net_annual = annual.get("net") or {}
            if net_annual.get("direction") == "worse_off":
                line += (
                    f" — a net "
                    f"{_money(net_annual['magnitude'], net_annual['currency'])} "
                    "worse for the two of you"
                )
            elif net_annual.get("direction") == "better_off":
                line += (
                    f" — a net "
                    f"{_money(net_annual['magnitude'], net_annual['currency'])} "
                    "better for the two of you"
                )
            parts.append(line + ".")

    caveats = []
    payment_caveat = (quote.get("payment") or {}).get("caveat")
    if payment_caveat:
        caveats.append(payment_caveat)

    payer_notes = [line for line in quote["assumptions"] if "4.00%" in line]
    if not payer_notes:
        payer_notes = [line for line in quote["assumptions"] if "payer" in line.lower()][:1]
    caveats.extend(payer_notes)

    cited = []
    for line in current["lines"] + current["payer_lines"]:
        if line["rule_id"] not in cited:
            cited.append(line["rule_id"])

    return {
        "explanation": " ".join(parts),
        "cited_rules": cited,
        "caveats": caveats,
    }

"""One serialiser for a quote, used by both the HTTP layer and the agent.

Why this is its own module: the agent is only allowed to state numbers that
appear in a tool result, and the tool result is this payload. If the API built
its JSON one way and the agent's tool built it another, the guardrail would be
checking a different set of numbers from the ones on screen — and the whole
"the model cannot invent a figure" claim would be quietly false.

Money is a string here. A float would reintroduce, at the last possible moment,
exactly the rounding drift the engine spends its time avoiding.
"""

from __future__ import annotations

from decimal import Decimal

from app.engine.models import CostBreakdown, Transaction


def line_payload(line) -> dict:
    return {
        "rule_id": line.rule_id,
        "label": line.label,
        "amount": str(line.amount),
        "currency": line.currency,
        "fixed_amount": str(line.fixed_amount),
        "fixed_currency": line.fixed_currency,
        "source_url": line.source_url,
        "verified": line.verified,
        "borne_by": line.borne_by,
        "tier": line.tier,
    }


def breakdown_payload(breakdown: CostBreakdown) -> dict:
    return {
        "path_id": breakdown.path_id,
        "lines": [line_payload(line) for line in breakdown.lines],
        "payer_lines": [line_payload(line) for line in breakdown.payer_lines],
        "total_fees": str(breakdown.total_fees),
        "total_fees_currency": breakdown.total_fees_currency,
        "net_received": str(breakdown.net_received),
        "net_received_currency": breakdown.net_received_currency,
        "gross_received": str(breakdown.gross_received),
        "effective_rate_pct": str(breakdown.effective_rate_pct),
        "fx_snapshot_date": breakdown.fx_snapshot_date,
        "fx_age_days": breakdown.fx_age_days,
        "fx_stale": breakdown.fx_stale,
        "payer_side_total": str(breakdown.payer_side_total),
        "payer_side_currency": breakdown.payer_side_currency,
        "assumptions": list(breakdown.assumptions),
    }


def client_side_payload(breakdown) -> dict:
    """The payer's side of a route that moves the conversion to them.

    `lines` are the payer-borne fees the engine charged for that route — in
    practice the 4.00% spread, but read from the breakdown rather than assumed,
    so a config change cannot leave the prose describing a rate that is no
    longer being charged.
    """
    return {
        "invoice_currency": breakdown.invoice_currency,
        "payer_currency": breakdown.payer_currency,
        "payer_converts": breakdown.payer_currency != breakdown.invoice_currency,
        "lines": [line_payload(line) for line in breakdown.payer_lines],
        "total": str(breakdown.payer_side_total),
        "currency": breakdown.payer_side_currency,
        "note": (
            "The conversion this route avoids is not removed, it moves to your "
            "client. This is the same engine pricing that side of the payment."
        ),
    }


def rail_payload(rail) -> dict:
    # Deliberately no `rate_pct` key. The quote payload states totals, not
    # operands, so the model is never handed a bare percentage it could combine
    # with a fee rate. The rate lives in the sentence, where the engine wrote it.
    return {
        "fee": str(rail.fee),
        "net": str(rail.net),
        "currency": rail.currency,
        "basis": (
            f"A hypothetical rail charging {rail.rate_pct}% of the same gross, "
            "for scale only. FeeLens does not price any other provider and this "
            "is not a quote."
        ),
    }


def net_savings_payload(net: Decimal, currency: str) -> dict:
    """A signed difference plus the magnitude prose is allowed to state.

    The guardrail reads `-55.76` as the token `55.76`, so a negative figure
    written in prose would look unsourced. The magnitude is therefore computed
    here, by the engine, rather than by the sentence that needs it.
    """
    if net < 0:
        direction = "worse_off"
    elif net > 0:
        direction = "better_off"
    else:
        direction = "even"
    return {
        "amount": str(net),
        "magnitude": str(abs(net)),
        "direction": direction,
        "currency": currency,
    }


def quote_payload(
    comparison,
    *,
    amount: Decimal,
    corridor: dict,
    payment: dict,
    disclaimer: str,
    tier: str,
    transaction: Transaction | None = None,
) -> dict:
    savings = comparison.savings
    net_savings = getattr(comparison, "net_savings", savings)
    currency = comparison.savings_currency

    payload = {
        "tier": tier,
        "disclaimer": disclaimer,
        "corridor": corridor,
        "payment": payment,
        "amount": str(amount),
        "current": breakdown_payload(comparison.current),
        "optimized": breakdown_payload(comparison.optimized),
        "savings": str(savings),
        "savings_currency": currency,
        "client_side": (
            client_side_payload(comparison.client_converts)
            if comparison.client_converts is not None
            else None
        ),
        "net_savings": net_savings_payload(net_savings, currency),
        "savings_if_monthly_for_a_year": {
            "months": 12,
            "basis": "if this invoice repeats monthly for a year",
            "receiver_savings": str(savings * 12),
            "client_cost": str(
                (comparison.client_converts.payer_side_total * 12)
                if comparison.client_converts is not None
                else Decimal("0")
            ),
            "net": net_savings_payload(net_savings * 12, currency),
        },
        "rail_comparison": (
            rail_payload(comparison.rail)
            if getattr(comparison, "rail", None) is not None
            else None
        ),
        "rejected": [
            {"path_id": item.path_id, "reason": item.reason}
            for item in comparison.rejected
        ],
        "assumptions": list(comparison.assumptions),
    }
    if transaction is not None:
        payload["transaction"] = {
            "invoice_currency": transaction.invoice_currency,
            "payer_currency": transaction.payer_currency,
            "payer_region": transaction.payer_region,
            "receive_currency": transaction.receive_currency,
            "region": transaction.region,
            "account_type": transaction.account_type,
            "transaction_type": transaction.transaction_type,
            "payment_method": transaction.payment_method,
        }
    return payload

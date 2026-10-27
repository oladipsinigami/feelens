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
    payload = {
        "tier": tier,
        "disclaimer": disclaimer,
        "corridor": corridor,
        "payment": payment,
        "amount": str(amount),
        "current": breakdown_payload(comparison.current),
        "optimized": breakdown_payload(comparison.optimized),
        "savings": str(comparison.savings),
        "savings_currency": comparison.savings_currency,
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

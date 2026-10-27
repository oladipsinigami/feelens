"""Path comparison and validity rules.

A path is only ever recommended if it is actually available. Every rejected
candidate carries a reason, because recommending something that cannot happen
is worse than recommending nothing.

Deliberately absent: any path that depends on holding a foreign balance outside
the receiving account's own currency. No PayPal region publishes that
capability for a standard account, so it is not offered rather than offered
and rejected with a guess.
"""

from .calculator import (
    HOLDING_CURRENCY_PATH,
    calculate_effective_cost,
    with_invoice_currency,
)
from .models import Comparison, RejectedPath, Transaction

CURRENT_PATH = "current"
PAYER_SPREAD_NOTE = "payer_funds_other_currency"


def compare_paths(
    tx: Transaction,
    fees,
    snapshot: dict,
    supported_receive_currencies=None,
) -> Comparison:
    current = calculate_effective_cost(tx, CURRENT_PATH, fees, snapshot)
    rejected = []

    if tx.payer_converts:
        rejected.append(
            RejectedPath(
                path_id=PAYER_SPREAD_NOTE,
                reason=(
                    "The payer is funding in "
                    f"{tx.payer_currency} while the invoice is in "
                    f"{tx.invoice_currency}, so a payer-side currency conversion "
                    "spread applies. That spread is charged to the payer and "
                    "cannot be removed by changing the invoice currency. Only "
                    "the receiver-side conversion can be avoided."
                ),
            )
        )

    supported = supported_receive_currencies
    optimized = current
    holding = tx.receive_currency

    if holding == tx.invoice_currency:
        rejected.append(
            RejectedPath(
                path_id=HOLDING_CURRENCY_PATH,
                reason=(
                    f"The invoice is already in {holding}, the currency this "
                    "account holds, so there is no receiver-side conversion to "
                    "remove. No cheaper path is available."
                ),
            )
        )
    elif supported is not None and holding not in supported:
        rejected.append(
            RejectedPath(
                path_id=HOLDING_CURRENCY_PATH,
                reason=(
                    f"This account cannot invoice in {holding}: it is not an "
                    "available receiving currency for the account, so PayPal "
                    "would reject the invoice or convert it anyway."
                ),
            )
        )
    else:
        optimized = calculate_effective_cost(
            with_invoice_currency(tx, holding, snapshot),
            HOLDING_CURRENCY_PATH,
            fees,
            snapshot,
        )

    savings = current.total_fees - optimized.total_fees
    if optimized is not current:
        rejected.append(
            RejectedPath(
                path_id="hold_and_batch",
                reason=(
                    "Holding a balance in a currency other than the account's "
                    "own and converting it later in batches is not available "
                    "for this account, so this path was not costed."
                ),
            )
        )

    assumptions = list(current.assumptions)
    if optimized is not current:
        # The saving is real, but it is paid for by moving the conversion to the
        # other side of the transaction. Saying so is the difference between an
        # insight and a sales pitch.
        assumptions.append(
            f"This cheaper path invoices in {holding} instead of "
            f"{tx.invoice_currency}, so it assumes the client pays in "
            f"{holding} without converting. If they choose to pay in their own "
            "currency instead, PayPal applies a 4.00% conversion spread to them "
            "— a real cost that does not appear in your net."
        )

    return Comparison(
        current=current,
        optimized=optimized,
        savings=savings,
        savings_currency=current.total_fees_currency,
        rejected=rejected,
        assumptions=assumptions,
    )
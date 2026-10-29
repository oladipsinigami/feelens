"""Path comparison and validity rules.

A path is only ever recommended if it is actually available. Every rejected
candidate carries a reason, because recommending something that cannot happen
is worse than recommending nothing.

Deliberately absent: any path that depends on holding a foreign balance outside
the receiving account's own currency. No PayPal region publishes that
capability for a standard account, so it is not offered rather than offered
and rejected with a guess.
"""

from decimal import Decimal

from .calculator import (
    HOLDING_CURRENCY_PATH,
    calculate_effective_cost,
    round_money,
    with_invoice_currency,
)
from .models import Comparison, RailBenchmark, RejectedPath, Transaction

CURRENT_PATH = "current"
CLIENT_CONVERTS_PATH = "client_converts"
PAYER_SPREAD_NOTE = "payer_funds_other_currency"

# Not a provider's price. A round number used only to say "the same payment on
# a cheap rail is roughly this", because a fee with nothing to compare it to is
# a number a reader cannot judge. Every surface that shows it says hypothetical.
NOMINAL_RAIL_PCT = Decimal("1.00")


def rail_benchmark(current) -> RailBenchmark:
    """What a 1% rail would take from the same gross, for scale only."""
    fee = round_money(current.gross_received * NOMINAL_RAIL_PCT / Decimal(100))
    return RailBenchmark(
        rate_pct=NOMINAL_RAIL_PCT,
        fee=fee,
        net=round_money(current.gross_received - fee),
        currency=current.total_fees_currency,
    )


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

    # Price the other side of the same route. Re-denominating the invoice into
    # USD only saves the receiver money because the conversion has to happen
    # somewhere; if the client still holds EUR, it happens to them. Computing
    # that here means the figure can be stated by the template, which may only
    # repeat numbers the engine produced.
    client_converts = None
    payer_cost = Decimal("0")
    if optimized is not current:
        client_converts = calculate_effective_cost(
            with_invoice_currency(
                tx, holding, snapshot, keep_payer_currency=True
            ),
            CLIENT_CONVERTS_PATH,
            fees,
            snapshot,
        )
        payer_cost = client_converts.payer_side_total

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
            f"{holding} without converting. If they pay from a "
            f"{tx.payer_currency} balance instead, PayPal applies a 4.00% "
            "conversion spread to them. That cost is priced separately as the "
            "client side of this comparison, and is not deducted from your net."
        )

    return Comparison(
        current=current,
        optimized=optimized,
        savings=savings,
        savings_currency=current.total_fees_currency,
        rejected=rejected,
        assumptions=assumptions,
        client_converts=client_converts,
        net_savings=savings - payer_cost,
        rail=rail_benchmark(current),
    )
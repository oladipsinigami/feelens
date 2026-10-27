"""Compare PayPal's own captured fee against the modelled one.

This is the only place FeeLens holds two independent numbers for the same
transaction, so it is the only place the model can be checked rather than
asserted. The output is deliberately blunt: a direction and a size, not a
reassurance.

One caveat is carried on every result, because it is true of every result: a
single capture validates one corridor, one amount, and one buyer country. It is
evidence, not proof.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from .models import Calibration

MATCHES_MODEL = "matches_model"
MODEL_UNDERSTATES = "model_understates"
MODEL_OVERSTATES = "model_overstates"

_CENT = Decimal("0.01")
_BASIS_POINT = Decimal("100")

SINGLE_CAPTURE_CAVEAT = (
    "One capture covers one corridor, one amount and one buyer country. "
    "Treat a match as evidence for this case, not as validation of the model."
)


@dataclass(frozen=True)
class Reconciliation:
    modeled_fee: Decimal
    modeled_currency: str
    observed_fee: Decimal
    observed_currency: str
    difference: Decimal
    difference_pct: Decimal | None
    tolerance_pct: Decimal
    verdict: str
    explanation: str
    caveat: str = SINGLE_CAPTURE_CAVEAT


def reconcile(
    modeled_fee: Decimal,
    modeled_currency: str,
    observed: Calibration,
    tolerance_pct: Decimal | float | str = Decimal("0.5"),
) -> Reconciliation:
    """Compare a modelled fee to PayPal's captured `paypal_fee`.

    `difference` is `observed - modeled`, so a positive number means the model
    understates the fee the freelancer actually paid. Comparing across
    currencies is refused outright: there is no honest conversion without the
    rate PayPal applied, and that rate is not in scope here.
    """
    modeled_fee = Decimal(modeled_fee)
    tolerance_pct = Decimal(str(tolerance_pct))

    observed_fee = observed.paypal_fee.value
    observed_currency = observed.paypal_fee.currency

    if modeled_currency != observed_currency:
        raise ValueError(
            f"cannot reconcile a modelled fee in {modeled_currency} against an "
            f"observed fee in {observed_currency}; convert one side explicitly "
            "first, using a stated rate"
        )

    difference = (observed_fee - modeled_fee).quantize(_CENT, rounding=ROUND_HALF_UP)
    tolerance_amount = (modeled_fee * tolerance_pct / _BASIS_POINT).quantize(
        _CENT, rounding=ROUND_HALF_UP
    )

    if abs(difference) <= tolerance_amount:
        verdict = MATCHES_MODEL
    elif difference > 0:
        verdict = MODEL_UNDERSTATES
    else:
        verdict = MODEL_OVERSTATES

    difference_pct = (
        (difference / modeled_fee * _BASIS_POINT).quantize(
            _CENT, rounding=ROUND_HALF_UP
        )
        if modeled_fee
        else None
    )

    return Reconciliation(
        modeled_fee=modeled_fee,
        modeled_currency=modeled_currency,
        observed_fee=observed_fee,
        observed_currency=observed_currency,
        difference=difference,
        difference_pct=difference_pct,
        tolerance_pct=tolerance_pct,
        verdict=verdict,
        explanation=_explain(
            verdict, modeled_fee, observed_fee, difference, difference_pct, tolerance_pct
        ),
    )


def _explain(verdict, modeled_fee, observed_fee, difference, difference_pct, tolerance_pct) -> str:
    if verdict == MATCHES_MODEL:
        return (
            f"PayPal charged {observed_fee} and the model said {modeled_fee}; "
            f"within the {tolerance_pct}% tolerance."
        )

    direction = "understates" if verdict == MODEL_UNDERSTATES else "overstates"
    pct = f" ({difference_pct:+}%)" if difference_pct is not None else ""
    return (
        f"PayPal charged {observed_fee}; the model said {modeled_fee}. "
        f"The model {direction} the real fee by {abs(difference)}{pct}."
    )

"""PayPal integration for FeeLens.

`Calibration` values are the only figures in the project that come from PayPal
rather than from `fees.json`. Everything they carry is `sandbox_observed`; the UI
must never present them as live, and must never mix them with `modeled` lines
without labelling each one.
"""

from .client import LIVE_BASE_URL, SANDBOX_BASE_URL, PayPalClient
from .models import (
    SANDBOX_OBSERVED,
    Calibration,
    MalformedResponse,
    Money,
    Order,
    PayPalError,
)
from .reconcile import (
    MATCHES_MODEL,
    MODEL_OVERSTATES,
    MODEL_UNDERSTATES,
    SINGLE_CAPTURE_CAVEAT,
    Reconciliation,
    reconcile,
)

__all__ = [
    "Calibration",
    "LIVE_BASE_URL",
    "MATCHES_MODEL",
    "MODEL_OVERSTATES",
    "MODEL_UNDERSTATES",
    "MalformedResponse",
    "Money",
    "Order",
    "PayPalClient",
    "PayPalError",
    "Reconciliation",
    "SANDBOX_BASE_URL",
    "SANDBOX_OBSERVED",
    "SINGLE_CAPTURE_CAVEAT",
    "reconcile",
]

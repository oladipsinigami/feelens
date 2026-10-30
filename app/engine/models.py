"""Data types for the FeeLens fee engine.

Modelling decisions that are assumptions rather than facts, surfaced so the UI
can display them:

- Percentage fees are applied to the invoice (payment) currency amount, then
  converted into the receiving currency. Real PayPal assessment order is not
  published, so this is a simplification.
- Fixed fees are denominated in the currency RECEIVED, per PayPal's
  "based on currency received" wording, and are never converted.
- Every fee line is REPORTED in the receiving currency, and a total is the sum
  of the rounded lines rather than a conversion of their sum. The two differ by
  a cent or two, and the sum of the lines is the one that has to agree with the
  column above it. The invoice-currency amount a percentage was applied to is
  kept alongside as `assessed_amount`, because that is the figure PayPal's own
  statement will show.
- Totals are expressed in the receiving currency, because that is the number a
  freelancer actually ends up with.
- An FX spread is modelled as a percentage of the invoice amount rather than of
  the converted balance. The difference is second-order.
"""

from dataclasses import dataclass, field
from decimal import Decimal

MODELLED = "modeled"
PAYPAL_QUOTE = "paypal_quote"
SANDBOX_OBSERVED = "sandbox_observed"


class UnsupportedCurrencyError(ValueError):
    """A corridor that cannot be costed because a rate is missing.

    Costing it anyway would mean inventing a rate, so this is raised for the
    caller to surface as a stated reason. Deliberately a ValueError and not a
    KeyError: nothing is wrong with the lookup, the data does not cover it.
    """


class UnsupportedCombinationError(ValueError):
    """The inputs describe a transaction no base rate covers.

    Distinct from a config error: the config may be perfectly valid, but this
    particular pairing of transaction type and payment method matches no product
    rate. Costing it anyway yields only the surcharges and FX spread, which
    reads as a suspiciously cheap transaction rather than as a failure.
    """


class FeeConfigError(ValueError):
    """The fee config is malformed in a way that would silently change a total.

    Every malformation this guards against has the same property: without the
    check it produces a plausible number rather than an error. A typo in a
    condition drops a fee; a typo in a scope key applies one everywhere; a
    missing fixed fee becomes zero. None of those are visible in the output.
    """


@dataclass(frozen=True)
class Transaction:
    amount: Decimal
    invoice_currency: str
    payer_currency: str
    payer_region: str
    receive_currency: str
    region: str
    account_type: str
    transaction_type: str
    payment_method: str

    @property
    def is_cross_border(self) -> bool:
        return self.payer_region != self.region

    @property
    def payer_converts(self) -> bool:
        return self.payer_currency != self.invoice_currency

    @property
    def receiver_converts(self) -> bool:
        return self.receive_currency != self.invoice_currency


@dataclass(frozen=True)
class Rule:
    id: str
    label: str
    type: str
    rate_pct: Decimal
    fixed: dict
    regions: tuple
    account_types: tuple
    payment_methods: tuple
    transaction_types: tuple
    conditions: tuple
    source_url: str
    verified: bool
    borne_by: str = "receiver"
    notes: str = ""


@dataclass(frozen=True)
class FeeConfig:
    schema_version: int
    retrieved_date: str
    default_region: str
    rules: tuple
    notes: str = ""


@dataclass(frozen=True)
class FeeLine:
    """One fee, reported in the currency the receiver ends up holding.

    `amount` and `fixed_amount` are both in the receiving currency, so a table
    of lines adds up to the total beneath it. `assessed_amount` is the same
    percentage expressed in the invoice currency — the basis PayPal applied the
    rate to — and is kept because a reader checking the model against a PayPal
    statement will see that figure, not the converted one.
    """

    rule_id: str
    label: str
    amount: Decimal
    currency: str
    fixed_amount: Decimal
    fixed_currency: str
    source_url: str
    verified: bool
    borne_by: str = "receiver"
    tier: str = MODELLED
    assessed_amount: Decimal = Decimal("0")
    assessed_currency: str = ""


@dataclass
class CostBreakdown:
    path_id: str
    lines: list
    total_fees: Decimal
    total_fees_currency: str
    net_received: Decimal
    net_received_currency: str
    gross_received: Decimal
    effective_rate_pct: Decimal
    fx_snapshot_date: str
    assumptions: list = field(default_factory=list)
    payer_lines: list = field(default_factory=list)
    payer_side_total: Decimal = Decimal("0")
    payer_side_currency: str = ""
    fx_age_days: int = 0
    fx_stale: bool = False
    # The pair of currencies the fee lines were assessed in. Kept on the result
    # so a caller can say "the client pays a conversion from EUR into a USD
    # invoice" without re-deriving it from a line that might be empty.
    invoice_currency: str = ""
    payer_currency: str = ""


@dataclass(frozen=True)
class RejectedPath:
    path_id: str
    reason: str


@dataclass(frozen=True)
class RailBenchmark:
    """A hypothetical non-PayPal rail, computed only to give the fee a scale.

    FeeLens prices no other provider: it holds no sourced rate for one, and
    inventing a provider's number would break the rule that every figure traces
    to a rule id. What it can do is answer the question a reader actually has —
    "is 417.57 a lot?" — with a stated benchmark instead of silence.

    The rate is a round number, labelled hypothetical everywhere it appears, and
    never presented as a quote from anyone.
    """

    rate_pct: Decimal
    fee: Decimal
    net: Decimal
    currency: str


@dataclass
class Comparison:
    current: CostBreakdown
    optimized: CostBreakdown
    savings: Decimal
    savings_currency: str
    rejected: list
    assumptions: list = field(default_factory=list)
    # What the "cheaper" route costs the other side, priced by the same engine
    # rather than described in prose. None when no route changes the invoice
    # currency, because then nothing moves.
    client_converts: CostBreakdown | None = None
    # The saving net of that payer cost. Negative means the pair of them are
    # worse off, which is a thing the UI must be able to say.
    net_savings: Decimal = Decimal("0")
    rail: RailBenchmark | None = None
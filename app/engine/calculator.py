"""Deterministic fee calculation. Every figure here traces to a Rule id.

Two invariants this module defends:

1. A malformed config raises. It never quietly produces a number, because a
   quiet number is indistinguishable from a correct one.
2. A fee borne by the payer is reported, but never subtracted from what the
   receiver gets. Conflating the two inflates the seller's apparent cost and
   makes the savings claim look better than it is.
"""

import json
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from .models import (
    MODELLED,
    CostBreakdown,
    FeeConfig,
    FeeConfigError,
    FeeLine,
    Rule,
    Transaction,
    UnsupportedCombinationError,
    UnsupportedCurrencyError,
)

DATA = Path(__file__).resolve().parent.parent / "data"
FEES_PATH = DATA / "fees.json"
FX_PATH = DATA / "fx_snapshot.json"

HOLDING_CURRENCY_PATH = "invoice_in_holding_currency"

KNOWN_CONDITIONS = frozenset(
    {
        "cross_border",
        "payer_currency_differs_from_invoice",
        "receive_currency_differs_from_invoice",
    }
)
KNOWN_SCOPE_KEYS = frozenset(
    {"regions", "account_types", "payment_methods", "transaction_types", "conditions"}
)
KNOWN_TYPES = frozenset({"percent", "percent_plus_fixed"})
KNOWN_BORNE_BY = frozenset({"receiver", "payer"})

# ECB publishes on TARGET working days. Friday to Monday is three days and is
# normal; anything past a long weekend is worth saying out loud.
STALENESS_THRESHOLD_DAYS = 4

ASSUMPTIONS = [
    "Percentage fees are applied to the invoice amount, then converted once into the receiving currency. PayPal does not publish its assessment order.",
    "Fixed fees are charged in the currency received, per PayPal's 'based on currency received' wording.",
    "Each fee line is converted to the receiving currency and rounded to the cent before the lines are summed, so a total can differ by one cent from converting their sum. The lines on screen are the ones the total is made of.",
    "The FX rate is an ECB euro reference rate cross-rated through USD, not a PayPal rate.",
    "Fees marked as borne by the payer are shown separately and are not deducted from your net.",
    "Modelled figures only. No PayPal response has been observed.",
]


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def round_money(value: Decimal) -> Decimal:
    """The same rounding every fee line uses, for figures computed elsewhere."""
    return _money(value)


def _decimal(value, field: str, rule_id: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise FeeConfigError(
            f"rule {rule_id}: {field} is not a number ({value!r})"
        ) from exc
    if parsed < 0:
        raise FeeConfigError(f"rule {rule_id}: {field} must not be negative")
    return parsed


def _validate_rule(raw: dict, seen_ids: set) -> None:
    rule_id = raw.get("id")
    if not rule_id:
        raise FeeConfigError("a rule is missing an 'id'")
    if rule_id in seen_ids:
        raise FeeConfigError(f"duplicate rule id: {rule_id}")

    applies = raw.get("applies_to", {})
    unknown_keys = set(applies) - KNOWN_SCOPE_KEYS
    if unknown_keys:
        raise FeeConfigError(
            f"rule {rule_id}: unknown applies_to key(s) {sorted(unknown_keys)}. "
            f"Allowed: {sorted(KNOWN_SCOPE_KEYS)}. A misspelled key silently "
            "removes that part of the rule's scope."
        )

    unknown_conditions = set(applies.get("conditions", ())) - KNOWN_CONDITIONS
    if unknown_conditions:
        raise FeeConfigError(
            f"rule {rule_id}: unknown condition(s) {sorted(unknown_conditions)}. "
            f"Allowed: {sorted(KNOWN_CONDITIONS)}. An unknown condition never "
            "matches, so the fee silently disappears."
        )

    rule_type = raw.get("type")
    if rule_type not in KNOWN_TYPES:
        raise FeeConfigError(
            f"rule {rule_id}: type {rule_type!r} is not one of {sorted(KNOWN_TYPES)}"
        )

    fixed = raw.get("fixed", {})
    if not isinstance(fixed, dict):
        raise FeeConfigError(f"rule {rule_id}: 'fixed' must be an object")
    if rule_type == "percent" and fixed:
        raise FeeConfigError(
            f"rule {rule_id}: type 'percent' must not declare fixed fees "
            f"({sorted(fixed)}); use 'percent_plus_fixed'"
        )
    if rule_type == "percent_plus_fixed" and not fixed:
        raise FeeConfigError(
            f"rule {rule_id}: type 'percent_plus_fixed' must declare at least one "
            "fixed fee, otherwise it behaves as 'percent' and the fixed fee is "
            "silently zero"
        )
    for currency, amount in fixed.items():
        _decimal(amount, f"fixed[{currency}]", rule_id)

    borne_by = raw.get("borne_by", "receiver")
    if borne_by not in KNOWN_BORNE_BY:
        raise FeeConfigError(
            f"rule {rule_id}: borne_by {borne_by!r} is not one of "
            f"{sorted(KNOWN_BORNE_BY)}"
        )

    source_url = raw.get("source_url", "")
    if not isinstance(source_url, str) or not source_url.startswith("https://"):
        raise FeeConfigError(
            f"rule {rule_id}: source_url must be an https URL, got {source_url!r}"
        )

    if not isinstance(raw.get("verified"), bool):
        raise FeeConfigError(
            f"rule {rule_id}: 'verified' must be true or false, got "
            f"{raw.get('verified')!r}"
        )


def load_fees(path: Path = FEES_PATH) -> FeeConfig:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))

    seen_ids: set = set()
    for item in raw["rules"]:
        _validate_rule(item, seen_ids)
        seen_ids.add(item["id"])

    rules = tuple(
        Rule(
            id=item["id"],
            label=item["label"],
            type=item["type"],
            rate_pct=_decimal(item["rate_pct"], "rate_pct", item["id"]),
            fixed={k: Decimal(v) for k, v in item.get("fixed", {}).items()},
            regions=tuple(item.get("applies_to", {}).get("regions", ())),
            account_types=tuple(item.get("applies_to", {}).get("account_types", ())),
            payment_methods=tuple(item.get("applies_to", {}).get("payment_methods", ())),
            transaction_types=tuple(item.get("applies_to", {}).get("transaction_types", ())),
            conditions=tuple(item.get("applies_to", {}).get("conditions", ())),
            source_url=item["source_url"],
            verified=item["verified"],
            borne_by=item.get("borne_by", "receiver"),
            notes=item.get("notes", ""),
        )
        for item in raw["rules"]
    )
    return FeeConfig(
        schema_version=raw["schema_version"],
        retrieved_date=raw["retrieved_date"],
        default_region=raw.get("default_region", "US"),
        rules=rules,
        notes=raw.get("notes", ""),
    )


def load_rates(path: Path = FX_PATH) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def rate_between(snapshot: dict, origin: str, target: str) -> Decimal:
    rates = snapshot["rates_to_usd"]
    missing = [code for code in (origin, target) if code not in rates]
    if missing:
        raise UnsupportedCurrencyError(
            f"No FX rate for {' or '.join(missing)} in the "
            f"{snapshot.get('retrieved_date', 'unknown')} ECB snapshot, so this "
            "corridor cannot be costed. Add the currency to fx_snapshot.json, or "
            "invoice in a currency the snapshot covers."
        )
    return Decimal(rates[origin]) / Decimal(rates[target])


def fx_snapshot_age_days(snapshot: dict, today: date = None) -> int:
    today = today or date.today()
    retrieved = date.fromisoformat(snapshot["retrieved_date"])
    return (today - retrieved).days


def staleness_warning(snapshot: dict, today: date = None) -> str | None:
    age = fx_snapshot_age_days(snapshot, today)
    if age <= STALENESS_THRESHOLD_DAYS:
        return None
    return (
        f"The ECB snapshot is dated {snapshot['retrieved_date']} and is {age} days "
        "old. Exchange rates move, so converted amounts are indicative until the "
        "snapshot is refreshed."
    )


def conditions_met(rule: Rule, tx: Transaction) -> bool:
    checks = {
        "cross_border": tx.is_cross_border,
        "payer_currency_differs_from_invoice": tx.payer_converts,
        "receive_currency_differs_from_invoice": tx.receiver_converts,
    }
    return all(checks[name] for name in rule.conditions)


def rule_applies(rule: Rule, tx: Transaction) -> bool:
    scope = (
        (rule.regions, tx.region),
        (rule.account_types, tx.account_type),
        (rule.payment_methods, tx.payment_method),
        (rule.transaction_types, tx.transaction_type),
    )
    if any(allowed and value not in allowed for allowed, value in scope):
        return False
    return conditions_met(rule, tx)


def applicable_rules(fees: FeeConfig, tx: Transaction) -> list:
    return [rule for rule in fees.rules if rule_applies(rule, tx)]


def _require_a_base_rate(rules: list, tx: Transaction) -> None:
    """Refuse to price a transaction that only matched surcharges.

    A base rate is a rule scoped to a transaction type — it names the product
    being charged for. Surcharges and FX spreads are unscoped by design, because
    they apply on top of whichever product is in play.

    So "no applicable rule names a transaction type" means the percentage
    component is missing. That produces a total that is merely too low, with
    nothing in the output to say so.
    """
    if any(rule.transaction_types for rule in rules):
        return
    raise UnsupportedCombinationError(
        f"no base rate applies to transaction_type={tx.transaction_type!r} with "
        f"payment_method={tx.payment_method!r} in region {tx.region!r}. Only "
        "surcharges and FX spreads matched, so the transaction percentage is "
        "missing from the total."
    )


def _fixed_for(rule: Rule, receive: str) -> Decimal:
    if not rule.fixed:
        return Decimal("0")
    if receive not in rule.fixed:
        raise FeeConfigError(
            f"rule {rule.id} declares fixed fees for {sorted(rule.fixed)} but the "
            f"currency received is {receive}. Charging zero here would understate "
            "the fee; add the currency to the rule's fixed map instead."
        )
    return rule.fixed[receive]


def calculate_effective_cost(
    tx: Transaction, path_id: str, fees: FeeConfig, snapshot: dict
) -> CostBreakdown:
    invoice = tx.invoice_currency
    receive = tx.receive_currency
    rate = rate_between(snapshot, invoice, receive)

    rules = applicable_rules(fees, tx)
    _require_a_base_rate(rules, tx)

    receiver_lines = []
    payer_lines = []

    for rule in rules:
        # Rounded once, in the currency the fee is assessed in, because that is
        # the figure PayPal's statement shows.
        assessed = _money(tx.amount * rule.rate_pct / Decimal(100))
        line = FeeLine(
            rule_id=rule.id,
            label=rule.label,
            # And converted per line rather than at the end. Converting the sum
            # instead would make the column above the total disagree with it by
            # a cent or two, which reads as an arithmetic error to anyone who
            # adds the column up — the one check a reader will actually perform.
            amount=_money(assessed * rate),
            currency=receive,
            fixed_amount=_money(_fixed_for(rule, receive)),
            fixed_currency=receive,
            source_url=rule.source_url,
            verified=rule.verified,
            borne_by=rule.borne_by,
            tier=MODELLED,
            assessed_amount=assessed,
            assessed_currency=invoice,
        )
        (payer_lines if rule.borne_by == "payer" else receiver_lines).append(line)

    def total_of(lines) -> Decimal:
        """The sum of the rounded lines, which is what the table displays.

        Quantised again so an empty side reports `0.00` rather than `0`: a
        money field that changes shape when it is zero is a money field some
        consumer will eventually format wrongly.
        """
        return _money(sum((line.amount + line.fixed_amount for line in lines), Decimal("0")))

    total_fees = total_of(receiver_lines)
    payer_side_total = total_of(payer_lines)
    gross = _money(tx.amount * rate)
    net = gross - total_fees
    effective = (
        (total_fees / gross * Decimal(100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if gross
        else Decimal("0")
    )

    age = fx_snapshot_age_days(snapshot)
    warning = staleness_warning(snapshot)
    assumptions = ([warning] if warning else []) + list(ASSUMPTIONS)

    return CostBreakdown(
        path_id=path_id,
        lines=receiver_lines,
        total_fees=total_fees,
        total_fees_currency=receive,
        net_received=net,
        net_received_currency=receive,
        gross_received=gross,
        effective_rate_pct=effective,
        fx_snapshot_date=snapshot["retrieved_date"],
        assumptions=assumptions,
        payer_lines=payer_lines,
        payer_side_total=payer_side_total,
        payer_side_currency=receive,
        fx_age_days=age,
        fx_stale=warning is not None,
        invoice_currency=invoice,
        payer_currency=tx.payer_currency,
    )


def with_invoice_currency(
    tx: Transaction,
    currency: str,
    snapshot: dict,
    keep_payer_currency: bool = False,
) -> Transaction:
    """Re-denominate the same economic amount into another invoice currency.

    The amount is converted, not carried across unchanged. Re-denominating
    without converting would model a smaller invoice rather than a cheaper
    route for the same work.

    `keep_payer_currency` is how the payer's side of the same route is priced.
    By default the payer is assumed to follow the invoice into the new currency,
    which is what makes the route cheap. Passing True keeps them in whatever
    they actually hold, so the conversion spread the invoice currency move hands
    them becomes a priced line instead of a sentence in an assumption.
    """
    amount = _money(tx.amount * rate_between(snapshot, tx.invoice_currency, currency))
    return Transaction(
        amount=amount,
        invoice_currency=currency,
        payer_currency=tx.payer_currency if keep_payer_currency else currency,
        payer_region=tx.payer_region,
        receive_currency=tx.receive_currency,
        region=tx.region,
        account_type=tx.account_type,
        transaction_type=tx.transaction_type,
        payment_method=tx.payment_method,
    )

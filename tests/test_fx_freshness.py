"""A rate snapshot has an age, and the age is part of the number's meaning.

ECB publishes on TARGET working days only. A Friday rate read on a Monday is
three days old and completely normal; that must not cry wolf. A three-week-old
rate presented next to a currency conversion is a different thing, and the user
is entitled to see it.
"""

from datetime import date
from decimal import Decimal

from app.engine import calculate_effective_cost, fx_snapshot_age_days, staleness_warning
from app.engine.models import Transaction


def snapshot(retrieved):
    return {"retrieved_date": retrieved, "rates_to_usd": {"USD": "1.000000"}}


def test_age_is_counted_in_calendar_days():
    assert fx_snapshot_age_days(snapshot("2026-10-02"), today=date(2026, 10, 26)) == 24


def test_a_weekend_carry_is_not_reported_as_stale():
    # Friday 23 October read on Monday 26 October: three days, business as usual.
    friday = snapshot("2026-10-23")

    assert fx_snapshot_age_days(friday, today=date(2026, 10, 26)) == 3
    assert staleness_warning(friday, today=date(2026, 10, 26)) is None


def test_a_three_week_old_snapshot_produces_a_warning_naming_dates_and_age():
    warning = staleness_warning(snapshot("2026-10-02"), today=date(2026, 10, 26))

    assert warning
    assert "2026-10-02" in warning
    assert "24" in warning
    assert "ECB" in warning


def test_a_breakdown_built_on_a_stale_snapshot_says_so(fees, rates):
    tx = Transaction(
        amount=Decimal("100"),
        invoice_currency="EUR",
        payer_currency="EUR",
        payer_region="DE",
        receive_currency="USD",
        region="US",
        account_type="business",
        transaction_type="goods_services",
        payment_method="goods_services_transfer",
    )
    breakdown = calculate_effective_cost(tx, "current", fees, rates)

    assert breakdown.fx_stale is True
    assert any("snapshot" in note.lower() for note in breakdown.assumptions)

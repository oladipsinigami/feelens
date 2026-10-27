"""The FX refresh tool, tested where it can be tested.

The fetch itself needs a network and is not covered here — deliberately, because
a test that hits the ECB mirror is a test that fails on a train. What is covered
is the part that can silently corrupt every converted figure: the direction of
the cross-rate, the magnitude sanity check, and the weekday wording that makes a
weekend carry read as normal rather than stale.
"""

import json
from datetime import date
from decimal import Decimal

from app.engine import rate_between
from app.engine.calculator import FX_PATH
from scripts.refresh_fx import CURRENCIES, movement, notes_for, rates_to_usd

# Shape returned by api.frankfurter.dev: units of each currency per one euro.
PAYLOAD = {
    "amount": 1.0,
    "base": "EUR",
    "date": "2026-10-26",
    "rates": {"USD": 1.1193, "GBP": 0.8373, "AUD": 1.5944, "INR": 106.28, "PHP": 71.24},
}


def test_usd_is_always_exactly_one():
    assert rates_to_usd(PAYLOAD)["USD"] == "1.000000"


def test_a_euro_is_worth_more_than_a_dollar():
    """Direction check. Inverting this flips every converted invoice."""
    assert Decimal(rates_to_usd(PAYLOAD)["EUR"]) > 1


def test_the_cross_rate_matches_what_the_engine_will_compute():
    """The snapshot is only correct if `rate_between` reads it as intended."""
    snapshot = {"rates_to_usd": rates_to_usd(PAYLOAD)}

    assert rate_between(snapshot, "EUR", "USD").quantize(Decimal("0.0001")) == Decimal(
        "1.1193"
    )
    assert rate_between(snapshot, "USD", "EUR") < 1


def test_every_priced_currency_survives_the_round_trip():
    rates = rates_to_usd(PAYLOAD)

    assert set(rates) == set(CURRENCIES)
    for code in CURRENCIES:
        assert Decimal(rates[code]) > 0


def test_a_rupee_is_worth_less_than_a_cent():
    """Magnitude sanity: 1 INR is about 0.0105 USD."""
    assert Decimal(rates_to_usd(PAYLOAD)["INR"]) < Decimal("0.02")


def test_movement_reports_a_signed_change():
    rows = movement({"USD": "1.000000", "EUR": "1.100000"}, rates_to_usd(PAYLOAD))
    eur = [row for row in rows if row[0] == "EUR"][0]

    assert eur[1] == "1.100000"
    assert eur[2] == "1.119300"
    assert eur[3].startswith("+")


def test_notes_name_the_weekday_so_a_weekend_carry_is_obvious():
    text = notes_for(date(2026, 10, 23), date(2026, 10, 26))

    assert "Friday" in text
    assert "Monday" in text
    assert "four calendar days" in text


def test_the_live_snapshot_is_readable_by_the_engine():
    """Guards the file the app actually ships, not a copy."""
    snapshot = json.loads(FX_PATH.read_text(encoding="utf-8"))

    assert snapshot["verified"] is True
    assert snapshot["rates_to_usd"]["USD"] == "1.000000"
    assert Decimal(rate_between(snapshot, "EUR", "USD")) > 0

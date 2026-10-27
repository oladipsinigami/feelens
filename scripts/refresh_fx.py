"""Refresh the exchange-rate snapshot, deliberately and visibly.

The app is designed to admit a stale rate rather than hide it, so refreshing is
a decision someone makes, not a cron job that quietly moves the numbers under a
demo. This script therefore:

- prints every rate it is about to change, with the movement, before writing
- refuses to write a snapshot that is not newer than the one on disk
- writes atomically, so a failed fetch cannot leave a half-written file
- does **not** touch `tests/fixtures/` — the tests read a frozen copy on purpose,
  and updating it is a separate act with recomputed expectations

    python scripts/refresh_fx.py --dry-run
    python scripts/refresh_fx.py

The source is the ECB's euro reference rates, via the frankfurter mirror. ECB
publishes on TARGET working days, so on a Monday the Friday rate is correct and
is not staleness.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.engine.calculator import FX_PATH

ENDPOINT = "https://api.frankfurter.dev/v1/latest?base=EUR&symbols=USD,GBP,AUD,INR,PHP"
CURRENCIES = ("USD", "EUR", "GBP", "AUD", "INR", "PHP")

SOURCE = (
    "European Central Bank euro foreign exchange reference rates, retrieved via "
    "api.frankfurter.dev (an ECB-sourced mirror)"
)


def fetch() -> dict:
    try:
        response = httpx.get(ENDPOINT, timeout=20.0)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as exc:
        raise SystemExit(
            f"Could not reach the rate source ({type(exc).__name__}).\n"
            f"  {ENDPOINT}\n"
            "The snapshot on disk was not touched. If the network is unavailable, "
            "the existing snapshot keeps working and /api/health reports its age."
        ) from exc


def rates_to_usd(payload: dict) -> dict:
    """Frankfurter quotes everything per euro; the engine wants USD per unit.

    `rate_between` divides two entries, so each entry must be "USD for one unit
    of this currency": EUR 1.1193, USD 1.000000.
    """
    per_euro = payload["rates"]
    usd_per_euro = Decimal(str(per_euro["USD"]))
    out = {"USD": "1.000000"}
    for code in CURRENCIES:
        if code == "USD":
            continue
        if code == "EUR":
            value = usd_per_euro
        else:
            value = usd_per_euro / Decimal(str(per_euro[code]))
        out[code] = str(value.quantize(Decimal("0.000001")))
    return out


def movement(old: dict, new: dict) -> list:
    rows = []
    for code in CURRENCIES:
        if code not in new:
            continue
        before = Decimal(old.get(code, new[code]))
        after = Decimal(new[code])
        if before == 0:
            pct = "n/a"
        else:
            pct = f"{((after - before) / before * 100).quantize(Decimal('0.0001')):+}%"
        rows.append((code, str(before), str(after), pct))
    return rows


def notes_for(snapshot_date: date, read_on: date) -> str:
    return (
        "ECB publishes on TARGET working days only, so on weekends and TARGET "
        f"holidays the last published rate stands. This snapshot is dated "
        f"{snapshot_date.isoformat()} ({snapshot_date.strftime('%A')}) and was read "
        f"on {read_on.isoformat()} ({read_on.strftime('%A')}). The UI must show "
        "this date rather than implying a live rate, and the engine raises a "
        "warning once the age passes four calendar days."
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="print, write nothing")
    parser.add_argument("--force", action="store_true", help="write even if not newer")
    args = parser.parse_args()

    current = json.loads(FX_PATH.read_text(encoding="utf-8"))
    payload = fetch()

    snapshot_date = date.fromisoformat(payload["date"])
    read_on = date.today()
    previous_date = date.fromisoformat(current["retrieved_date"])

    print(f"on disk      : {previous_date.isoformat()}")
    print(f"source says  : {snapshot_date.isoformat()} ({snapshot_date.strftime('%A')})")
    print()

    if snapshot_date < previous_date:
        print("Refusing to move backwards: the source is older than the file.")
        return 1
    if snapshot_date == previous_date and not args.force:
        print("Nothing newer published. The file is already current.")
        print("(Use --force only if the rates themselves are known to be wrong.)")
        return 0

    new_rates = rates_to_usd(payload)
    print(f"{'currency':9} {'before':>12} {'after':>12} {'move':>10}")
    print("-" * 46)
    for code, before, after, pct in movement(current["rates_to_usd"], new_rates):
        print(f"{code:9} {before:>12} {after:>12} {pct:>10}")
    print()

    snapshot = {
        "retrieved_date": snapshot_date.isoformat(),
        "base": "USD",
        "source": SOURCE,
        "source_urls": [current["source_urls"][0], ENDPOINT],
        "verified": True,
        "notes": notes_for(snapshot_date, read_on),
        "rates_to_usd": new_rates,
    }

    if args.dry_run:
        print("--dry-run: nothing written.")
        print(json.dumps(snapshot, indent=2))
        return 0

    temporary = FX_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
    temporary.replace(FX_PATH)
    print(f"Wrote {FX_PATH}")
    print()
    print("The test fixture was NOT touched. If you want the suite to cover the")
    print("new rates, copy this file to tests/fixtures/fx_snapshot.json and")
    print("recompute the expected figures deliberately — do not edit them to pass.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

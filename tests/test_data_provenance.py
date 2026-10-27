"""Provenance is a product feature here, so it gets tests.

Every rule claims a source and a retrieval date. Those two claims can contradict
each other: a rule retrieved on 15 July cannot have been read off a document
published on 1 September. Nothing crashes when that happens — the citation just
becomes a small lie that a judge can find in ten seconds.

These tests read the live data files on purpose. They are about the shipped
numbers, not about engine behaviour.
"""

import json
import re
from datetime import date
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "app" / "data"

MONTHS = {
    name: index
    for index, name in enumerate(
        [
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ],
        start=1,
    )
}

DATED_PDF = re.compile(r"(\d{2})-([a-z]+)-(\d{4})")


def fees_config():
    return json.loads((DATA / "fees.json").read_text(encoding="utf-8"))


def fx_snapshot():
    return json.loads((DATA / "fx_snapshot.json").read_text(encoding="utf-8"))


def test_retrieved_date_is_a_real_iso_date():
    retrieved = date.fromisoformat(fees_config()["retrieved_date"])

    assert retrieved.year >= 2026


def test_no_rule_cites_a_document_published_after_it_was_retrieved():
    retrieved = date.fromisoformat(fees_config()["retrieved_date"])

    for rule in fees_config()["rules"]:
        match = DATED_PDF.search(rule["source_url"])
        if not match:
            continue
        day, month_name, year = match.groups()
        published = date(int(year), MONTHS[month_name], int(day))

        assert published <= retrieved, (
            f"{rule['id']} claims retrieval on {retrieved} but cites a document "
            f"published {published} ({rule['source_url']}). One of the two dates "
            "is wrong."
        )


def test_every_rule_cites_an_https_url():
    for rule in fees_config()["rules"]:
        assert rule["source_url"].startswith("https://"), rule["id"]


def test_fx_snapshot_declares_a_date_and_a_source():
    snapshot = fx_snapshot()

    date.fromisoformat(snapshot["retrieved_date"])
    assert snapshot["source"].strip()
    assert any(url.startswith("https://") for url in snapshot["source_urls"])


def test_every_fx_rate_is_positive_and_usd_is_one():
    rates = fx_snapshot()["rates_to_usd"]

    assert rates["USD"] == "1.000000"
    for currency, value in rates.items():
        assert float(value) > 0, currency

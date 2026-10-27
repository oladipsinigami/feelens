"""The fee config must fail loudly, never silently.

A fee config is a list of rules that decide how much money a freelancer keeps.
Every way it can be wrong has the same shape: it does not crash, it just
produces a confident wrong number.

- A misspelled condition (`cross_bordr`) makes the rule never apply, so a real
  fee disappears and the quote looks cheaper than reality.
- A misspelled scope key (`payment_method` instead of `payment_methods`) makes
  the scope check pass for every payment method, so a fee is charged twice.
- A missing fixed fee for the currency received silently becomes zero.

None of those raise on their own. They are caught here at load time instead,
which is the only place where "this config is malformed" can still be said
before a number reaches a user.
"""

import json
from pathlib import Path

import pytest

from app.engine import load_fees
from app.engine.models import FeeConfigError

FIXTURES = Path(__file__).parent / "fixtures"


def rule(raw, rule_id):
    """Address a rule by id.

    Indexing `raw["rules"][2]` couples the test to rule ordering: insert a rule
    and the mutation lands on a different one, so the test fails (or worse,
    passes) for a reason unrelated to what it claims to check.
    """
    for item in raw["rules"]:
        if item["id"] == rule_id:
            return item
    raise AssertionError(f"no rule {rule_id!r} in the fixture config")


def config_with(tmp_path, mutate):
    raw = json.loads((FIXTURES / "fees.json").read_text(encoding="utf-8"))
    mutate(raw)
    path = tmp_path / "fees.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def test_the_shipped_config_is_valid():
    load_fees(FIXTURES / "fees.json")


def test_unknown_condition_name_is_rejected_instead_of_dropping_the_fee(tmp_path):
    def mutate(raw):
        rule(raw, "intl_surcharge_us")["applies_to"]["conditions"] = ["cross_bordr"]

    with pytest.raises(FeeConfigError) as raised:
        load_fees(config_with(tmp_path, mutate))

    assert "cross_bordr" in str(raised.value)
    assert "intl_surcharge_us" in str(raised.value)


def test_misspelled_scope_key_is_rejected_instead_of_broadening_the_rule(tmp_path):
    def mutate(raw):
        applies = rule(raw, "commercial_paypal_checkout")["applies_to"]
        applies["payment_method"] = applies.pop("payment_methods")

    with pytest.raises(FeeConfigError) as raised:
        load_fees(config_with(tmp_path, mutate))

    assert "payment_method" in str(raised.value)


def test_duplicate_rule_ids_are_rejected(tmp_path):
    def mutate(raw):
        rule(raw, "commercial_paypal_checkout")["id"] = "commercial_goods_services_transfer"

    with pytest.raises(FeeConfigError) as raised:
        load_fees(config_with(tmp_path, mutate))

    assert "duplicate" in str(raised.value).lower()


def test_percent_plus_fixed_without_any_fixed_fee_is_rejected(tmp_path):
    def mutate(raw):
        rule(raw, "commercial_paypal_checkout")["fixed"] = {}

    with pytest.raises(FeeConfigError) as raised:
        load_fees(config_with(tmp_path, mutate))

    assert "fixed" in str(raised.value).lower()


def test_percent_rule_that_declares_a_fixed_fee_is_rejected(tmp_path):
    def mutate(raw):
        rule(raw, "commercial_goods_services_transfer")["fixed"] = {"USD": "0.49"}

    with pytest.raises(FeeConfigError) as raised:
        load_fees(config_with(tmp_path, mutate))

    assert "fixed" in str(raised.value).lower()


def test_rule_citing_a_non_https_source_is_rejected(tmp_path):
    def mutate(raw):
        rule(raw, "commercial_goods_services_transfer")["source_url"] = "http://example.com/fees"

    with pytest.raises(FeeConfigError) as raised:
        load_fees(config_with(tmp_path, mutate))

    assert "https" in str(raised.value)


def test_verified_flag_must_be_a_boolean(tmp_path):
    def mutate(raw):
        rule(raw, "commercial_goods_services_transfer")["verified"] = "yes"

    with pytest.raises(FeeConfigError) as raised:
        load_fees(config_with(tmp_path, mutate))

    assert "verified" in str(raised.value)


def test_unknown_borne_by_is_rejected(tmp_path):
    def mutate(raw):
        rule(raw, "commercial_goods_services_transfer")["borne_by"] = "somebody"

    with pytest.raises(FeeConfigError) as raised:
        load_fees(config_with(tmp_path, mutate))

    assert "borne_by" in str(raised.value)

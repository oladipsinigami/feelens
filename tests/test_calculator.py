from decimal import Decimal

import pytest

from app.engine import calculate_effective_cost, with_invoice_currency
from app.engine.models import FeeConfigError, Transaction

EUR_USD = Decimal("1.122500")
GBP_USD = Decimal("1.320076")

SNAPSHOT_DATE = "2026-10-02"


def test_percent_fees_are_computed_in_the_invoice_currency(fees, rates):
    tx = Transaction(
        amount=Decimal("5000"),
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

    assert [(line.rule_id, line.amount, line.currency) for line in breakdown.lines] == [
        ("commercial_goods_services_transfer", Decimal("149.50"), "EUR"),
        ("intl_surcharge_us", Decimal("75.00"), "EUR"),
        ("fx_spread_balance_conversion", Decimal("150.00"), "EUR"),
    ]
    assert breakdown.total_fees == Decimal("420.38")
    assert breakdown.total_fees_currency == "USD"
    assert breakdown.net_received == Decimal("5192.12")


def test_international_surcharge_applies_even_when_currency_matches(fees, rates):
    tx = Transaction(
        amount=Decimal("5000"),
        invoice_currency="USD",
        payer_currency="USD",
        payer_region="DE",
        receive_currency="USD",
        region="US",
        account_type="business",
        transaction_type="goods_services",
        payment_method="goods_services_transfer",
    )
    breakdown = calculate_effective_cost(tx, "current", fees, rates)

    assert [line.rule_id for line in breakdown.lines] == [
        "commercial_goods_services_transfer",
        "intl_surcharge_us",
    ]
    assert breakdown.total_fees == Decimal("224.50")
    assert breakdown.net_received == Decimal("4775.50")


def test_changing_invoice_currency_to_the_holding_currency_saves_the_fx_spread(fees, rates):
    current = Transaction(
        amount=Decimal("5000"),
        invoice_currency="EUR",
        payer_currency="EUR",
        payer_region="DE",
        receive_currency="USD",
        region="US",
        account_type="business",
        transaction_type="goods_services",
        payment_method="goods_services_transfer",
    )
    re_denominated = with_invoice_currency(current, "USD", rates)

    assert re_denominated.amount == Decimal("5612.50")
    assert re_denominated.invoice_currency == "USD"

    current_cost = calculate_effective_cost(current, "current", fees, rates)
    optimized_cost = calculate_effective_cost(
        re_denominated, "invoice_in_holding_currency", fees, rates
    )

    assert optimized_cost.gross_received == current_cost.gross_received

    savings = current_cost.total_fees - optimized_cost.total_fees
    assert savings == Decimal("168.38")
    assert Decimal("0") < savings < current_cost.total_fees


def test_re_denominating_without_converting_would_model_a_smaller_invoice(fees, rates):
    tx = Transaction(
        amount=Decimal("5000"),
        invoice_currency="EUR",
        payer_currency="EUR",
        payer_region="DE",
        receive_currency="USD",
        region="US",
        account_type="business",
        transaction_type="goods_services",
        payment_method="goods_services_transfer",
    )
    naive = calculate_effective_cost(tx, "current", fees, rates)
    correct = calculate_effective_cost(
        with_invoice_currency(tx, "USD", rates), "invoice_in_holding_currency", fees, rates
    )

    assert correct.gross_received == naive.gross_received
    assert correct.total_fees < naive.total_fees
    assert correct.net_received > naive.net_received


def test_fixed_fee_is_charged_in_the_currency_received(fees, rates):
    tx = Transaction(
        amount=Decimal("2000"),
        invoice_currency="GBP",
        payer_currency="GBP",
        payer_region="GB",
        receive_currency="USD",
        region="US",
        account_type="business",
        transaction_type="goods_services",
        payment_method="paypal_checkout",
    )
    breakdown = calculate_effective_cost(tx, "current", fees, rates)

    fixed = [line for line in breakdown.lines if line.rule_id == "commercial_paypal_checkout"][0]
    assert fixed.fixed_amount == Decimal("0.49")
    assert fixed.fixed_currency == "USD"
    assert breakdown.total_fees == Decimal("211.44")
    assert breakdown.net_received == Decimal("2428.71")


def test_domestic_payment_carries_no_international_surcharge(fees, rates):
    tx = Transaction(
        amount=Decimal("5000"),
        invoice_currency="USD",
        payer_currency="USD",
        payer_region="US",
        receive_currency="USD",
        region="US",
        account_type="business",
        transaction_type="goods_services",
        payment_method="goods_services_transfer",
    )
    breakdown = calculate_effective_cost(tx, "current", fees, rates)

    assert "intl_surcharge_us" not in [line.rule_id for line in breakdown.lines]
    assert breakdown.total_fees == Decimal("149.50")


def test_every_line_traces_to_a_rule_in_the_fee_config(fees, rates):
    tx = Transaction(
        amount=Decimal("5000"),
        invoice_currency="EUR",
        payer_currency="USD",
        payer_region="US",
        receive_currency="USD",
        region="US",
        account_type="business",
        transaction_type="goods_services",
        payment_method="goods_services_transfer",
    )
    breakdown = calculate_effective_cost(tx, "current", fees, rates)

    known = {rule.id for rule in fees.rules}
    assert breakdown.lines
    for line in breakdown.lines:
        assert line.rule_id in known
        assert line.source_url
        assert line.tier == "modeled"


def test_all_five_corridors_calculate_without_error(fees, rates):
    corridors = {
        "EUR": ("DE", Decimal("5000")),
        "GBP": ("GB", Decimal("2000")),
        "PHP": ("PH", Decimal("250000")),
        "AUD": ("AU", Decimal("7500")),
        "INR": ("IN", Decimal("400000")),
    }
    for currency, (payer_region, amount) in corridors.items():
        tx = Transaction(
            amount=amount,
            invoice_currency=currency,
            payer_currency=currency,
            payer_region=payer_region,
            receive_currency="USD",
            region="US",
            account_type="business",
            transaction_type="goods_services",
            payment_method="goods_services_transfer",
        )
        breakdown = calculate_effective_cost(tx, "current", fees, rates)

        assert breakdown.net_received > 0
        assert breakdown.total_fees > 0
        assert breakdown.fx_snapshot_date == SNAPSHOT_DATE

def test_payer_side_conversion_is_reported_but_never_deducted_from_the_receiver(fees, rates):
    tx = Transaction(
        amount=Decimal("5000"),
        invoice_currency="EUR",
        payer_currency="USD",
        payer_region="US",
        receive_currency="USD",
        region="US",
        account_type="business",
        transaction_type="goods_services",
        payment_method="goods_services_transfer",
    )
    breakdown = calculate_effective_cost(tx, "current", fees, rates)

    receiver_rules = {line.rule_id for line in breakdown.lines}
    payer_rules = {line.rule_id for line in breakdown.payer_lines}

    assert "fx_spread_payer_other_currency" in payer_rules
    assert "fx_spread_payer_other_currency" not in receiver_rules
    # Two conversions happen here: the payer converts USD -> EUR (4%, theirs),
    # then the receiver converts EUR -> USD (3%, ours). The receiver keeps
    # 2.99% + 3.00% of 5000 EUR, converted.
    assert breakdown.total_fees == Decimal("336.19")
    assert breakdown.payer_side_total == Decimal("224.50")
    assert breakdown.total_fees + breakdown.payer_side_total == Decimal("560.69")


def test_fixed_fee_missing_for_the_currency_received_raises_instead_of_charging_zero(fees, rates):
    tx = Transaction(
        amount=Decimal("2000"),
        invoice_currency="EUR",
        payer_currency="EUR",
        payer_region="DE",
        receive_currency="EUR",
        region="US",
        account_type="business",
        transaction_type="goods_services",
        payment_method="paypal_checkout",
    )

    with pytest.raises(FeeConfigError) as raised:
        calculate_effective_cost(tx, "current", fees, rates)

    assert "EUR" in str(raised.value)
    assert "commercial_paypal_checkout" in str(raised.value)

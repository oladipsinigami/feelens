from decimal import Decimal

import pytest

from app.engine import (
    HOLDING_CURRENCY_PATH,
    Transaction,
    UnsupportedCurrencyError,
    compare_paths,
)


def eur_invoice_usd_receiver():
    return Transaction(
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


def test_savings_equal_the_difference_between_the_two_breakdowns(fees, rates):
    comparison = compare_paths(eur_invoice_usd_receiver(), fees, rates)

    expected = comparison.current.total_fees - comparison.optimized.total_fees
    assert comparison.savings == expected
    assert comparison.savings == Decimal("168.38")
    assert comparison.savings_currency == "USD"


def test_optimized_path_invoices_in_the_currency_actually_held(fees, rates):
    comparison = compare_paths(eur_invoice_usd_receiver(), fees, rates)

    assert comparison.optimized.path_id == HOLDING_CURRENCY_PATH
    assert comparison.optimized.total_fees < comparison.current.total_fees
    assert comparison.optimized.net_received > comparison.current.net_received


def test_already_in_the_holding_currency_reports_no_savings_and_says_why(fees, rates):
    tx = eur_invoice_usd_receiver()
    already_optimal = Transaction(
        amount=tx.amount,
        invoice_currency="USD",
        payer_currency="USD",
        payer_region="DE",
        receive_currency="USD",
        region="US",
        account_type="business",
        transaction_type="goods_services",
        payment_method="goods_services_transfer",
    )
    comparison = compare_paths(already_optimal, fees, rates)

    assert comparison.savings == Decimal("0")
    assert comparison.savings >= Decimal("0")
    reasons = {rejected.path_id: rejected.reason for rejected in comparison.rejected}
    assert HOLDING_CURRENCY_PATH in reasons
    assert reasons[HOLDING_CURRENCY_PATH].strip()


def test_savings_are_never_negative(fees, rates):
    for currency, payer_region in [("EUR", "DE"), ("GBP", "GB"), ("PHP", "PH"), ("USD", "US")]:
        tx = Transaction(
            amount=Decimal("1000"),
            invoice_currency=currency,
            payer_currency=currency,
            payer_region=payer_region,
            receive_currency="USD",
            region="US",
            account_type="business",
            transaction_type="goods_services",
            payment_method="goods_services_transfer",
        )
        comparison = compare_paths(tx, fees, rates)
        assert comparison.savings >= Decimal("0")


def test_currency_without_a_rate_is_rejected_with_a_reason_not_an_exception(fees, rates):
    tx = Transaction(
        amount=Decimal("1000"),
        invoice_currency="EUR",
        payer_currency="EUR",
        payer_region="DE",
        receive_currency="USD",
        region="US",
        account_type="business",
        transaction_type="goods_services",
        payment_method="goods_services_transfer",
    )
    comparison = compare_paths(tx, fees, rates, supported_receive_currencies=["GBP"])

    reasons = {rejected.path_id: rejected.reason for rejected in comparison.rejected}
    assert HOLDING_CURRENCY_PATH in reasons
    assert "USD" in reasons[HOLDING_CURRENCY_PATH]
    assert comparison.savings == Decimal("0")


def test_payer_side_conversion_is_reported_as_outside_the_receivers_control(fees, rates):
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
    comparison = compare_paths(tx, fees, rates)

    reasons = " ".join(rejected.reason for rejected in comparison.rejected)
    assert "payer" in reasons.lower()


def test_every_rejected_path_carries_a_non_empty_reason(fees, rates):
    comparison = compare_paths(eur_invoice_usd_receiver(), fees, rates)

    for rejected in comparison.rejected:
        assert rejected.path_id
        assert rejected.reason.strip()


def test_comparison_surfaces_the_fx_snapshot_date(fees, rates):
    comparison = compare_paths(eur_invoice_usd_receiver(), fees, rates)

    assert comparison.current.fx_snapshot_date == "2026-10-02"
    assert comparison.optimized.fx_snapshot_date == "2026-10-02"
    assert comparison.assumptions


def test_currency_missing_from_the_snapshot_raises_a_named_error_not_a_keyerror(fees, rates):
    tx = Transaction(
        amount=Decimal("1000"),
        invoice_currency="CAD",
        payer_currency="CAD",
        payer_region="CA",
        receive_currency="USD",
        region="US",
        account_type="business",
        transaction_type="goods_services",
        payment_method="goods_services_transfer",
    )

    with pytest.raises(UnsupportedCurrencyError) as raised:
        compare_paths(tx, fees, rates)

    message = str(raised.value)
    assert "CAD" in message
    assert "2026-10-02" in message
    assert not isinstance(raised.value, KeyError)


def test_savings_path_says_it_moves_the_conversion_onto_the_client(fees, rates):
    """The saving is real, but it is not free — it is paid by the other side.

    Re-denominating the invoice into the receiver's currency removes the 3%
    receiver-side spread. It also hands the client a USD invoice, and a client
    whose money is in EUR may pay a 4% spread to settle it. A savings figure
    that omits that is a sales pitch, not an audit.
    """
    comparison = compare_paths(eur_invoice_usd_receiver(), fees, rates)

    caveats = [a for a in comparison.assumptions if "4.00%" in a]
    assert len(caveats) == 1
    assert "client" in caveats[0]
    assert "USD" in caveats[0] and "EUR" in caveats[0]


def test_no_conversion_caveat_when_the_invoice_currency_does_not_change(fees, rates):
    """An invoice already in USD has no cheaper path, so nothing moves."""
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

    comparison = compare_paths(tx, fees, rates)

    assert not [a for a in comparison.assumptions if "4.00%" in a]

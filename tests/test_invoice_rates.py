"""Invoice transactions are priced off PayPal's own invoicing table.

PayPal publishes "Invoicing Transaction Rates" separately from "Commercial
Transaction Rates", and the difference is not cosmetic:

| How the money arrives        | Rate              |
| ---------------------------- | ----------------- |
| Direct send                  | 2.99%, no fixed   |
| Invoice, paid from PayPal    | 3.49% + fixed     |
| Invoice, paid by card        | 2.99% + fixed     |

The third row is the trap. It carries the *same percentage* as a direct send but
adds the fixed fee, so a model that reuses the direct rate understates by
exactly that fixed fee — and on a small invoice the fixed fee dominates.

Every figure below is computed against `tests/fixtures/fees.json` and its frozen
EUR->USD rate of 1.1225, so refreshing the live FX snapshot cannot move them.
"""

from decimal import Decimal

import pytest

from app.engine import calculate_effective_cost
from app.engine.models import (
    FeeConfigError,
    Transaction,
    UnsupportedCombinationError,
)

# Frozen fixture rate; see module docstring.
EUR_USD = Decimal("1.122500")


def invoice_tx(payment_method="paypal_checkout", currency="EUR", amount="5000", receive="USD"):
    return Transaction(
        amount=Decimal(amount),
        invoice_currency=currency,
        payer_currency=currency,
        payer_region="DE",
        receive_currency=receive,
        region="US",
        account_type="business",
        transaction_type="invoice",
        payment_method=payment_method,
    )


def direct_tx(amount="5000"):
    return Transaction(
        amount=Decimal(amount),
        invoice_currency="EUR",
        payer_currency="EUR",
        payer_region="DE",
        receive_currency="USD",
        region="US",
        account_type="business",
        transaction_type="goods_services",
        payment_method="goods_services_transfer",
    )


# --- the rate itself ------------------------------------------------------


def test_invoice_paid_from_paypal_uses_the_invoicing_rate(fees, rates):
    breakdown = calculate_effective_cost(invoice_tx("paypal_checkout"), "current", fees, rates)

    # Assessed in EUR (the invoice currency), reported in USD (what is received).
    # Fixture rate 1.1225: 174.50 -> 195.88, 75.00 -> 84.19, 150.00 -> 168.38.
    assert [
        (line.rule_id, line.assessed_amount, line.amount)
        for line in breakdown.lines
    ] == [
        ("invoice_checkout", Decimal("174.50"), Decimal("195.88")),  # 3.49% of 5,000
        ("intl_surcharge_us", Decimal("75.00"), Decimal("84.19")),  # 1.50%, invoices too
        ("fx_spread_balance_conversion", Decimal("150.00"), Decimal("168.38")),  # 3.00%
    ]
    assert breakdown.total_fees == Decimal("448.94")  # the lines above, plus 0.49 fixed
    assert breakdown.net_received == Decimal("5163.56")


def test_invoice_paid_by_card_uses_the_card_rate(fees, rates):
    breakdown = calculate_effective_cost(invoice_tx("card"), "current", fees, rates)

    assert [line.rule_id for line in breakdown.lines] == [
        "invoice_card",
        "intl_surcharge_us",
        "fx_spread_balance_conversion",
    ]
    assert breakdown.lines[0].assessed_amount == Decimal("149.50")  # 2.99% of 5,000
    assert breakdown.lines[0].amount == Decimal("167.81")  # reported in USD


def test_card_funded_invoice_still_pays_the_fixed_fee(fees, rates):
    """2.99% + fixed, never 2.99% alone.

    This is the row that looks identical to a direct send and is not. Without
    the fixed fee the model would understate by exactly that amount.
    """
    breakdown = calculate_effective_cost(invoice_tx("card"), "current", fees, rates)
    line = breakdown.lines[0]

    assert line.fixed_amount == Decimal("0.49")
    assert line.fixed_currency == "USD"


def test_card_funded_invoice_costs_exactly_the_fixed_fee_more_than_a_direct_send(fees, rates):
    direct = calculate_effective_cost(direct_tx(), "current", fees, rates)
    card_invoice = calculate_effective_cost(invoice_tx("card"), "current", fees, rates)

    assert card_invoice.total_fees - direct.total_fees == Decimal("0.49")


def test_paypal_funded_invoice_costs_more_than_a_direct_send(fees, rates):
    direct = calculate_effective_cost(direct_tx(), "current", fees, rates)
    invoice = calculate_effective_cost(invoice_tx("paypal_checkout"), "current", fees, rates)

    # The rate difference is exactly 0.50% of 5,000 EUR = 25.00 EUR, plus the
    # 0.49 USD fixed fee. Stated in the currency it is assessed in, because that
    # is the precise claim; the totals below are sums of separately rounded
    # lines and can differ from the converted 28.55 by a cent.
    difference = invoice.lines[0].assessed_amount - direct.lines[0].assessed_amount
    assert difference == Decimal("25.00")
    assert abs(
        (invoice.total_fees - direct.total_fees) - (Decimal("25.00") * EUR_USD + Decimal("0.49"))
    ) <= Decimal("0.01")


# --- scope ----------------------------------------------------------------


def test_invoice_rules_do_not_leak_into_direct_payments(fees, rates):
    """The invoice rules are scoped to transaction_type 'invoice'.

    Drop that scope and every direct payment silently gets the higher rate —
    a plausible number, wrong by 28.55 USD, and invisible in the output.
    """
    breakdown = calculate_effective_cost(direct_tx(), "current", fees, rates)

    assert [line.rule_id for line in breakdown.lines] == [
        "commercial_goods_services_transfer",
        "intl_surcharge_us",
        "fx_spread_balance_conversion",
    ]
    assert breakdown.total_fees == Decimal("420.38")


def test_invoice_received_in_indian_rupees_fails_loudly(fees, rates):
    """INR is absent from PayPal's fixed-fee table, for invoicing as well.

    The account receiving INR has no published fixed fee to charge, so the only
    honest answers are "raise" or "do not offer this corridor". Charging zero
    would understate every INR invoice by the missing fixed fee.
    """
    with pytest.raises(FeeConfigError) as raised:
        calculate_effective_cost(
            invoice_tx(currency="INR", receive="INR"), "current", fees, rates
        )

    assert "INR" in str(raised.value)


def test_unknown_payment_method_for_an_invoice_is_refused(fees, rates):
    """A mismatched pair must not quietly price an invoice as a direct send.

    'invoice' + 'goods_services_transfer' matches no base rate at all. Without a
    guard the breakdown would contain only the surcharges and FX spread, and
    look like a very cheap invoice.
    """
    with pytest.raises(UnsupportedCombinationError) as raised:
        calculate_effective_cost(
            invoice_tx("goods_services_transfer"), "current", fees, rates
        )

    message = str(raised.value)
    assert "invoice" in message
    assert "goods_services_transfer" in message

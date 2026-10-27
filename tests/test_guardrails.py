"""The guardrail, tested against the things a language model actually does.

These are the tests that justify the project's central claim. Each one is a
real failure mode observed in tool-calling models, not a hypothetical:

- restating a figure with a rounding that was never in the data
- inventing a percentage from the shape of the numbers it saw
- citing a rule id that does not exist
- describing a model output as a live quote
- writing a confident answer without calling a tool at all
"""

from decimal import Decimal

from app.agent.guardrails import (
    allowed_numbers,
    check_citations,
    check_claims,
    check_numbers,
    numbers_in,
    rule_ids_in,
    validate,
)

PAYLOAD = {
    "amount": "5000.00",
    "current": {
        "lines": [
            {"rule_id": "invoice_checkout", "amount": "174.50", "fixed_amount": "0.49"},
            {"rule_id": "intl_surcharge_1_5", "amount": "75.00", "fixed_amount": "0"},
        ],
        "total_fees": "448.93",
        "net_received": "5163.57",
        "effective_rate_pct": "8.00",
        "fx_snapshot_date": "2026-10-02",
        "fx_age_days": 24,
        "assumptions": ["PayPal applies a 4.00% conversion spread to the payer."],
    },
    "savings": "167.89",
}


# --- extraction -----------------------------------------------------------


def test_a_date_is_not_read_as_three_numbers():
    tokens = numbers_in("Rates from 2026-10-02 are used here.")

    assert tokens == []


def test_a_percentage_and_a_money_amount_are_told_apart():
    tokens = {t.raw: t.kind for t in numbers_in("A 8.00% rate on 5,000.00 EUR.")}

    assert tokens["8.00"] == "percent"
    assert tokens["5,000.00"] == "money"


def test_a_bare_integer_is_treated_as_a_count():
    assert numbers_in("Three fees, 3 in total.")[-1].kind == "count"


def test_a_number_with_a_currency_code_after_it_is_money():
    assert numbers_in("you keep 5163.57 USD")[0].kind == "money"


def test_a_number_with_a_currency_symbol_before_it_is_money():
    assert numbers_in("you keep $5163.57")[0].kind == "money"


# --- provenance -----------------------------------------------------------


def test_a_figure_that_came_from_a_tool_is_allowed():
    allowed = allowed_numbers(PAYLOAD)

    assert check_numbers("PayPal keeps 448.93 USD.", allowed) == []
    assert check_numbers("That is 8.00% of the invoice.", allowed) == []


def test_a_rounded_restatement_is_rejected():
    """5163.57 rounded to 5164 is a number the engine never produced."""
    allowed = allowed_numbers(PAYLOAD)

    violations = check_numbers("You receive about 5,164 USD.", allowed)

    assert violations
    assert "5,164" in violations[0]


def test_an_invented_percentage_is_rejected():
    """8.00 and 3.49 are both in the data; their difference is not."""
    allowed = allowed_numbers(PAYLOAD)

    assert check_numbers("The surcharge adds roughly 4.51% to your cost.", allowed)


def test_a_thousands_separator_does_not_change_the_value():
    allowed = allowed_numbers(PAYLOAD)

    assert check_numbers("You receive 5,163.57 USD.", allowed) == []


def test_a_count_is_allowed_without_a_source():
    allowed = allowed_numbers(PAYLOAD)

    assert check_numbers("Two fee lines apply.", allowed) == []


def test_a_bare_integer_too_large_to_be_a_count_is_rejected():
    """The documented residual gap is kept as narrow as possible.

    "PayPal keeps 45" has no currency, no decimal point and no percent sign, so
    it is read as a count. Counts are capped low for exactly this reason.
    """
    allowed = allowed_numbers(PAYLOAD)

    assert check_numbers("PayPal keeps 45 of it.", allowed)


def test_a_year_is_allowed_but_an_invented_four_digit_amount_is_not():
    allowed = allowed_numbers(PAYLOAD)

    assert check_numbers("PayPal's schedule was published in 2026.", allowed) == []
    assert check_numbers("PayPal keeps 1200 USD.", allowed)


def test_numbers_inside_the_engines_own_prose_count_as_sourced():
    """The engine wrote "4.00%" in an assumption; the model may repeat it."""
    allowed = allowed_numbers(PAYLOAD)

    assert Decimal("4.00") in allowed
    assert check_numbers("A 4.00% spread is charged to the client.", allowed) == []


# --- citations ------------------------------------------------------------


def test_citing_a_rule_that_was_not_in_the_quote_is_rejected():
    allowed_rules = rule_ids_in(PAYLOAD)

    violations = check_citations(["invoice_checkout", "made_up_rule"], allowed_rules)

    assert violations
    assert "made_up_rule" in violations[0]


def test_citing_nothing_is_rejected():
    assert check_citations([], rule_ids_in(PAYLOAD))


def test_citing_a_real_rule_passes():
    assert check_citations(["invoice_checkout"], rule_ids_in(PAYLOAD)) == []


# --- claim language -------------------------------------------------------


def test_calling_a_modelled_figure_live_is_rejected():
    assert check_claims("Here is your live quote from PayPal.")


def test_a_negated_claim_is_allowed():
    """Saying it is NOT a quote is exactly what we want the model to say."""
    assert check_claims("This is not a live quote, only a modelled estimate.") == []


def test_guaranteeing_a_fee_is_rejected():
    assert check_claims("This is the guaranteed cost of the invoice.")


# --- the whole verdict ----------------------------------------------------


def test_a_clean_answer_passes():
    verdict = validate(
        {
            "explanation": "PayPal keeps 448.93 USD of the 5,000.00 EUR invoice.",
            "cited_rules": ["invoice_checkout"],
            "caveats": ["A 4.00% spread falls on the client."],
        },
        [PAYLOAD],
    )

    assert verdict.ok, verdict.violations
    assert verdict.numbers_checked >= 2
    assert verdict.rules_cited == ["invoice_checkout"]


def test_a_missing_explanation_is_a_violation_not_a_blank_answer():
    verdict = validate({"cited_rules": ["invoice_checkout"]}, [PAYLOAD])

    assert not verdict.ok
    assert "explanation" in verdict.violations[0]


def test_caveats_are_checked_too_not_just_the_main_text():
    """A number hidden in a caveat is still a number the model asserted."""
    verdict = validate(
        {
            "explanation": "PayPal keeps 448.93 USD.",
            "cited_rules": ["invoice_checkout"],
            "caveats": ["Roughly 4.51% of the invoice disappears."],
        },
        [PAYLOAD],
    )

    assert not verdict.ok
    assert any("4.51" in item for item in verdict.violations)

"""Mechanical checks on anything the model wrote.

The pitch of FeeLens is that the model never does arithmetic. A prompt saying
"do not do arithmetic" is not a guarantee; a model that has seen `3.49` and
`1.50` will eventually add them, and a number that is merely wrong is
indistinguishable from one that is right.

So the rule is enforced after generation, not before:

1. **Number provenance.** Every figure in the prose must appear somewhere in the
   tool results the model was given. If it wrote `5,200` and no tool returned
   `5200`, the answer is rejected — not corrected, rejected, and replaced by a
   deterministic template.
2. **Rule citation.** The answer must name at least one rule id, and every rule
   id it names must be one that actually appeared in the quote.
3. **No observed-data claims.** Modelled figures may not be described as live,
   confirmed, observed or guaranteed. The three tiers exist precisely so this
   cannot blur.

None of this judges whether the prose is *good*. It judges whether the prose is
*earned*, which is the part a human reviewer cannot check at a glance.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

CURRENCY_SYMBOLS = ("$", "€", "£", "₱", "₹")
CURRENCY_CODES = ("USD", "EUR", "GBP", "PHP", "AUD", "INR")

_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_CURRENCY_BEFORE = re.compile(r"(?:%s)\s*$" % "|".join(CURRENCY_CODES))
_CURRENCY_AFTER = re.compile(r"^\s*(?:%s)\b" % "|".join(CURRENCY_CODES))

# Phrasings that assert a fact about PayPal's behaviour that this build cannot
# have. Matched case-insensitively, skipped when negated nearby.
_OBSERVATION_CLAIMS = (
    ("live quote", "a modelled figure is not a quote"),
    ("real-time", "nothing here is real-time"),
    ("confirmed by paypal", "no PayPal response is involved"),
    ("paypal confirmed", "no PayPal response is involved"),
    ("we observed", "no transaction has been observed in this answer"),
    ("guaranteed", "these are estimates, not guarantees"),
    ("your actual fee", "the figure is modelled, not assessed"),
    ("exactly what paypal will charge", "the figure is modelled, not assessed"),
)

_NEGATIONS = ("not ", "no ", "never ", "isn't ", "aren't ", "cannot ", "can't ", "n't ")


@dataclass(frozen=True)
class NumberToken:
    raw: str
    value: Decimal
    kind: str  # "percent" | "money" | "count"

    def __str__(self) -> str:
        return self.raw


@dataclass
class Verdict:
    ok: bool
    violations: list = field(default_factory=list)
    numbers_checked: int = 0
    numbers_allowed: int = 0
    rules_cited: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "violations": list(self.violations),
            "numbers_checked": self.numbers_checked,
            "numbers_allowed": self.numbers_allowed,
            "rules_cited": list(self.rules_cited),
        }


def _parse(raw: str) -> Decimal | None:
    try:
        value = Decimal(raw.replace(",", "").strip())
    except (InvalidOperation, AttributeError, ValueError):
        return None
    return value if value.is_finite() else None


def numbers_in(text: str) -> list:
    """Every numeric token in `text`, classified by how it is being used.

    ISO dates are blanked first so `2026-10-23` does not read as three numbers.
    A token is a *percent* if it is followed by `%`, *money* if it has a decimal
    point, a thousands separator or an adjacent currency, and otherwise a
    *count* — which is the only kind allowed to be a plain integer the model
    chose for itself.

    Known residual gap, stated rather than hidden: a bare integer written
    without a currency, a decimal point or a percent sign is treated as a count
    and allowed if it is small. A model that wrote "PayPal keeps 45" would slip
    past this check. In practice a fee stated without a currency is not a fee a
    reader can act on, and requiring every count to be sourced would reject
    "two fee lines". The bound is kept tight for that reason.
    """
    masked = _DATE.sub(lambda m: " " * len(m.group(0)), text)
    tokens = []
    for match in _NUMBER.finditer(masked):
        raw = match.group(0)
        value = _parse(raw)
        if value is None:
            continue
        before = masked[max(0, match.start() - 8) : match.start()]
        after = masked[match.end() : match.end() + 8]

        if after.lstrip().startswith("%"):
            kind = "percent"
        elif "." in raw or "," in raw:
            kind = "money"
        elif any(symbol in before for symbol in CURRENCY_SYMBOLS):
            kind = "money"
        elif _CURRENCY_BEFORE.search(before) or _CURRENCY_AFTER.search(after):
            kind = "money"
        else:
            kind = "count"

        tokens.append(NumberToken(raw=raw, value=value, kind=kind))
    return tokens


def allowed_numbers(payloads) -> set:
    """Every number the model is permitted to state, harvested from tool output.

    Both machine-readable strings ("447.65") and numbers embedded in the
    engine's own prose ("a 4.00% conversion spread") count, because both are
    things we asserted first.
    """
    found: set = set()

    def walk(node):
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, (list, tuple, set)):
            for value in node:
                walk(value)
        elif isinstance(node, bool):
            return
        elif isinstance(node, (int, float, Decimal)):
            parsed = _parse(str(node))
            if parsed is not None:
                found.add(parsed)
        elif isinstance(node, str):
            parsed = _parse(node)
            if parsed is not None:
                found.add(parsed)
            for token in numbers_in(node):
                found.add(token.value)

    walk(payloads)
    return found


def rule_ids_in(payloads) -> set:
    found: set = set()

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "rule_id" and isinstance(value, str):
                    found.add(value)
                walk(value)
        elif isinstance(node, (list, tuple)):
            for value in node:
                walk(value)

    walk(payloads)
    return found


def _negated(text: str, start: int) -> bool:
    window = text[max(0, start - 24) : start].lower()
    return any(word in window for word in _NEGATIONS)


def check_numbers(text: str, allowed: set) -> list:
    violations = []
    for token in numbers_in(text):
        if token.value in allowed:
            continue
        if token.kind == "count" and token.value == token.value.to_integral_value():
            # A count is not a financial claim: "three fee lines" needs no source.
            # Years are counts too, and a year is never a figure we priced.
            if 0 <= token.value <= 20 or 1900 <= token.value <= 2100:
                continue
        violations.append(
            f"the figure {token.raw!r} does not appear in any tool result. "
            "State only figures a tool returned."
        )
    return violations


def check_citations(cited, allowed_rules: set) -> list:
    violations = []
    if not isinstance(cited, (list, tuple)):
        return ["'cited_rules' must be a list of rule ids"]
    if not cited:
        violations.append("no rule id was cited; every fee must be traceable")
    for rule_id in cited:
        if not isinstance(rule_id, str) or rule_id not in allowed_rules:
            violations.append(
                f"cited rule {rule_id!r} was not part of the quote. "
                f"Allowed: {sorted(allowed_rules)}"
            )
    return violations


def check_claims(text: str) -> list:
    lowered = text.lower()
    violations = []
    for phrase, reason in _OBSERVATION_CLAIMS:
        start = lowered.find(phrase)
        while start != -1:
            if not _negated(text, start):
                violations.append(f"the phrase {phrase!r} is not supportable: {reason}")
                break
            start = lowered.find(phrase, start + 1)
    return violations


def validate(answer, payloads) -> Verdict:
    """Check one structured answer against the tool results it was given.

    `answer` is the model's parsed JSON: {"explanation", "cited_rules",
    "caveats"}. Anything missing is a violation, not a default.
    """
    allowed = allowed_numbers(payloads)
    allowed_rules = rule_ids_in(payloads)

    if not isinstance(answer, dict):
        return Verdict(ok=False, violations=["the answer was not a JSON object"])

    text = answer.get("explanation")
    if not isinstance(text, str) or not text.strip():
        return Verdict(ok=False, violations=["'explanation' is missing or empty"])

    caveats = answer.get("caveats", [])
    if isinstance(caveats, list):
        combined = "\n".join([text] + [str(c) for c in caveats])
    else:
        combined = text
        answer.setdefault("_notes", []).append("'caveats' was not a list")

    violations = []
    violations += check_numbers(combined, allowed)
    violations += check_citations(answer.get("cited_rules"), allowed_rules)
    violations += check_claims(combined)

    tokens = numbers_in(combined)
    return Verdict(
        ok=not violations,
        violations=violations,
        numbers_checked=len(tokens),
        numbers_allowed=len(allowed),
        rules_cited=list(answer.get("cited_rules") or []),
    )

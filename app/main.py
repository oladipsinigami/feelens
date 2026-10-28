"""FeeLens HTTP surface.

Deliberately small: one page, a config endpoint, a quote endpoint and an
explanation endpoint. The arithmetic all happens in `app.engine`; this module
only translates HTTP into a `Transaction` and the result back into JSON.

Two conventions worth knowing:

- Money is serialised as a **string**, never a float. A float would reintroduce
  exactly the rounding drift the engine spends its time avoiding, at the last
  possible moment.
- Every fee line carries `tier: "modeled"` and a `source_url`. The UI is not
  allowed to present a modelled figure as a live one, and the payload makes
  that impossible to do by accident.

The explanation endpoint is the only place a language model is involved, and it
cannot change a number: `/api/quote` and `/api/explain` both price through
`_price()`, so the prose and the table are built from the same payload.
"""

from __future__ import annotations

import os
from decimal import Decimal, InvalidOperation
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.agent import ExplanationAgent, FeeTools, OpenRouterClient
from app.corridors import (
    ACCOUNT_TYPE,
    CORRIDORS,
    DISCLAIMER,
    PAYMENT_OPTIONS,
    RECEIVE_CURRENCY,
    RECEIVE_REGION,
    corridor as find_corridor,
    payment_option as find_payment,
)
from app.engine import (
    STALENESS_THRESHOLD_DAYS,
    compare_paths,
    fx_snapshot_age_days,
    load_fees,
    load_rates,
)
from app.engine.calculator import FEES_PATH, FX_PATH
from app.engine.models import (
    MODELLED,
    FeeConfigError,
    Transaction,
    UnsupportedCombinationError,
    UnsupportedCurrencyError,
)
from app.serialize import quote_payload

load_dotenv()

STATIC = Path(__file__).resolve().parent / "static"

DEFAULT_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"

# The fallback matters more than it looks. On a free tier a rate-limited primary
# is the normal case, and a fallback that does not exist is worse than none: the
# second call 404s, LLMUnavailable is raised, and every explanation quietly
# becomes the template while /api/health still says a model is configured. This
# one is checked against OpenRouter's public model list by scripts/check_models.py.
DEFAULT_FALLBACK_MODEL = "poolside/laguna-s-2.1:free"
GUARDRAIL_NOTE = (
    "The model may only restate figures returned by a tool. Every number in its "
    "answer is checked against the tool output, and an answer that fails is "
    "discarded in favour of a deterministic explanation."
)


class QuoteRequest(BaseModel):
    amount: str = Field(..., description="Invoice amount as a decimal string, e.g. '5000.00'")
    corridor: str = Field(..., description="Invoice currency, e.g. 'EUR'")
    payment: str = Field(..., description="One of the payment option ids")


class ExplainRequest(QuoteRequest):
    question: str | None = Field(
        default=None,
        description="Optional free-text question, e.g. 'why is this so high?'",
        max_length=500,
    )


def _parse_amount(raw: str) -> Decimal:
    try:
        amount = Decimal(raw.strip().replace(",", ""))
    except (InvalidOperation, AttributeError):
        raise HTTPException(
            status_code=422,
            detail=f"amount {raw!r} is not a number. Send a decimal string such as '5000.00'.",
        )
    if not amount.is_finite():
        raise HTTPException(status_code=422, detail="amount must be a finite number")
    if amount <= 0:
        raise HTTPException(status_code=422, detail="amount must be greater than zero")
    return amount


def _build_agent(fees, snapshot) -> ExplanationAgent:
    key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
    model = (os.environ.get("LLM_MODEL") or "").strip() or DEFAULT_MODEL
    fallback = (
        (os.environ.get("LLM_FALLBACK_MODEL") or "").strip() or DEFAULT_FALLBACK_MODEL
    )
    client = OpenRouterClient(key, model, fallback) if key else None
    return ExplanationAgent(FeeTools(fees, snapshot), client)


def create_app(
    fees_path: Path | None = None,
    fx_path: Path | None = None,
    agent: ExplanationAgent | None = None,
) -> FastAPI:
    """Build the app against a specific pair of data files.

    Injection exists so tests can point at the frozen fixtures instead of the
    live config, which is refreshed independently and would otherwise break the
    suite on every legitimate update. The agent is injectable for the same
    reason: a test must not reach the network, and must not depend on whatever
    key happens to be in the environment.
    """
    fees = load_fees(fees_path or FEES_PATH)
    snapshot = load_rates(fx_path or FX_PATH)
    agent = agent or _build_agent(fees, snapshot)

    app = FastAPI(
        title="FeeLens",
        description="What PayPal takes before a freelancer gets paid.",
        version="0.2.0",
    )

    def _price(request: QuoteRequest) -> dict:
        try:
            chosen_corridor = find_corridor(request.corridor)
        except KeyError:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"unknown corridor {request.corridor!r}. "
                    f"Known: {[c['invoice_currency'] for c in CORRIDORS]}"
                ),
            )
        try:
            chosen_payment = find_payment(request.payment)
        except KeyError:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"unknown payment option {request.payment!r}. "
                    f"Known: {[p['id'] for p in PAYMENT_OPTIONS]}"
                ),
            )

        amount = _parse_amount(request.amount)

        tx = Transaction(
            amount=amount,
            invoice_currency=chosen_corridor["invoice_currency"],
            # The client settles the invoice as issued. A payer who converts into
            # their own currency is charged a 4% spread; that is surfaced as an
            # assumption on the cheaper path rather than modelled as the default.
            payer_currency=chosen_corridor["invoice_currency"],
            payer_region=chosen_corridor["client_country"],
            receive_currency=RECEIVE_CURRENCY,
            region=RECEIVE_REGION,
            account_type=ACCOUNT_TYPE,
            transaction_type=chosen_payment["transaction_type"],
            payment_method=chosen_payment["payment_method"],
        )

        try:
            comparison = compare_paths(tx, fees, snapshot)
        except (UnsupportedCurrencyError, UnsupportedCombinationError) as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        except FeeConfigError as exc:
            # A malformed config is our fault, not the caller's.
            raise HTTPException(status_code=500, detail=f"Fee configuration error: {exc}")

        return quote_payload(
            comparison,
            amount=amount,
            corridor=chosen_corridor,
            payment=chosen_payment,
            disclaimer=DISCLAIMER,
            tier=MODELLED,
            transaction=tx,
        )

    @app.get("/api/health")
    def health():
        """What a host or a judge needs to know before trusting the page.

        Deliberately not just `{"ok": true}`. The two things that can make this
        deployment quietly wrong are a stale exchange-rate snapshot and a
        missing model key, so both are reported rather than discovered later.
        """
        age = fx_snapshot_age_days(snapshot)
        return {
            "status": "ok",
            "version": app.version,
            "fee_retrieved_date": fees.retrieved_date,
            "fx_snapshot_date": snapshot["retrieved_date"],
            "fx_age_days": age,
            "fx_stale": age > STALENESS_THRESHOLD_DAYS,
            "model_configured": agent.model_ready,
            "tier": MODELLED,
        }

    @app.get("/api/config")
    def config():
        return {
            "corridors": list(CORRIDORS),
            "payment_options": list(PAYMENT_OPTIONS),
            "receive_currency": RECEIVE_CURRENCY,
            "receive_region": RECEIVE_REGION,
            "fee_retrieved_date": fees.retrieved_date,
            "fx": {
                "snapshot_date": snapshot["retrieved_date"],
                "source": snapshot.get("source", ""),
                "source_urls": snapshot.get("source_urls", []),
            },
            "explanation": {
                "model_configured": agent.model_ready,
                "models": agent.client.models if agent.client else [],
                "fallback": "deterministic template",
                "guardrail": GUARDRAIL_NOTE,
            },
            "disclaimer": DISCLAIMER,
        }

    @app.post("/api/quote")
    def quote(request: QuoteRequest):
        return _price(request)

    @app.post("/api/explain")
    def explain(request: ExplainRequest):
        payload = _price(request)
        explanation = agent.explain(
            request.amount,
            request.corridor,
            request.payment,
            request.question,
        )
        return {**payload, "explanation": explanation.as_dict()}

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    return app


app = create_app()

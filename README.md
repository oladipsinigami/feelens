# FeeLens

[![tests](https://github.com/oladipsinigami/feelens/actions/workflows/tests.yml/badge.svg)](https://github.com/oladipsinigami/feelens/actions/workflows/tests.yml)

**What PayPal takes before the money reaches you.**

A pre-payment cost auditor for cross-border freelancers. A deterministic fee
engine computes every figure from PayPal's published US fee schedule; a language
model explains the result and is mechanically prevented from changing it.

```
python -m uvicorn app.main:app --port 8000     →  http://127.0.0.1:8000
```

Live: **https://feelens-su23.onrender.com** (Render free tier — the first request
after a sleep takes ~30s to wake, and the first explanation of a quote takes
20–60s on a loaded free model).

---

## The problem

A freelancer invoices a client abroad for €5,000. PayPal shows them the fee
*after* the money arrives. By then three separate charges have already been
applied — the product rate, an international surcharge, and the receiver's
currency-conversion spread — and none of them were visible when the invoice was
written.

The cost of getting it wrong is not obvious. On that €5,000 invoice:

| How the money arrives | PayPal keeps | You keep | Effective rate |
| --- | --- | --- | --- |
| Client sends money directly | 417.57 USD | 5,157.43 USD | 7.49% |
| Invoice, paid from a PayPal account | **445.93 USD** | **5,129.07 USD** | **8.00%** |
| Invoice, paid by card or wallet | 418.06 USD | 5,156.94 USD | 7.50% |

Same money, same client, same day. **28.36 USD** apart, purely because of which
button the client presses. Nobody is told this before the invoice is sent.

And the drag is worst where it matters most. A 200 INR invoice through PayPal
Invoicing costs **31.43%** — not because any rate is high, but because a fixed
fee of 0.49 USD is 23% of a 2.10 USD payment.

## What it does

Pick the amount, the client's country, and how the money arrives. You get:

- **the net** — what actually lands, in the currency the account holds
- **every fee line**, each carrying the rule id it came from and a link to the
  PayPal page that states it
- **the effective rate**, because "3.49%" is never the number anyone experiences
- **a cheaper route, with its cost stated** — re-denominating the invoice into
  USD removes your 3% conversion spread, but it hands the client a USD invoice,
  and a German client paying in EUR is charged a 4% spread instead. The saving
  is real. So is the person who pays for it.
- **an explanation** in plain language, from the model when one is configured
  and from a deterministic template when it is not

## Why the AI cannot change the number

This is the part that is usually hand-waved, so it is the part that is enforced
in code rather than in a prompt.

The model has **four tools and no arithmetic**. The system prompt contains no
fee rates at all — a model that was never shown `3.49` and `1.50` cannot add
them.

| tool | returns |
| --- | --- |
| `get_quote(amount, corridor, payment)` | the same payload `/api/quote` returns |
| `get_fee_rule(rule_id)` | rate, fixed fees, source URL, date checked, modelling note |
| `list_corridors()` | the five priced corridors and three payment products |
| `get_fx_snapshot()` | the rate table and how old it is |

Then the answer is checked, after generation, against the tool output:

1. **Number provenance.** Every figure in the prose must appear in a tool
   result. "About 5,200" when the engine said 5,129.07 is rejected — not
   corrected, rejected.
2. **Rule citation.** At least one rule id must be cited, and every cited id
   must be one that appeared in the quote.
3. **No observed-data claims.** Modelled figures may not be described as live,
   real-time, confirmed, observed or guaranteed.

A failure gets one repair attempt. A second failure is **discarded** and the
deterministic template is served instead — and that template passes the same
validator, parametrised over all fifteen corridor × product combinations. There
is no path through the code that returns unvalidated prose.

The guardrail was mutation-tested: eight deliberate breaks — provenance check
disabled, citation membership dropped, empty citation allowed, claim language
allowed, dates read as numbers, prose numbers not harvested, caveats excluded,
verdict forced to ok — and **all eight were caught** by the existing tests.

## Three tiers, and only one of them is in use

| tier | meaning | in use |
| --- | --- | --- |
| `modeled` | calculated from published rates | **yes, everything** |
| `paypal_quote` | a figure PayPal itself quoted | no |
| `sandbox_observed` | captured from a sandbox transaction | client built, not wired |

Every fee line carries `tier: "modeled"` and the UI renders a `modeled` chip
above the result with "Not a PayPal quote" beside it. The payload makes it
impossible to present a modelled figure as a live one by accident.

## What it models

Seven rules, all from PayPal's US merchant fee schedule (last checked
2026-10-26). Six cite the fee page; the international surcharge cites PayPal's
October 2026 US merchant fee PDF.

| rule id | rate | fixed (USD received) | borne by |
| --- | --- | --- | --- |
| `commercial_goods_services_transfer` | 2.99% | — | receiver |
| `commercial_paypal_checkout` | 3.49% | 0.49 USD | receiver |
| `invoice_checkout` | 3.49% | 0.49 USD | receiver |
| `invoice_card` | 2.99% | 0.49 USD | receiver |
| `intl_surcharge_us` | 1.50% | — | receiver |
| `fx_spread_payer_other_currency` | 4.00% | — | **payer** |
| `fx_spread_balance_conversion` | 3.00% | — | receiver |

Five corridors, all into a **US business account holding USD**: `EUR→USD`,
`GBP→USD`, `PHP→USD`, `AUD→USD`, `INR→USD`. A corridor is a (client country,
currency) pair, not just a currency — the 1.50% surcharge keys off the country.

Three payment products, because "invoice" is not one price. PayPal's invoicing
table splits by how the client funds it, and the card row costs the same
percentage as a direct send *plus* a fixed fee. Reusing one rate for both would
understate the card invoice by exactly that fixed fee.

Exchange rates are ECB reference rates via the frankfurter mirror, snapshotted
to a dated file. PayPal's own spread is priced separately as a fee line; the ECB
rate is not presented as a PayPal rate.

## What it deliberately does not model

Each of these is a stated reason, not an omission:

- **Pay Later (4.99%).** It is a customer funding choice, not a merchant
  setting, so it cannot be selected before the invoice is paid. Offering it as a
  fourth product would imply you choose it.
- **Pay by Bank / ACH (1%, capped at $10).** The cap needs a fee type the engine
  does not have, and the product is not offered to cross-border payers on these
  corridors.
- **Holding a foreign balance and converting later.** Not available for this
  account, so it is rejected with that reason rather than offered and guessed
  at.
- **An invoice already in USD.** There is no receiver-side conversion to remove,
  so no cheaper path is claimed.

## Run it

```bash
pip install -r requirements.txt
python -m uvicorn app.main:app --port 8000
```

No key required. Without `OPENROUTER_API_KEY` every explanation is the
deterministic template, and `/api/health` says so (sample response, as of this
writing):

```json
{"status":"ok","fee_retrieved_date":"2026-10-26","fx_snapshot_date":"2026-10-27",
 "fx_age_days":0,"fx_stale":false,"model_configured":false,"tier":"modeled"}
```

Copy `.env.example` to `.env` to enable the model. The PayPal sandbox
credentials in it are only used by `scripts/check_sandbox.py`; the web app never
calls the PayPal API.

The model key is optional and free-tier. Without it every explanation is the
template; with it, the first explanation of a quote takes 20–60 seconds on a
loaded free provider and every repeat is instant from an in-process cache. When
the daily quota runs out the template comes back and says why.

## API

| endpoint | purpose |
| --- | --- |
| `POST /api/quote` | price one invoice: `{amount, corridor, payment}` |
| `POST /api/explain` | the same, plus an explanation and the tool trace |
| `GET /api/config` | corridors, products, dates, disclaimer |
| `GET /api/health` | rate staleness and whether a model is configured |

Money is serialised as a **string**, never a float — a float would reintroduce
at the last moment exactly the rounding drift the engine spends its time
avoiding.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q        # 189 passing
```

Pricing tests read frozen fixtures, never the live data files. If they read the
live snapshot, every legitimate rate refresh would break the suite and the
tempting repair would be to edit the expected numbers until it passed — which
quietly destroys the test's meaning. One separate test does open the shipped
snapshot, but only to assert the engine can parse it; it checks no prices, so a
refresh cannot move it.

## Honesty statement

- Every figure is a **modelled estimate** from PayPal's published US fee
  schedule. It is not a PayPal quote and not financial advice.
- Percentage fees are applied to the invoice amount and converted once; PayPal
  does not publish its assessment order, so that is a documented simplification.
- Fixed fees are charged in the currency received, per PayPal's "based on
  currency received" wording. This account receives USD.
- The exchange rate is the ECB's, not PayPal's. It is shown with its date and
  flagged once it is more than four days old.
- One known gap in the guardrail, stated rather than hidden: a bare integer with
  no currency, decimal point or percent sign is read as a count and allowed if
  small. Counts are capped at 20 to keep that narrow.

## Deployment

`render.yaml` deploys one free web service with a health check on `/api/health`.
See [DEPLOY.md](DEPLOY.md) for the environment variables, the cold-start
caveat on the free tier, and how to refresh the rate snapshot.

## Licence

MIT.

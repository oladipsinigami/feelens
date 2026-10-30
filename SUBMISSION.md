# Devpost submission — copy-paste source

Everything here is written to be pasted into the Devpost form. Every figure was
read off the running engine on 2026-10-30 (FX snapshot 2026-10-27), not from
memory. If a number changes, re-run `/api/quote` before pasting.

---

## Project name

FeeLens

## Elevator pitch (tagline, ~200 chars)

PayPal's fee is invisible until after the money lands. FeeLens prices it before
the invoice goes out — every line sourced, every number computed by an engine the
AI is not allowed to touch.

## Inspiration

A freelancer invoices a client abroad for €5,000 and finds out what it cost only
when it arrives. By then three separate charges have been applied — the product
rate, an international surcharge, and a currency-conversion spread — and none of
them were visible when the invoice was written.

The part that stuck with me is that the amount is not fixed by the money. It is
fixed by **which button the client presses**. On the same €5,000, same client,
same day:

| How it arrives | PayPal keeps | You keep |
| --- | --- | --- |
| Client sends money directly | 417.57 USD | 5,157.43 USD |
| Invoice, paid from a PayPal account | **445.94 USD** | **5,129.06 USD** |
| Invoice, paid by card or wallet | 418.06 USD | 5,156.94 USD |

**28.37 USD** of difference that exists purely because of a funding choice nobody
tells you about in advance. That is a design choice, not a fact — and it can be
read before the invoice is sent rather than after.

## What it does

Pick an amount, the client's country, and how the money arrives. FeeLens returns:

- **the net** — what actually lands in the account's own currency
- **every fee line**, each carrying the rule id it came from and a link to the
  PayPal page that states it
- **the effective rate**, because "3.49%" is never the number anyone experiences
- **a cheaper route with its cost stated** — re-denominating the invoice into USD
  removes the receiver's 3% conversion spread, but it hands a German client a USD
  invoice and charges them a 4% spread instead. Both sides are priced by the same
  engine, so the app can say what it actually is: you keep 167.25 USD more, your
  client pays 223.00 USD more, and the pair of you end up **55.75 USD worse off**
  — 669.00 USD a year if the invoice repeats monthly.
- **a 1% rail for scale** — 55.75 USD against PayPal's 445.94, labelled
  hypothetical everywhere, because FeeLens prices no other provider
- **an explanation** in plain language, from a language model when one is
  configured and from a deterministic template when it is not
- **a real invoice in PayPal.** One button calls PayPal's Invoicing API
  (`POST /v2/invoicing/invoices`), reads the invoice back, and shows the id,
  status and amount PayPal returned. The invoice is `sandbox_observed`; the fee
  is still `modeled`, because creating an invoice does not charge it. The card
  says both, in those words.

## How we built it

**Python / FastAPI, vanilla JS, no framework, no database.** The whole app is a
deterministic fee engine plus a thin agent layer.

The engine computes every figure from PayPal's published US merchant fee schedule
— seven rules, each with a source URL, a date checked, and a `verified` flag. The
rates live in one JSON file. Money is handled as `Decimal` and serialised as
**strings**, never floats, because a float at the last moment reintroduces exactly
the rounding drift the engine spends its time avoiding.

The interesting constraint was the AI. The model has **four tools and no
arithmetic**. The system prompt contains no fee rates at all — a model that was
never shown `3.49` and `1.50` cannot add them. After generation, the answer is
checked against the tool output:

1. **Number provenance** — every figure in the prose must appear in a tool
   result. "About 5,200" when the engine said 5,129.06 is rejected, not corrected.
2. **Rule citation** — at least one rule id must be cited, and every cited id must
   be one that appeared in the quote.
3. **No observed-data claims** — modelled figures may not be called live,
   confirmed, observed or guaranteed.

A failure gets one repair attempt; a second failure is discarded and a
deterministic template is served instead. The template passes the same validator,
parameterised over all fifteen corridor × product combinations, so there is no
path through the code that returns unvalidated prose.

The guardrail was mutation-tested: eight deliberate breaks — provenance disabled,
citation membership dropped, empty citation allowed, claim language allowed, dates
read as numbers, prose numbers not harvested, caveats excluded, verdict forced to
ok — and all eight were caught.

Exchange rates are ECB reference rates via a dated snapshot. PayPal's own spread
is priced separately as a fee line; the ECB rate is never presented as PayPal's.

## Challenges we ran into

**Making the AI unable to lie about a number.** Prompting it not to guess is not a
control. The control is that the numbers are not in its context and its output is
checked afterwards against what the tools actually returned. That took three
rewrites of the guardrail before the tests stopped finding holes.

**Deciding what not to build.** Five corridors, three payment products, and a
written refusal for everything else — Pay Later, Pay by Bank, holding a foreign
balance, and pricing Wise or Revolut. Each refusal is a stated reason in the UI
rather than an omission, because a fee tool that guesses at a rate is worse than
one that admits it has no sourced rate.

**The free-tier budget.** The model is free but capped at 50 requests a day,
shared by everyone who opens the link. So the public deployment limits what can be
*spent*, not what can be *calculated*: 6 explanations per visitor per 10 minutes,
44 upstream requests per process per day. Hitting either does not return an error
— the quote is still computed and the explanation falls back to the template with
a note saying which limit was reached. The numbers are never affected, because the
model never had them.

**The framing.** The first version called the USD invoice a "saving". It is not.
It moves the cost to the client. That is now priced, netted, and stated — the app
tells you when the cheaper route makes you both worse off.

## Accomplishments we're proud of

- **253 tests**, with pricing tests reading frozen fixtures rather than live data
  files. If they read the live snapshot, every legitimate rate refresh would break
  the suite and the tempting repair would be to edit the expected numbers until it
  passed — which quietly destroys the test's meaning.
- A **three-tier data model** — `modeled`, `PayPal quote`, `sandbox_observed` —
  where every fee line carries its tier, and the payload makes it impossible to
  present a modelled figure as a live one by accident.
- **The fee schedule is in a file you can read**, not in a prompt. That is the
  whole thesis: the fee is a design choice, and design choices are inspectable.

## What we learned

An LLM is a bad calculator and a good explainer, and the fix is not a better
prompt — it is removing the arithmetic from the model's reach and verifying its
prose against the source of truth afterwards. Once that is in place, the model
becomes safe to use for the part it is actually good at.

We also learned that honesty is a feature you can demo. The app says "not a
PayPal quote", prices the other side of a saving, labels a benchmark
hypothetical, and warns about Seller Protection *before* the choice rather than
after the result.

## What's next

- **Calibration against a real sandbox payment.** The invoice is now created
  through the Invoicing API, but an invoice is not a payment. Capturing one and
  reconciling PayPal's `seller_receivable_breakdown` against the model is the
  next step, and the reconciliation is already built and tested in
  `app/paypal/reconcile.py`.
- **CSV import.** A merchant exports their Transaction Search CSV and FeeLens
  compares PayPal's reported `fee_amount` against the model, per corridor, on
  their own volume. This is the honest way to get a merchant's *effective* rate —
  PayPal publishes no pricing API, so the number has to come from their own data.
- More corridors, and the same treatment for the payer's side of a card payment.

## Built with

`python` `fastapi` `vanilla-js` `decimal` `pydantic` `httpx` `openrouter`
`paypal-rest-api` `render` `github-actions`

---

# Demo video script (target 2:45, hard limit 3:00)

Rules say under three minutes, must show the project functioning, must be public
on YouTube, no third-party trademarks or copyrighted music. Record at 1080p,
browser zoom 110%, no cursor wobble.

**Before recording:** open the live URL, submit one explanation to warm the cache,
then reload. A cold free-tier model takes 20–60s and will eat your runtime.

| time | on screen | say |
| --- | --- | --- |
| 0:00–0:15 | README table | "A freelancer invoices a client for €5,000. PayPal keeps 417.57 USD, or 445.94 USD, or 418.06 USD — same money, same client, same day. The difference is which button the client presses. Nobody is told before the invoice is sent." |
| 0:15–0:35 | app, amount €5,000, corridor EUR, product = invoice paid from PayPal | "FeeLens prices it before you send. Five corridors, three payment products." |
| 0:35–0:55 | three product cards | "28.37 USD apart. And the invoice paid from a PayPal account is the *expensive* one — the opposite of what you'd guess." |
| 0:55–1:20 | fee lines | "Every line carries its rule id and a link to the page that states it. 3.49% product, 1.50% international surcharge, 3.00% conversion spread. The effective rate is 8.00%, not 3.49%." |
| 1:20–1:45 | scroll to savings + client side | "It suggests a cheaper route — invoice in USD, drop the conversion spread. You keep 167.25 USD more. But your client is now paying a USD invoice from a EUR balance, and that costs them 223.00. The pair of you are 55.75 USD worse off, 669.00 a year." |
| 1:45–2:00 | rail card | "And a scale: a hypothetical 1% rail would take 55.75 instead of 445.94. Labelled hypothetical, because FeeLens prices no other provider." |
| 2:00–2:30 | explanation panel | "The explanation is from a model that has no arithmetic. Four tools, no rates in its prompt, and every number in its answer is checked against the tool output afterwards. If it invents a figure, the answer is thrown away and a deterministic template is served instead." |
| 2:30–2:45 | health endpoint or README | "Every figure is modelled from PayPal's published US fee schedule, with sources and dates. Not a PayPal quote, not financial advice. It's the fee, before you commit to it." |

Do not claim a live PayPal quote anywhere in the narration. The tier is `modeled`
and the page says so.

---

# Pre-submission checklist

- [ ] Devpost form fields pasted and proofread
- [ ] Video uploaded to YouTube, **public**, under 3:00
- [ ] Repo public with MIT detected in the About section — **done**
- [ ] Repo homepage links the live demo — **done** (`https://feelens-su23.onrender.com`)
- [ ] Live app awake and warm when judging starts
- [ ] Uptime monitor pinging `/api/health` every 10 min (free tier sleeps after 15)
- [ ] Render API key revoked
- [ ] OpenRouter key rotated after the hackathon
- [ ] `FEELENS_ARCHITECTURE.md` and `FEELENS_BUILD_PROMPT.md` resolved — they are
      committed, unlinked from the README, and still contain "Use APIMatic" lines
      that are no longer true

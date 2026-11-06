# Devpost submission — copy-paste source

Everything here is written to be pasted into the Devpost form. Every figure was
read off the running engine on 2026-11-06 (FX snapshot 2026-11-05), not from
memory. If a number changes, re-run `/api/quote` before pasting.

---

## Links (paste into the form's URL fields)

| field | value |
| --- | --- |
| Live demo / "Try it out" | https://feelens-su23.onrender.com |
| Source code / GitHub | https://github.com/oladipsinigami/feelens |
| Demo video | (YouTube, public) |
| Built with PayPal | PayPal REST API — Invoicing v2, sandbox |

The repository is also linked from the live page's footer, so a judge who starts
at the demo can reach the code without going back to Devpost.

---

## Project media (image gallery)

Seven screenshots of the live site, taken 2026-11-06 at 3000x2000 (exactly 3:2,
the ratio Devpost asks for). Nothing is a mockup: each one is the deployed app
after pricing a real 5,000 EUR invoice to a German client.

Upload in this order — the first image is the thumbnail.

| # | file | what it shows |
| --- | --- | --- |
| 1 | `feelens-01-result.png` | Net received, the three payment doors, and the 28.55 USD gap between them |
| 2 | `feelens-02-breakdown.png` | "Where it goes": every fee line with its rule id, source and FX note |
| 3 | `feelens-03-savings.png` | The 1% rail benchmark, the cheaper route, and who pays for it |
| 4 | `feelens-04-invoice.png` | A real sandbox invoice (INV2-…, DRAFT, 5,000.00 EUR) read back from PayPal |
| 5 | `feelens-05-explain.png` | The AI answer with its caveats and the "13 figures traced, 4 rules cited" line |
| 6 | `feelens-06-light-top.png` | The light theme: cream stock and gold, the form before submitting |
| 7 | `feelens-07-light-result.png` | The light theme on the result, same numbers as image 1 |

Files live outside the repository in `Downloads\FeeLens-devpost-images\` so the
repo stays free of multi-megabyte PNGs. Each is 0.19-0.31 MB, well under the
5 MB limit.

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
| Client sends money directly | 420.34 USD | 5,191.66 USD |
| Invoice, paid from a PayPal account | **448.89 USD** | **5,163.11 USD** |
| Invoice, paid by card or wallet | 420.83 USD | 5,191.17 USD |

**28.55 USD** of difference that exists purely because of a funding choice nobody
tells you about in advance. That is a design choice, not a fact — and it can be
read before the invoice is sent rather than after.

## What it does

Pick an amount, the invoice currency, the client's country, and how the money
arrives. FeeLens returns:

- **the net** — what actually lands in the account's own currency
- **all three products side by side** — the same invoice through every funding
  choice at once, with the gap between the cheapest and dearest stated in
  dollars. Clicking a card re-prices the page, so the argument is something the
  judge watches happen rather than assembles themselves
- **every fee line**, each carrying the rule id it came from and a link to the
  PayPal page that states it
- **the effective rate**, because "3.49%" is never the number anyone experiences
- **a cheaper route with its cost stated** — re-denominating the invoice into USD
  removes the receiver's 3% conversion spread, but it hands a German client a USD
  invoice and charges them a 4% spread instead. Both sides are priced by the same
  engine, so the app can say what it actually is: you keep 168.36 USD more, your
  client pays 224.48 USD more, and the pair of you end up **56.12 USD worse off**
  — 673.44 USD a year if the invoice repeats monthly.
- **a 1% rail for scale** — 56.12 USD against PayPal's 448.89, labelled
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
— six rules, each with a source URL, a date checked, and a `verified` flag. The
rates live in one JSON file. Money is handled as `Decimal` and serialised as
**strings**, never floats, because a float at the last moment reintroduces exactly
the rounding drift the engine spends its time avoiding.

The interesting constraint was the AI. The model has **four tools and no
arithmetic**. The system prompt contains no fee rates at all — a model that was
never shown `3.49` and `1.50` cannot add them. After generation, the answer is
checked against the tool output:

1. **Number provenance** — every figure in the prose must appear in a tool
   result. "About 5,200" when the engine said 5,163.11 is rejected, not corrected.
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
after the result. It also refuses to answer when the answer would be nonsense: an
invoice smaller than PayPal's fixed fee is a `422` with the reason attached, not
a confident negative net.

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

# Demo video script (as recorded: 2:10, hard limit 3:00)

Rules say under three minutes, must show the project functioning, must be public
on YouTube, no third-party trademarks or copyrighted music. Recorded at 1600×900,
dark theme, on the live URL.

The narration is timed to the screen, not the other way round: each beat was
synthesised first, the recording waited out each clip, and the subtitles come
from the same manifest. That is why the words and the picture cannot drift.

**Before a re-record:** open the live URL, submit one explanation to warm the
cache, then reload. A cold free-tier model takes 20–60s and will eat the runtime.

| time | on screen | say |
| --- | --- | --- |
| 0:00–0:12 | result card, `$5,191.66` | "Five thousand euros. A client in Germany pays it. By the time it reaches your PayPal balance, it is not five thousand euros any more. And nothing on the invoice told you that." |
| 0:12–0:22 | the four inputs, each outlined as named | "FeeLens prices it before you send. Four choices: the amount, the currency you invoice in, where your client is, and how they pay." |
| 0:22–0:38 | Calculate, then the headline facts | "Five thousand euros becomes five thousand, six hundred and twelve dollars. PayPal keeps four hundred and twenty. You keep five thousand, one hundred and ninety-one. Seven point four nine percent. Not the three point four nine on the fee page." |
| 0:38–0:57 | three product cards, then the invoice-from-PayPal card | "The fee is not set by the money. It is set by which button the client presses. Direct: four hundred and twenty. Card: four hundred and twenty-one. Invoice, paid from PayPal: four hundred and forty-nine. Twenty-eight dollars apart. And the most familiar option is the expensive one." |
| 0:57–1:08 | fee lines and their rule ids | "Every line carries the rule that produced it, and a link to the page that states it. The model never does arithmetic. It calls the engine, and explains what came back." |
| 1:08–1:28 | cheaper-route card, then the client side | "It will suggest a cheaper route. Invoice in dollars, skip the conversion, keep a hundred and sixty-eight more. But your client would be paying a dollar invoice from a euro balance, and that costs them two hundred and twenty-four. Together, you are fifty-six dollars worse off. FeeLens says so, instead of hiding it." |
| 1:28–1:45 | press "Create invoice in PayPal sandbox", hold on the id | "And it is not a calculator sitting next to PayPal. This creates a real draft invoice through PayPal's Invoicing API, and reads back the id PayPal returned. The invoice is real. The fee is still modelled, and the card says both." |
| 1:45–1:57 | ask "why is it so high?", read the answer | "Ask why, and an agent answers with no exchange rates in its prompt. Every figure is checked against the engine afterwards. Invent one, and the answer is thrown away for a deterministic one." |
| 1:57–2:07 | back to the headline | "The fee is not a fact. It is a design choice PayPal made. And it is readable before the invoice goes out. That is FeeLens." |
| 2:07–2:10 | end card | — |

Do not claim a live PayPal quote anywhere in the narration. The tier is `modeled`
and the page says so.

---

# Pre-submission checklist

- [ ] Devpost form fields pasted and proofread
- [ ] Video uploaded to YouTube, **public**, under 3:00
- [x] Repo public with MIT detected in the About section
- [x] Repo homepage links the live demo — `https://feelens-su23.onrender.com`
- [x] Live app awake and warm when judging starts — `.github/workflows/keepalive.yml` pings `/api/health` every 5 min, 06:00–23:55 UTC
- [x] Exchange rate inside its four-day window through 2026-12-15 — `.github/workflows/refresh-fx.yml` refreshes the snapshot every weekday at 05:30 UTC
- [ ] **Render API key revoked** — still live in the Render account; revoke it
      (Account Settings → API Keys). Nothing in the deployment depends on it.
- [ ] OpenRouter key rotated after the hackathon
- [x] `FEELENS_ARCHITECTURE.md` and `FEELENS_BUILD_PROMPT.md` resolved — both now
      open with a **SUPERSEDED** banner naming the dropped APIMatic claim and
      pointing at README/DEPLOY/SUBMISSION. Kept rather than deleted so the
      planning history stays readable; neither is linked from the README.

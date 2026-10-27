# FeeLens: Architecture (v3)

**AI pre-payment cost auditor for PayPal** | PayPal AI Hackathon | Deadline: Nov 12, 2026, 12:00 pm Pacific = **9:00 pm WAT** (submit by **Nov 11**)
Hackathon inspiration category: **Business Solutions** ("an invoicing tool built for freelancers who bill in multiple currencies") with an agentic action step.

> Status: proposed design, updated after reading PayPal's developer docs **and the official Devpost rules and sponsor pages**.
> Items marked **CONFIRMED** were read in PayPal's docs. Items marked **VERIFY** were not, so check them before relying on them.

---

## R. Official rules that shape the build

Read from `paypalaihackathon.devpost.com/rules` and the sponsor pages on Oct 5, 2026. Rules can be amended, so re-read before you submit.

### Dates (Nigeria time)

| Event | Pacific time | WAT |
|---|---|---|
| Submission deadline | Nov 12, 2026, 12:00 pm | **Nov 12, 9:00 pm** (US daylight saving ends Nov 1) |
| Judging period | Dec 1, 2:00 pm to Dec 15, 8:00 am | Dec 1, 10:00 pm to Dec 15, 4:00 pm |
| Winners announced | around Dec 21 | around Dec 21 |

Your project must remain available **free of charge and without restriction** for judges until the judging period ends (Dec 15). Plan hosting and LLM budget for that.

### How you will be judged

Stage 1 is pass/fail: the project must reasonably fit the theme and **reasonably apply the PayPal APIs/SDKs**. PayPal integration must be central, not decorative.
Stage 2 uses **five equally weighted criteria**:

| Criterion | What the rules ask | What it means for FeeLens |
|---|---|---|
| Technological Implementation | How thoroughly and skillfully the project uses PayPal and AI; genuine, non-trivial, working | FX Quote + Invoicing + order/capture calibration, a tested fee engine, and a real tool loop |
| Design | A complete, coherent product experience, **not just a technical proof of concept** | Loading, error and empty states; example presets; a clear flow; no raw JSON on screen |
| Potential Impact | A credible, specific case for a real problem and audience, and the solution addresses it **as demonstrated** | Show the real problem and the fix on screen. Cite only sources you checked yourself |
| Innovation/Idea | Creative, and different from existing concepts | Pre-transaction audit plus an action, with honest data-tier labeling |
| Presentation | Video shows the project working end to end; problem, audience, why it matters | One story, one flow, under 3 minutes |

Judging may include automated AI-driven analysis. Write the README and project description clearly and explicitly, and **list the exact PayPal endpoints you call**.

### Submission requirements (all are required)

- [ ] Text description of features and functionality
- [ ] Working demo: a hosted URL **or** complete setup and run instructions in the repo. A mockup or static prototype does not count
- [ ] **Public GitHub repo with an open source license file** visible in the repo's About section
- [ ] Demo video **under 3 minutes**, **public on YouTube**, showing the project working, with **no third-party trademarks or copyrighted music/material** unless you have permission
- [ ] Everything in English
- [ ] Test credentials or sandbox login details if judges need them, in the README or project description. Never include production secrets
- [ ] Original work, built (or significantly updated) after Oct 1. FeeLens is new, so start the repo now and let the commit history show it

### Prizes and eligibility

- A project can win **one grand prize or one honorable mention, plus at most one sponsor prize**.
- Nigeria is not on the listed excluded countries (Brazil, Quebec, Russia, Crimea, Cuba, Iran, North Korea, and other comprehensively sanctioned places). This is not legal advice. Winners also handle tax forms (such as W-8BEN) and any wiring or currency-exchange fees.
- If a rule is ambiguous (for example, whether showing PayPal's own screens counts as a "third-party trademark"), ask in the Discussions tab or at support@devpost.com. The rules invite written clarification requests.

### Sponsor prizes: what they actually require

| Sponsor | Prize | Real requirement | Decision |
|---|---|---|---|
| **APIMatic** | $1,000 + 6 months Business, 3 winners | Use the **Context Plugin** in your AI coding agent; free 1-month Basic for anyone who used it | **Use it** (near-zero cost) |
| **Render** | $1,000 / $750 / $500 in credits | Deploy on Render; Render Workflows is a deeper option; $50 credits are available | **Use it** (you need hosting anyway) |
| **AG Grid** | $5,000 / $2,000 / $1,000 x3 | Built around **AG Studio**: custom widgets, theming, the Studio Agent Framework, React, 45-day trial license. A plain fee table would not qualify | **Skip** (would force a React rewrite and expand scope) |
| Bryntum, Channel3 | $1,000 x3 / $1,500 | No natural fit | Skip |

Since only one sponsor prize can be won, do not chase several. Using APIMatic and Render costs almost nothing, so do both.

---

## 0. What changed from v1

| Change | Why |
|---|---|
| **Primary action is now "draft the invoice in the cheaper currency"** (Invoicing API), not "create an order" | Freelancers get paid through invoices. It turns FeeLens from an advisor into an agent that acts, which the hackathon explicitly values. |
| **FX rates come from PayPal's Exchange Rate Quote API**, with a cached snapshot as fallback | Deeper PayPal integration. Falls back safely if the sandbox app lacks the scope. |
| **New calibration step: sandbox order + capture** compares your modeled fee against the fee and exchange rate PayPal actually returns | This is your proof that the numbers are real, not just plausible. |
| **Three data tiers shown in the UI** (modeled / PayPal quote / sandbox-observed) | Honest about what is live and what is modeled. |
| **Phase 1 is now a sandbox spike** before any app code | De-risks the unknowns (scopes, sandbox FX behavior) in the first 2 days. |
| **Dev tooling and sponsor section added** | Specs for your coding AI, Render for hosting, APIMatic Context Plugin. |
| **v3: official rules added (section R)** | Judging criteria, submission requirements, hosting through Dec 15, LICENSE and README, and corrected sponsor advice. AG Grid is now a skip. |

---

## 1. Core principle

```
Deterministic fee engine   -> computes every number
PayPal-sourced data        -> FX reference rate (Quote API), real fee/rate from sandbox capture
LLM agent                  -> calls tools, explains, compares, then drafts the invoice
```

The LLM **never does arithmetic**. Every fee line traces to a rule ID in the fee config. Every figure in the UI carries one of three badges:

| Badge | Meaning |
|---|---|
| `modeled` | Computed from `fees.json` rules (published rates you entered) |
| `PayPal quote` | Reference rate returned by PayPal's Exchange Rate Quote API |
| `sandbox-observed` | Fee and exchange rate returned by a real sandbox capture |

Never present a `modeled` figure as live.

---

## 2. System overview

```
┌──────────────────────────────────────────────────────────────┐
│                     Frontend (vanilla JS)                     │
│  Input form -> Before/After comparison (AG Grid optional)     │
│  Data-tier badges -> "Create invoice draft" -> Calibration    │
└────────────────────────────┬─────────────────────────────────┘
                             │ HTTP (JSON)
┌────────────────────────────▼─────────────────────────────────┐
│                       FastAPI backend                         │
│                                                               │
│  /api/audit ──► Agent loop (LLM + tools)                      │
│                    │                                          │
│   ┌────────────┬───┴──────────┬──────────────┬────────────┐   │
│   ▼            ▼              ▼              ▼            ▼   │
│ get_fee_   get_fx_      compare_paths   create_invoice_  run_  │
│ schedule   quote                        draft           calib. │
│   │            │              │              │            │   │
│   ▼            ▼              ▼              ▼            ▼   │
│ ┌────────┐ ┌─────────┐  ┌──────────┐  ┌───────────┐ ┌────────┐│
│ │ Fee    │ │ FX      │  │ Path     │  │ Invoices  │ │ Orders ││
│ │ engine │ │ source  │  │ validity │  │ client    │ │ client ││
│ │(Decimal)│ │(quote → │  │ rules    │  │           │ │+capture││
│ └───┬────┘ │snapshot)│  └──────────┘  └─────┬─────┘ └───┬────┘│
│     ▼      └────┬────┘                      │           │     │
│  fees.json      │                           │           │     │
│  fx_snapshot.json                           │           │     │
└─────────────────────────────────────────────┼───────────┼─────┘
                                              ▼           ▼
                                    PayPal Sandbox (api-m.sandbox.paypal.com)
                           Quote API · Invoicing v2 · Orders v2 / capture
```

---

## 3. Folder structure

```
feelens/
├── .env                      # secrets, never committed
├── .env.example
├── .gitignore
├── README.md
├── requirements.txt
├── render.yaml               # optional, Render deploy config
├── spikes/
│   └── sandbox_spike.py      # Phase 1: token, FX quote, invoice, order+capture
├── specs/                    # PayPal OpenAPI files for your coding AI (see section 12)
├── app/
│   ├── main.py               # FastAPI app, routes
│   ├── config.py             # env + paths
│   ├── agent/
│   │   ├── loop.py           # tool-calling loop
│   │   ├── llm.py            # ONE function: call_llm(messages, tools)
│   │   ├── tools.py          # tool schemas + dispatcher
│   │   └── prompts.py        # agent system prompt
│   ├── engine/
│   │   ├── models.py         # Transaction, FeeLine, CostBreakdown, ObservedCapture
│   │   ├── fees.py           # load fees.json, resolve applicable rules
│   │   ├── calculator.py     # calculate_effective_cost (Decimal math)
│   │   ├── paths.py          # compare_paths + validity rules
│   │   └── calibrate.py      # modeled vs observed comparison, implied markup
│   ├── paypal/
│   │   ├── client.py         # OAuth token (cached) + HTTP wrapper
│   │   ├── fx.py             # Exchange Rate Quote API + snapshot fallback
│   │   ├── invoices.py       # create draft, send
│   │   └── orders.py         # create order, capture, read seller_receivable_breakdown
│   └── data/
│       ├── fees.json         # fee rules (single source of truth)
│       └── fx_snapshot.json  # cached rates + date
├── static/
│   ├── index.html
│   ├── app.js
│   └── style.css
└── tests/
    ├── test_calculator.py
    ├── test_paths.py
    ├── test_fee_config.py
    ├── test_calibrate.py
    └── test_fx_direction.py  # guards against inverted rates
```

---

## 4. Fee config schema (`fees.json`)

```json
{
  "schema_version": 1,
  "retrieved_date": "YYYY-MM-DD",
  "rules": [
    {
      "id": "txn_fee_commercial_intl",
      "label": "Commercial transaction fee",
      "type": "percent_plus_fixed",
      "rate_pct": 2.99,
      "fixed": { "USD": 0.49 },
      "applies_to": { "account_types": ["business"], "transaction_types": ["goods_services"] },
      "source_url": "https://...",
      "verified": false
    },
    {
      "id": "cross_border_surcharge",
      "label": "Cross-border fee",
      "type": "percent",
      "rate_pct": 1.5,
      "applies_to": { "cross_border": true },
      "source_url": "https://...",
      "verified": false
    },
    {
      "id": "fx_markup_default",
      "label": "Currency conversion markup",
      "type": "percent_over_base_rate",
      "rate_pct_range": [3.0, 4.5],
      "rate_pct_used": 4.0,
      "applies_to": { "requires_conversion": true },
      "source_url": "https://...",
      "verified": false
    }
  ]
}
```

> The values above are **placeholders from the research notes, not verified rates.** Replace them with figures from PayPal's official fees page for your account type. Keep `verified: false` until you do. Use ranges in the demo, not false precision.

---

## 5. Data models (`engine/models.py`)

```python
from decimal import Decimal
from dataclasses import dataclass

@dataclass
class Transaction:
    amount: Decimal
    source_currency: str          # what the payer pays in
    receive_currency: str         # receiver's PayPal account currency
    account_region: str
    account_type: str             # "business" | "personal"
    transaction_type: str         # "goods_services" | "friends_family"
    holds_receive_currency: bool  # hardcoded for MVP; List Balances API is a stretch

@dataclass
class FeeLine:
    rule_id: str
    label: str
    amount: Decimal               # in source currency
    verified: bool
    tier: str                     # "modeled" | "paypal_quote" | "sandbox_observed"

@dataclass
class CostBreakdown:
    path_id: str
    lines: list[FeeLine]
    total_fees: Decimal
    effective_rate_pct: Decimal
    net_received: Decimal         # in receive_currency
    fx_source: str                # "paypal_quote" | "snapshot"
    assumptions: list[str]

@dataclass
class ObservedCapture:            # from seller_receivable_breakdown
    gross_amount: Decimal
    paypal_fee: Decimal
    net_amount: Decimal
    receivable_amount: Decimal | None   # only present when currencies differ
    exchange_rate: Decimal | None       # only present when currencies differ
```

Use `decimal.Decimal` for all money. Never floats.

---

## 6. PayPal integration (what is confirmed and what is not)

### 6.1 Confirmed from PayPal docs

| Item | Detail |
|---|---|
| Sandbox base URL | `https://api-m.sandbox.paypal.com` |
| Token endpoint | `POST /v1/oauth2/token` (client credentials) |
| FX quote | `POST /v2/pricing/quote-exchange-rates` |
| FX quote scope | `https://uri.paypal.com/services/pricing/quote-exchange-rates/read` |
| FX quote request | `quote_items[]` with `base_currency`, `base_amount`, `quote_currency`, `markup_percent`, `fx_id` |
| FX quote response | `exchange_rate_quotes[]` with `base_amount`, `quote_amount`, `exchange_rate`, `fx_id`, `expiry_time`, `rate_refresh_time`, `update_time` |
| Link quote to order | `payment_instruction.payee_receivable_fx_rate_id` on create order |
| Create draft invoice | `POST /v2/invoicing/invoices` (returns 201; currency set in `detail.currency_code`) |
| Send invoice | `POST /v2/invoicing/invoices/{id}/send` (Draft → Sent → Viewed → Paid) |
| Capture order | `POST /v2/checkout/orders/{order_id}/capture` (supports `PayPal-Request-Id` header) |
| Capture breakdown | `seller_receivable_breakdown`: `gross_amount`, `paypal_fee`, `net_amount`, plus `receivable_amount` and `exchange_rate` **only when the payment currency differs from the receiving account's currency** |
| Related errors | "Currency not allowed", "Currency mismatch", "Unsupported payee currency" |

### 6.2 Still VERIFY

- Whether your sandbox app is granted the FX quote scope (the doc says rate details depend on the contract agreement). **Test on day 1.** Expect a possible 403.
- **Rate direction.** In the quote request, `base_currency` is described as the merchant's settlement currency and `quote_currency` as the transaction currency. Confirm the direction with a unit test (`test_fx_direction.py`) before using the rate in calculations.
- Whether the sandbox applies realistic fees and FX spreads at capture. If it does not, say so in the demo and treat captures as proof of the **flow**, not of real-world fees.
- Whether you can create a sandbox business account with a receiving currency different from the payment currency (needed to get `exchange_rate` in the capture response).
- The exact required fields for the create-order and create-invoice request bodies.
- Whether the account supports holding balances in each target currency in your region.
- Whether the sandbox exposes a BUY/SELL rate endpoint. The OAuth scope list mentions an exchange-currency scope. If it works, you could measure PayPal's spread directly.

### 6.3 FX source fallback chain

```
1. Try Quote API  -> tag figures "paypal_quote"
2. On 401/403/error or missing scope -> use fx_snapshot.json, tag "snapshot (modeled)"
```

Log which source was used on every audit so you can state it truthfully in the demo.

### 6.4 Calibration (the "proof" step)

1. Create a sandbox order in the payer's currency, approve it with a sandbox personal account, and capture it.
2. Read `seller_receivable_breakdown`.
3. Compare `paypal_fee` against the modeled fee for the same transaction.
4. If `exchange_rate` is present, compute the **implied markup** against the reference rate:

```
implied_markup_pct = (reference_rate - applied_rate) / reference_rate * 100
```

(direction must match; guard with a test)

5. Show the result as: "Model said X, sandbox returned Y, difference Z%." Whatever the gap, report it honestly. A transparent gap is more credible than a perfect match.

---

## 7. Tool contracts (what the LLM can call)

| Tool | Input | Output |
|---|---|---|
| `get_fee_schedule` | `region`, `account_type`, `transaction_type` | Applicable rules (id, label, rate, verified) |
| `get_fx_quote` | `base_currency`, `quote_currency`, `amount` | `{ rate, source, expiry_time }` (source is `paypal_quote` or `snapshot`) |
| `calculate_effective_cost` | Transaction fields + `path_id` | `CostBreakdown` |
| `compare_paths` | Transaction | List of `CostBreakdown` (current + valid alternatives), `savings`, and `why_invalid` for rejected paths |
| `create_invoice_draft` | `amount`, `currency`, `description`, `client_email` | `{ invoice_id, status: "DRAFT", link }` |
| `run_calibration` | `amount`, `currency` | `{ modeled_fee, observed_fee, implied_markup_pct, notes }` |

Safety rules:
- `create_invoice_draft` and `run_calibration` run **only after an explicit UI confirmation**. The agent never acts on its own.
- Create **drafts only**. Do not call send unless the user clicks a separate "Send" button (sandbox sends go to a sandbox email).

### Candidate paths for `compare_paths`

| Path ID | Description | Valid only if |
|---|---|---|
| `current` | Invoice in source currency, auto-convert on receipt | always |
| `invoice_in_local` | Invoice in receiver's currency | the currency is supported for the receiving account (encode "currency not allowed" / "unsupported payee currency" cases); payer-side conversion cost is flagged, not hidden |
| `hold_and_batch` | Hold foreign balance, convert later in batches | account can hold that currency (**VERIFY** per region) |

Every rejected path returns a reason. This stops FeeLens from recommending something that doesn't work.

---

## 8. Agent loop (`agent/loop.py`)

```
1. Receive user input (invoice details or natural language)
2. Send to LLM with tools + system prompt
3. If LLM returns tool_calls:
     - execute each via dispatcher
     - append tool results to messages
     - go to 2 (cap at ~6 iterations)
4. If LLM returns final text:
     - return { breakdowns (from tool results), explanation (LLM text) }
```

Rules:
- The frontend renders numbers from **structured tool results**, never from LLM text.
- `call_llm()` is the single place that touches the provider SDK, so you can swap Claude, GPT, or Gemini by editing one file.
- Action tools require confirmation (section 7).

**Toolkit decision (make this in Phase 0):** PayPal's Python `paypal-agent-toolkit` supports OpenAI Agents SDK, LangChain, CrewAI, and MCP, with Orders and Invoices tools. If you use OpenAI or LangChain, it can handle the invoice and order actions. If you use another provider, a thin REST client is simpler. Either way, fee math and FX handling stay in your own engine. A hybrid is allowed but adds moving parts, so avoid it unless it saves time.

### Agent system prompt (outline)

- Role: payments cost analyst explaining fees to a freelancer or small merchant
- Always call `compare_paths` before recommending anything
- Quote only numbers returned by tools, and always mention the data tier
- Output: short comparison first, then plain-language reasoning
- State assumptions; flag anything `modeled` or `snapshot`
- Never frame output as financial advice

---

## 9. API surface (FastAPI)

| Method | Route | Purpose |
|---|---|---|
| `POST` | `/api/audit` | Run agent on a transaction, return breakdowns + explanation |
| `POST` | `/api/invoice` | After confirmation, create the draft invoice in the recommended currency |
| `POST` | `/api/calibrate` | Run sandbox order + capture and return modeled vs observed |
| `GET` | `/api/health` | Sanity check (also used by Render) |
| `GET` | `/` | Serve static frontend |

`POST /api/audit` response shape:

```json
{
  "current":  { "total_fees": "...", "effective_rate_pct": "...", "lines": [ ... ] },
  "optimized": { "path_id": "...", "total_fees": "...", "lines": [ ... ] },
  "savings": "...",
  "rejected_paths": [ { "path_id": "...", "reason": "..." } ],
  "fx_source": "paypal_quote | snapshot",
  "explanation": "...",
  "assumptions": [ "..." ]
}
```

---

## 10. Frontend

Single page, vanilla JS:

1. **Input panel**: amount, invoice currency, receive currency, account region, account type
2. **Audit button** -> `/api/audit`
3. **Comparison view**: "Current path" (red) vs "Optimized path" (green), fee lines with data-tier badges.
4. **Savings banner**: the single number the demo hinges on
5. **Explanation panel**: agent reasoning, assumptions, rejected paths with reasons
6. **"Create invoice draft in EUR" button** -> `/api/invoice` -> shows the invoice link/status
7. **"Validate with sandbox" button** -> `/api/calibrate` -> shows modeled vs observed
8. Footer: "Estimates, not financial advice."
9. **Polish required by the Design criterion** (the rules say "complete product experience, not just a proof of concept"): loading spinners during agent calls, friendly error messages (token failure, FX fallback notice, invalid path), one-click example presets so judges can try it in seconds, an empty state, and readable explanations instead of raw JSON.

---

## 11. Testing

| Test | What it proves |
|---|---|
| `test_calculator.py` | Known inputs produce exact hand-computed totals |
| `test_paths.py` | Invalid paths rejected with reasons; savings never negative-labeled as savings |
| `test_fee_config.py` | Every rule has `id`, `source_url`, `verified`; no duplicate IDs |
| `test_fx_direction.py` | A known pair gives a sensible direction (1 USD should not convert to a tiny or huge amount by mistake) |
| `test_calibrate.py` | Implied-markup math is correct on sample captured breakdowns |
| Manual: 5 corridors | USD->EUR, GBP->USD, USD->PHP, AUD->USD, USD->INR |
| Manual: sandbox | Token, FX quote (or fallback), draft invoice, order + capture all succeed |

---

## 12. Dev tooling that speeds up the build

- **Give your coding AI the real specs.** PayPal's docs index at `https://docs.paypal.ai/llms.txt` lists OpenAPI files, including `checkout_orders_v2.json`, `invoicing_v2.json`, `currency_exchange_v2.json`, and `payments_payment_v2.json` (all under `https://docs.paypal.ai/api-reference/`). Download them into `specs/` and attach them when you build each module. Docs pages also work as `.md` files, which are easier for AI tools to read.
- **APIMatic context plugin** (webinar Oct 7): another way to give your AI assistant accurate PayPal API context. This directly reduces invented endpoints.
- **PayPal MCP server** (`@paypal/mcp`, set `PAYPAL_ENVIRONMENT=SANDBOX`): use it in an MCP client such as Claude Desktop to poke at your sandbox before writing code. It needs an access token, which expires, so regenerate when it stops working.
- **Postman**: build a small collection of your four calls (token, FX quote, draft invoice, create + capture order). It doubles as your debugging tool and as documentation.
- **Official sandbox starter**: `github.com/paypaldev/getting-started-with-paypal-sandbox`.
- **Sandbox accounts**: create one business account (receiver) and one personal account (payer) in the Developer Dashboard. App creation there is dashboard-only (no API).

### Sponsor tools worth using (and the ones to skip)

| Tool | Use in FeeLens | Priority |
|---|---|---|
| **Render** | Host the app so judges can open a live link; the $50 credits help cover hosting through Dec 15; Render Workflows is optional deeper use | High |
| **APIMatic** | Context Plugin inside your AI coding agent for accurate PayPal API calls. Claim the free 1-month Basic subscription and say in the README how you used it | High (near-zero cost) |
| **Postman** | Request collection for the four core calls | Medium |
| **AG Grid** | **Skip.** The prize requires AG Studio (custom widgets, theming, Studio Agent Framework) and React; a plain fee table would not qualify | Skip |
| Bryntum, Channel3, Kernel, Elastic, Zapier, Astropods | No natural fit | Skip |

The sponsor pages and rules (section R) say what each prize requires. Only one sponsor prize can be won, so do not chase several or bolt tools on at the cost of the core product.

---

## 13. Deploy on Render

- Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Environment variables in the Render dashboard (client ID, secret, LLM key). Never commit them.
- Health check path: `/api/health`
- Deploy early (Phase 5), not in the last week.
- **Protect your budget.** The public link can be hit by judges and strangers. Add a per-IP rate limit and a daily cap on LLM calls, and set a spending limit on the LLM key.
- **Stay live through Dec 15.** The rules require free, unrestricted access until judging ends. Keep the Sandbox app credentials valid, watch your Render credit balance, and do not push risky changes during judging.
- Apply the Render credits per Render's docs (credits are deducted from your balance, which is billed monthly).

---

## 14. Build order and timeline

Today is Oct 5. Webinar times are in Nigeria time (WAT).

| Dates | Phase | Deliverable | Done when |
|---|---|---|---|
| Oct 5-7 | **1. Sandbox spike** (webinar Oct 6, 5pm; APIMatic Oct 7, 5pm) | `sandbox_spike.py` runs: token, FX quote (or confirmed 403), draft invoice, order + capture | You know which data tiers are real, and whether the capture returns an `exchange_rate` |
| Oct 8-14 | **2. Fee engine + FX source + tests** | Calculator, `fees.json` with verified rates, FX fallback chain | Tests pass for 5 corridors |
| Oct 15-21 | **3. Agent loop + calibration** | Tool loop using only tool numbers; `run_calibration` | Agent answers correctly; calibration shows modeled vs observed |
| Oct 22-28 | **4. Invoice action + UI** | Draft invoice from the agent; before/after view with badges | End-to-end demo path works locally |
| Oct 29-Nov 4 | **5. Edge cases, deploy, demo video** | Render deploy; wrong-currency and weekend-rate scenarios; 3-min video | Live URL works; video recorded |
| Nov 5-11 | **6. Devpost write-up + submit** | Submission by **Nov 11** (hard deadline Nov 12, 9:00 pm WAT) | Submitted, with Nov 12 as buffer only |
| Nov 12-Dec 15 | **7. Keep it live** | App stays up and free to use | No risky changes during judging |

If the spike shows the FX quote scope is unavailable, you lose nothing structurally: the snapshot fallback is already designed in.

---

## 15. Demo flow (3 minutes)

1. **Hook (0:20):** a freelancer is about to invoice $5,000 to a client; the cost is invisible.
2. **Audit (0:40):** FeeLens breaks the payment into fee events with data-tier badges.
3. **Compare (0:40):** current path vs optimized path, with rejected paths and reasons.
4. **Act (0:40):** one click drafts the invoice in the cheaper currency; show it in the sandbox dashboard.
5. **Proof (0:30):** sandbox capture returns the real fee and rate; show "model vs observed."
6. **Close (0:10):** "Know what you pay before you pay it."

The dollar figures come from your verified config and the real run, **not** from the research notes' illustrative numbers.

Video constraints from the rules: **under 3 minutes**, **public on YouTube**, shows the project functioning, and contains **no third-party trademarks or copyrighted music**. Use no music (or royalty-free music you have rights to), do not show competitor logos, and keep PayPal screens to what is needed. PayPal is the sponsor and required platform, so showing sandbox screens is very likely fine, but ask in Discussions if you want certainty.

---

## 16. Prize alignment

See section R for the five equally weighted judging criteria. Below is how FeeLens answers the honorable-mention categories.

| Prize signal | How FeeLens answers it |
|---|---|
| Best Use of PayPal + AI | Quote API + Invoicing API + Orders/capture, all in one flow, with AI reasoning over real responses |
| Most Impactful | Real money saved for cross-border freelancers; measurable before/after |
| Best Demo Delivery | One clear story with a visual before/after and a proof step |
| Agentic action | Agent drafts the invoice after confirmation instead of only recommending |
| Most Creative | Pre-transaction cost auditing that explains the decision, with honest data-tier labeling |

---

## 17. Risks and mitigations

| Risk | Mitigation |
|---|---|
| FX quote scope unavailable | Snapshot fallback; tag the source; test on day 1 |
| Sandbox doesn't mimic real fees or FX spreads | Say so openly; frame calibration as flow validation; keep `modeled` labels |
| Fee rates wrong or outdated | Single config, `verified` flag, source URLs |
| FX direction inverted | `test_fx_direction.py` before any rate is used |
| Demo reads as "calculator with an LLM wrapper" | Centerpiece is path reasoning, rejected paths, the invoice action, and the proof step |
| LLM misquotes a number | UI renders from structured tool output only |
| Invalid recommendation | Validity rules with tested rejection reasons |
| Scope creep as a solo builder | Cut list and stretch list stay in force |
| Last-week surprises | Deploy by end of Phase 5; submit by Nov 11 |

**Cut list:** OAuth account connection, transaction history dashboard, full mobile design, Wise/Revolut comparison (also avoids third-party trademark issues in the video). The Design criterion still requires a coherent, polished desktop experience (see section 10, item 9).
**Stretch (only if ahead):** List Balances API for `holds_receive_currency`, refund simulation, webhooks for invoice-paid events, savings tracker.

---

## 18. VERIFY checklist (before trusting any number)

- [ ] Current PayPal fee rates for your account type and region
- [ ] Cross-border fee rate and which corridors it applies to
- [ ] Currency conversion markup (a range or rule, not one fixed number)
- [ ] Whether the sandbox app can call the FX quote API
- [ ] FX quote rate direction (unit test)
- [ ] Whether sandbox captures return a realistic `paypal_fee` and `exchange_rate`
- [ ] Create-order and create-invoice request body required fields
- [ ] Whether balances can be held in each target currency for your region
- [ ] Whether PayPal's own screens count as "third-party trademarks" in the video (ask in Discussions)
- [ ] Rules re-read before submission (they can be amended)

---

## 19. Final submission checklist (from the official rules)

- [ ] Repo is **public**, has an **open source LICENSE file** (for example MIT or Apache-2.0) that shows in the About section
- [ ] README has: what FeeLens is, the list of PayPal endpoints used, how AI is used, setup and run steps, `.env.example`, test/sandbox details for judges, and how APIMatic and Render were used
- [ ] Hosted demo URL works with no setup, has rate limits and a spending cap, and stays live through Dec 15
- [ ] Demo video is under 3 minutes, public on YouTube, shows the full flow working, with no third-party trademarks and no copyrighted music
- [ ] Devpost text description covers features, the problem and audience, and any limits (what is `modeled` vs `sandbox-observed`)
- [ ] All materials are in English
- [ ] No secrets in the repo history (rotate any key that was ever committed)
- [ ] Submitted by **Nov 11**; hard deadline **Nov 12, 12:00 pm PT = 9:00 pm WAT**

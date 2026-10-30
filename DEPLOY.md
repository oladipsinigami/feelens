# Deploying FeeLens

One free Render web service. No database, no queue, no cron. The fee engine is
stateless and the exchange-rate snapshot is a file in the repo, so the only
thing that has to stay alive is the process.

## What the service needs

| Environment variable | Required | Why |
| --- | --- | --- |
| `PYTHON_VERSION` | yes | Render builds 3.8–3.13; this repo is developed on 3.14, so the version is pinned rather than inherited. |
| `OPENROUTER_API_KEY` | no | Without it every explanation is the deterministic template. The page is fully functional either way; `/api/health` says which mode is live. |
| `LLM_MODEL` | no | Primary model. Defaults to the free Nemotron build. |
| `LLM_FALLBACK_MODEL` | no | Tried when the primary is rate-limited, which on a free tier is normal. |
| `PAYPAL_CLIENT_ID` | no | Sandbox app client id. Without it the "Create invoice in PayPal" button is hidden and `/api/health` reports `paypal_configured: false`. The quote is unaffected. |
| `PAYPAL_CLIENT_SECRET` | no | Sandbox app secret, paired with the id above. |
| `PAYPAL_BASE_URL` | no | Defaults to `https://api-m.sandbox.paypal.com`. The client refuses any non-sandbox host unless `allow_live=True` is passed in code. |
| `PAYPAL_DEMO_RECIPIENT` | no | The sandbox buyer address an invoice is drafted to. Defaults to `sb-buyer@business.example.com`. |
| `PAYPAL_INVOICES_PER_WINDOW` | no | Invoice creations per visitor per window. Defaults to 3 — enough to demo, not enough to fill a shared sandbox. |

The PayPal credentials are **sandbox** credentials, and they are the only secret
the web app needs in order to demonstrate a real API call. They are safe to host:
a sandbox app cannot move real money, and the client will not talk to the live
host even if the base URL is changed. Without them the app still works — the
button is hidden, not broken — but the project then has no PayPal API call in it,
which is the thing the hackathon is scoring.

Both model names are checked by `python scripts/check_models.py`, which needs no
key: OpenRouter publishes its model list publicly. It verifies that each name
exists, is free, and advertises tool support. Run it before changing a model
name — a wrong fallback is invisible until the primary is rate-limited, and then
it silently turns every explanation into the template.

## Steps

1. Push this repository to GitHub. `render.yaml` is already at the root, so
   Render reads the build command, start command and health check from it.
2. In Render: **New → Blueprint**, pick the repo, apply. It creates one free web
   service named `feelens`.
3. Render prompts for `OPENROUTER_API_KEY` because the blueprint declares it
   with `sync: false`. Leave it blank to ship the template-only build, or paste
   the key to enable the model. It is never read from the YAML file.
4. Wait for the first build. The health check is `GET /api/health`; Render will
   not route traffic until it returns 200.

## What was verified before pushing

The pinned set was installed into a clean CPython 3.13.14 environment, which is
what Render builds, and the resolver moved the transitive dependencies off the
versions this repo is developed against (pydantic 2.12.5 → 2.14.1, starlette
1.3.1 → 1.10.0). On that combination: **189 tests pass**, the app boots on
`0.0.0.0` with no model key, all fifteen corridor × product quotes return 200,
and `/api/explain` falls back to the template with a clean guardrail verdict.

That is the failure mode this check exists to catch — a pin on the direct
dependencies does not pin their dependencies, so "it worked on my machine" is
not evidence about the build machine.

## What the free model costs you

The key is free-tier: **50 model requests per day, 20 per minute**, and one
explanation is two or three requests — the tool round trip, then the answer,
plus one if the guardrail asks for a repair.

Measured on the demo quote, the first explanation takes **20–60 seconds**
depending on how loaded the provider is. A repeated quote is served from an
in-process cache and takes **0.0 seconds**, because the same invoice asked twice
is the same explanation and should not cost a second request. Only validated
model answers are cached; a template is cheap to rebuild and is never stored, so
a later request can still reach the model once it recovers.

Practical consequence: explain the three quotes you intend to show **before** you
share the link. The cache makes every later view of those quotes instant, and a
judge is unlikely to click through all fifteen corridor × product combinations.

The cache is in process memory, so it dies with the instance. On the free plan
the instance sleeps after 15 minutes of inactivity, which means a warmed quote
is warm only while something is keeping the app awake. `.github/workflows/keepalive.yml`
pings `/api/health` every 5 minutes between 06:00 and 23:55 UTC for exactly that
reason — see [Keeping it awake](#keeping-it-awake). Outside that window the
first click after a sleep costs 20–60 seconds, and the page is honest about it:
the chip reads `ai` only once the model has actually answered.

When the quota runs out, nothing breaks. `/api/explain` returns the template and
the note says `model unavailable: …`, which is the same thing it says when no key
is configured at all.

Two free models are configured, primary then fallback, and both are verified as
free and tool-capable by `scripts/check_models.py`. The fallback helps with a
per-model rate limit or an upstream outage; it does **not** help with the 50/day
account limit, because that limit is shared.

### The limits that keep the budget for a judge

A public link with a 50/day budget and no limit is a link where the first visitor
can spend the day. So `/api/explain` is capped twice:

| variable | default | meaning |
| --- | --- | --- |
| `MODEL_EXPLAINS_PER_WINDOW` | `6` | explanations per visitor per window |
| `MODEL_WINDOW_MINUTES` | `10` | length of the sliding window |
| `MODEL_DAILY_REQUEST_CAP` | `44` | upstream requests per UTC day, all visitors |

The per-visitor key is the **last** entry of `X-Forwarded-For`, because Render
appends the real peer to whatever the client sent — the first entry is
client-controlled and reading it would let anyone mint a new bucket per request.

Neither limit returns an error. The quote is priced and the template explanation
is served, with a note naming the limit and when it resets, and `/api/config`
publishes both limits so the behaviour is inspectable rather than mysterious.
`/api/health` reports the remaining allowance.

The daily count is per process, so a restart starts it from zero. That is stated
rather than hidden: OpenRouter's own 50/day is the ceiling that actually holds,
and the 44 exists to keep this deployment below it. Setting
`MODEL_DAILY_REQUEST_CAP=0` switches the model off and says so.

## Verifying the deployment

`/api/health` is the honest answer to "is this demo trustworthy right now".
A sample response, as of this writing:

```json
{
  "status": "ok",
  "version": "0.2.0",
  "fee_retrieved_date": "2026-10-26",
  "fx_snapshot_date": "2026-10-27",
  "fx_age_days": 0,
  "fx_stale": false,
  "model_configured": false,
  "tier": "modeled"
}
```

`fx_stale: true` means the exchange-rate snapshot has crossed the four-day
threshold and the converted figures are drifting from reality. `model_configured:
false` means every explanation on the page is the template — which is fine, and
which the page says out loud rather than implying otherwise.

## Two things that will bite on a free tier

**Cold starts.** Free instances sleep after 15 minutes of inactivity and take
roughly 30–60 seconds to wake. A judge clicking the Devpost link may see a
browser timeout rather than the page. Either open the URL yourself a minute
before demoing, or rely on the scheduled ping described below. Ping the health
endpoint, not `/` — it is cheap and it is the same signal Render uses.

**`/api/explain` costs a model call.** It is rate-limited twice: a visitor gets
six explanations per ten minutes, and the process as a whole gets a daily
upstream cap. Both refuse by returning the template plus a note rather than an
error, because nothing was rejected — one thing was withheld. Cached
explanations are served regardless of either limit. The limits exist because a
free model tier is 50 requests a day shared by everyone who opens the link.

## Keeping it awake

`.github/workflows/keepalive.yml` pings `/api/health` every 5 minutes between
06:00 and 23:55 UTC. No third-party account, no cron service to maintain. To
check it by hand:

```bash
gh workflow run keepalive
gh run watch
```

It is deliberately **not** 24/7. Render's free plan allows 750 instance hours a
month and a month is about 730 hours, so keeping one service awake around the
clock would spend nearly all of it and starve the other free services on the
account. 06:00–23:55 UTC is roughly 547 hours a month and covers US and European
working hours, when judging is most likely.

Two limits, stated rather than hidden:

- GitHub delays scheduled runs under load, sometimes by 10–15 minutes. A run
  that slips past 15 minutes lets the service sleep anyway, so this reduces cold
  starts; it does not eliminate them.
- GitHub disables scheduled workflows on a public repo after 60 days with no
  activity. The judging period ends 2026-12-15, well inside that.

The job exits 0 even when the ping fails and raises a `::warning::` instead. A
red X on a public repo reads as "their tests fail", and this is not a test.

## Refreshing the rates before a demo

The snapshot is dated and the app is designed to admit it. To refresh:

```bash
python scripts/refresh_fx.py --dry-run   # show every rate movement, write nothing
python scripts/refresh_fx.py             # write the snapshot
```

then update `tests/fixtures/fx_snapshot.json` **separately and deliberately**,
recomputing the expected numbers. The pricing tests read the frozen fixture on
purpose: if they read the live file, every legitimate refresh would break the
suite and the tempting repair would be to edit the expected values until it
passed. (One test opens the shipped snapshot to confirm it parses; it asserts no
figures.)

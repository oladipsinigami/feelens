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

The PayPal credentials are **not** on the server. They are used only by
`scripts/check_sandbox.py`, which runs by hand; the web app never calls the
PayPal API. Adding them to the host would be a secret with nothing consuming it.

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
is warm only while something is keeping the app awake — point a free uptime
monitor (cron-job.org, UptimeRobot) at `/api/health` every 10 minutes and both
the cold start and the cold cache stop being a problem. Without one, the first
click after a sleep costs 20–60 seconds again, and the page is honest about it:
the chip reads `ai` only once the model has actually answered.

When the quota runs out, nothing breaks. `/api/explain` returns the template and
the note says `model unavailable: …`, which is the same thing it says when no key
is configured at all.

Two free models are configured, primary then fallback, and both are verified as
free and tool-capable by `scripts/check_models.py`. The fallback helps with a
per-model rate limit or an upstream outage; it does **not** help with the 50/day
account limit, because that limit is shared.

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
before demoing, or point a free uptime monitor (UptimeRobot, cron-job.org) at
`/api/health` every 10 minutes. Ping the health endpoint, not `/` — it is cheap
and it is the same signal Render uses.

**`/api/explain` costs a model call.** There is no authentication and no rate
limit, because there is nothing to protect but a free-tier quota: an abusive
caller burns rate limit, not money, and every failure degrades to the template.
If the app is ever pointed at a paid model, that assumption stops holding and
the endpoint needs a limit before the key is set.

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

# `specs/` — PayPal OpenAPI specifications (build-time input)

Vendored, unmodified copies of PayPal's official OpenAPI 3.0 specifications.

**FeeLens does not call these APIs at runtime.** The web application makes no
PayPal API calls at all: there are no PayPal credentials in the deployed
environment, and every figure the app shows is either `modeled` by the
deterministic fee engine, a `PayPal quote` the user supplies, or
`sandbox-observed`. This directory is build-time input only — reference material
for getting field names, response shapes and sandbox hostnames right while
building, not a dependency of the running service.

Keeping that distinction explicit matters. A generated client that nothing
imports would be dead code, and dead code included to satisfy a prize category
is the kind of thing this project is specifically trying not to do.

## Provenance

| File | Upstream spec | Version | Bytes | SHA-256 |
| --- | --- | --- | --- | --- |
| `checkout_orders_v2.json` | Orders | 2.32 | 971,284 | `14db0b9e…77fd6` |
| `invoicing_v2.json` | Invoices | 2.6 | 276,617 | `b38ec179…19f52` |
| `reporting_transactions_v1.json` | Transaction Search | 1.9 | 75,463 | `40f2af0c…52b4` |

- **Source:** <https://github.com/paypal/paypal-rest-api-specifications>, branch `main`, path `openapi/`
- **Retrieved:** 2026-10-29
- **Method:** `curl -sSfL https://raw.githubusercontent.com/paypal/paypal-rest-api-specifications/main/openapi/<file>`
- **Integrity:** full digests, byte counts and per-file source URLs are in [`MANIFEST.json`](./MANIFEST.json). Verify with `sha256sum *.json`.

The files are byte-identical to upstream. Nothing here has been edited by hand.

## What we actually confirmed from them

Three things, each of which changed or validated something in the app:

1. **`transaction_info.fee_amount` is the authoritative per-transaction fee.**
   The spec describes it as *"The PayPal fee amount. All transaction fees are
   included in this amount, which is the record of fee associated with the
   transaction."* This is the field a post-hackathon calibration would read: a
   merchant exports their Transaction Search CSV and compares the real
   `fee_amount` against what FeeLens modelled. It is also the honest boundary —
   it requires the merchant's own authenticated account, which is exactly why
   FeeLens ships with `modeled` numbers rather than pretending to know a
   merchant's effective rate.

2. **The Orders v2 capture response breaks the fee out explicitly.**
   `seller_receivable_breakdown` carries `gross_amount`, `paypal_fee`,
   `net_amount`, plus `paypal_fee_in_receivable_currency` and `exchange_rate`
   for cross-currency captures. That is the response shape behind the
   `sandbox-observed` tier: when a capture is observed in the sandbox, the fee
   and the FX rate are reported by PayPal rather than inferred by us.
   *Note:* the published `x-samples` examples in the spec are illustrative
   (`100.00 USD → paypal_fee 3.00`) and are deliberately **not** used as data
   anywhere in the app.

3. **The sandbox base URL is `https://api-m.sandbox.paypal.com`.**
   Confirmed directly from the `servers` block of both the Invoices and
   Transaction Search specs, rather than from documentation prose.

What the specs do **not** contain: pricing. There are no rate or percentage
fields describing what PayPal charges — `invoicing_v2.json` has no fee-related
properties at all. This is the root of the project's central claim: the fee
schedule is not discoverable from the API, so it has to live in an inspectable
config file with sources (`app/data/fees.json`) instead of being asserted by a
model.

## Build-time tooling

APIMatic Context Plugins were used during development to give the coding agent
the PayPal API surface above, so that field names and response shapes came from
the spec rather than from memory.

> **Status: pending.** This section is filled in from the artifact the plugin
> produces — not from intent. Until an artifact is committed alongside this
> file, **no APIMatic claim appears in the top-level `README.md` or the Devpost
> submission.** The claim and the evidence ship together or not at all.

## Licensing

The specifications are PayPal's, licensed **Apache-2.0**. The upstream licence
text is included unmodified as
[`LICENSE.paypal-rest-api-specifications`](./LICENSE.paypal-rest-api-specifications).
FeeLens itself is MIT; this directory is the only Apache-2.0 content in the
repository.

## Refreshing

```bash
cd specs
for f in checkout_orders_v2.json invoicing_v2.json reporting_transactions_v1.json; do
  curl -sSfL -o "$f" "https://raw.githubusercontent.com/paypal/paypal-rest-api-specifications/main/openapi/$f"
done
sha256sum *.json   # update MANIFEST.json
```

Re-check the `info.version` of each file afterwards — a spec bump can rename
fields, and the point of pinning versions here is to make that visible.

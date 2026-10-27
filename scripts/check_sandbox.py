"""Three-step PayPal sandbox spike.

Purpose: turn "is the sandbox working?" into one command with evidence, instead
of a guess. Run the steps in order.

    python scripts/check_sandbox.py token
    python scripts/check_sandbox.py create
    # open the printed approve URL, log in as the sandbox BUYER, approve
    python scripts/check_sandbox.py capture <order_id>

`capture` prints PayPal's own `seller_receivable_breakdown`. That number is the
only figure in FeeLens that is not modelled, so it is the one worth capturing
verbatim and pasting into the README.

Docs used:
- OAuth token:  https://developer.paypal.com/api/rest/authentication/
- Create order: https://developer.paypal.com/docs/api/orders/v2/#orders_create
- Capture order:https://developer.paypal.com/docs/api/orders/v2/#orders_capture
"""

import argparse
import os
import sys
import uuid

import httpx
from dotenv import load_dotenv

load_dotenv()
BASE_URL = os.getenv("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com").rstrip("/")


def fail(message):
    print(f"\nFAILED: {message}\n", file=sys.stderr)
    raise SystemExit(1)


def credentials():
    load_dotenv()
    client_id = os.getenv("PAYPAL_CLIENT_ID", "").strip()
    secret = os.getenv("PAYPAL_CLIENT_SECRET", "").strip()

    missing = [
        name
        for name, value in (
            ("PAYPAL_CLIENT_ID", client_id),
            ("PAYPAL_CLIENT_SECRET", secret),
        )
        if not value
    ]
    if missing:
        fail(
            f"{' and '.join(missing)} not set in .env.\n"
            "  Both are required: the Client ID identifies the app, the Secret\n"
            "  proves you own it. developer.paypal.com > Apps & Credentials >\n"
            "  Sandbox > FeeLens > Show (next to Secret).\n"
            "  Do not paste the Secret into chat; put it straight into .env."
        )
    if "sandbox" not in BASE_URL:
        fail(
            f"refusing to run: PAYPAL_BASE_URL is {BASE_URL}, which is not a "
            "sandbox host. This spike must never touch a live account."
        )
    return client_id, secret


def explain(response):
    """PayPal errors carry name/message/details. Show them, not a bare status."""
    try:
        body = response.json()
    except ValueError:
        return response.text[:500]
    parts = [body.get("error"), body.get("name"), body.get("message")]
    for detail in body.get("details", []) or []:
        parts.append(f"  - {detail.get('issue')}: {detail.get('description')}")
    return "\n".join(part for part in parts if part)


def get_token(client, client_id, secret):
    response = client.post(
        "/v1/oauth2/token",
        data={"grant_type": "client_credentials"},
        auth=(client_id, secret),
    )
    if response.status_code != 200:
        fail(f"token request returned {response.status_code}\n{explain(response)}")
    return response.json()


def cmd_token(client, _args):
    client_id, secret = credentials()
    body = get_token(client, client_id, secret)
    print("AUTH OK")
    print(f"  app_id:     {body.get('app_id')}")
    print(f"  expires_in: {body.get('expires_in')}s")
    print(f"  scope:      {str(body.get('scope'))[:160]}...")


def cmd_create(client, args):
    client_id, secret = credentials()
    token = get_token(client, client_id, secret)["access_token"]

    payload = {
        "intent": "CAPTURE",
        "purchase_units": [
            {
                "reference_id": "feelens-spike",
                "description": "FeeLens sandbox calibration",
                "amount": {"currency_code": args.currency, "value": args.amount},
            }
        ],
        "application_context": {
            "brand_name": "FeeLens",
            "user_action": "PAY_NOW",
            "shipping_preference": "NO_SHIPPING",
        },
    }
    response = client.post(
        "/v2/checkout/orders",
        json=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "PayPal-Request-Id": str(uuid.uuid4()),
        },
    )
    if response.status_code not in (200, 201):
        fail(f"create order returned {response.status_code}\n{explain(response)}")

    order = response.json()
    links = order.get("links", [])
    # Docs are inconsistent about rel ("payer-action" vs "approve"), so match the
    # href rather than trusting rel.
    approve = next(
        (link["href"] for link in links if "checkoutnow" in link.get("href", "")),
        None,
    )
    print("ORDER CREATED")
    print(f"  id:     {order.get('id')}")
    print(f"  status: {order.get('status')}")
    if approve:
        print(f"\nOpen this and log in as the sandbox BUYER:\n  {approve}\n")
        print(f"Then run:\n  python scripts/check_sandbox.py capture {order.get('id')}")
    else:
        print("  no checkoutnow link returned; links were:")
        for link in links:
            print(f"    rel={link.get('rel')} href={link.get('href')}")


def cmd_capture(client, args):
    client_id, secret = credentials()
    token = get_token(client, client_id, secret)["access_token"]

    response = client.post(
        f"/v2/checkout/orders/{args.order_id}/capture",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "PayPal-Request-Id": str(uuid.uuid4()),
        },
    )
    if response.status_code not in (200, 201):
        fail(
            f"capture returned {response.status_code}\n{explain(response)}\n"
            "  (UNPROCESSABLE_ENTITY usually means the buyer has not approved yet)"
        )

    order = response.json()
    print(f"CAPTURED  status={order.get('status')}")
    for unit in order.get("purchase_units", []):
        capture = (unit.get("payments", {}).get("captures") or [{}])[0]
        breakdown = capture.get("seller_receivable_breakdown", {})
        print(f"\n  capture id: {capture.get('id')}")
        for key in ("gross_amount", "paypal_fee", "net_amount", "receivable_amount", "exchange_rate"):
            value = breakdown.get(key)
            if value:
                print(f"  {key:18} {value.get('value', value)} {value.get('currency_code', '')}")
        if "receivable_amount" not in breakdown:
            print("  (no receivable_amount: PayPal only returns it when currencies differ)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("token", help="prove the credentials authenticate").set_defaults(func=cmd_token)

    create = sub.add_parser("create", help="create an order and print the approve URL")
    create.add_argument("--amount", default="5000.00")
    create.add_argument("--currency", default="EUR")
    create.set_defaults(func=cmd_create)

    capture = sub.add_parser("capture", help="capture an approved order")
    capture.add_argument("order_id")
    capture.set_defaults(func=cmd_capture)

    args = parser.parse_args()
    with httpx.Client(base_url=BASE_URL, timeout=30) as client:
        args.func(client, args)


if __name__ == "__main__":
    main()

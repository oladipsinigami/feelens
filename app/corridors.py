"""The corridors FeeLens prices, and the payment products it knows.

One list each, in one place. The UI builds its inputs from this module, so a
corridor cannot appear in the interface without a rate behind it, and cannot
quietly disappear from the interface while the API still quotes it.

Five corridors, all into USD: the receiver is a US business account holding USD.
A corridor is a (client country, client currency) pair, not just a currency —
the 1.50% international surcharge keys off the country, and the invoice may be
issued in a different currency from the one the client holds. Those are two
separate inputs: `client_currency` is what the client's balance is likely in,
`invoice_currency` on the request is what the invoice is denominated in.
"""

RECEIVE_CURRENCY = "USD"
RECEIVE_REGION = "US"
ACCOUNT_TYPE = "business"

CORRIDORS = (
    {
        "client_currency": "EUR",
        "client_country": "DE",
        "country": "Germany",
        "currency_name": "Euro",
    },
    {
        "client_currency": "GBP",
        "client_country": "GB",
        "country": "United Kingdom",
        "currency_name": "Pound sterling",
    },
    {
        "client_currency": "PHP",
        "client_country": "PH",
        "country": "Philippines",
        "currency_name": "Philippine peso",
    },
    {
        "client_currency": "AUD",
        "client_country": "AU",
        "country": "Australia",
        "currency_name": "Australian dollar",
    },
    {
        "client_currency": "INR",
        "client_country": "IN",
        "country": "India",
        "currency_name": "Indian rupee",
    },
)

# What an invoice can be denominated in. USD comes first because it is the
# currency the receiving account holds, and issuing in it removes the receiver's
# conversion entirely; the rest are the currencies the corridors already price.
USD_CURRENCY_NAME = "US dollar"


def invoice_currencies() -> list:
    """Every currency an invoice can be issued in, in display order.

    Derived from the corridor list rather than written out again, so a corridor
    cannot be added without its currency becoming selectable, and a currency
    cannot be selectable without a corridor that prices it.
    """
    options = [{"code": RECEIVE_CURRENCY, "name": USD_CURRENCY_NAME}]
    seen = {RECEIVE_CURRENCY}
    for item in CORRIDORS:
        code = item["client_currency"]
        if code in seen:
            continue
        seen.add(code)
        options.append({"code": code, "name": item["currency_name"]})
    return options

# Three products, because "invoice" is not one price. PayPal's invoicing table
# splits by how the client funds the payment, and the card row costs the same
# percentage as a direct send plus a fixed fee.
#
# `spoken` exists because a UI label is not a sentence. Lowercasing "Invoice -
# paid from PayPal" produces "paid as invoice - paid from paypal", which is the
# kind of detail that tells a reader the text was assembled rather than written.
PAYMENT_OPTIONS = (
    {
        "id": "direct",
        "label": "Direct send",
        "group": "Client sends money",
        "description": "They send it to your PayPal address, the way you would send money to a person.",
        "spoken": "a direct send to your PayPal address",
        "transaction_type": "goods_services",
        "payment_method": "goods_services_transfer",
        "caveat": (
            "This is PayPal's send-money product, not its invoicing or checkout "
            "product. Check that the payment qualifies for Seller Protection "
            "before you use it for business income; a payment that does not "
            "may leave you with little recourse if the client disputes it."
        ),
    },
    {
        "id": "invoice_paypal",
        "label": "Invoice - paid from PayPal",
        "group": "You send an invoice",
        "description": "They open your PayPal invoice and pay with their PayPal balance or bank.",
        "spoken": "a PayPal invoice settled from a PayPal account",
        "transaction_type": "invoice",
        "payment_method": "paypal_checkout",
    },
    {
        "id": "invoice_card",
        "label": "Invoice - paid by card",
        "group": "You send an invoice",
        "description": "They open your PayPal invoice and pay with a card, Apple Pay or another wallet.",
        "spoken": "a PayPal invoice settled by card or wallet",
        "transaction_type": "invoice",
        "payment_method": "card",
    },
)

DISCLAIMER = (
    "Estimates calculated from PayPal's published US fee schedule for a US "
    "business account. This is not a PayPal quote and not financial advice."
)


def corridor(client_currency: str) -> dict:
    for item in CORRIDORS:
        if item["client_currency"] == client_currency:
            return item
    raise KeyError(client_currency)


def payment_option(option_id: str) -> dict:
    for item in PAYMENT_OPTIONS:
        if item["id"] == option_id:
            return item
    raise KeyError(option_id)

"""The corridors FeeLens prices, and the payment products it knows.

One list each, in one place. The UI builds its inputs from this module, so a
corridor cannot appear in the interface without a rate behind it, and cannot
quietly disappear from the interface while the API still quotes it.

Five corridors, all into USD: the receiver is a US business account holding USD.
A corridor is a (client country, invoice currency) pair, not just a currency —
the 1.50% international surcharge keys off the country, and the two can disagree
in ways that change the total.
"""

RECEIVE_CURRENCY = "USD"
RECEIVE_REGION = "US"
ACCOUNT_TYPE = "business"

CORRIDORS = (
    {
        "invoice_currency": "EUR",
        "client_country": "DE",
        "country": "Germany",
        "currency_name": "Euro",
    },
    {
        "invoice_currency": "GBP",
        "client_country": "GB",
        "country": "United Kingdom",
        "currency_name": "Pound sterling",
    },
    {
        "invoice_currency": "PHP",
        "client_country": "PH",
        "country": "Philippines",
        "currency_name": "Philippine peso",
    },
    {
        "invoice_currency": "AUD",
        "client_country": "AU",
        "country": "Australia",
        "currency_name": "Australian dollar",
    },
    {
        "invoice_currency": "INR",
        "client_country": "IN",
        "country": "India",
        "currency_name": "Indian rupee",
    },
)

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


def corridor(invoice_currency: str) -> dict:
    for item in CORRIDORS:
        if item["invoice_currency"] == invoice_currency:
            return item
    raise KeyError(invoice_currency)


def payment_option(option_id: str) -> dict:
    for item in PAYMENT_OPTIONS:
        if item["id"] == option_id:
            return item
    raise KeyError(option_id)

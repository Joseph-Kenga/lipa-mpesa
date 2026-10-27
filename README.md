# lipa-mpesa

A small Python client for Safaricom's **M-Pesa Daraja API**, with Django-friendly helpers.

- **STK Push** (Lipa Na M-Pesa Online) for Paybill and Buy Goods, plus status query
- **C2B**: register confirmation/validation URLs, sandbox simulation
- **B2C** payouts, **transaction status**, **account balance**, **reversal**
- Parsers for every callback M-Pesa sends you
- Phone and amount normalisation (`0712 345 678` → `254712345678`)
- OAuth token cached and refreshed automatically; thread-safe
- Sandbox or production with one setting

## Install

```bash
pip install lipa-mpesa
pip install "lipa-mpesa[crypto]"     # adds the B2C/status/balance/reversal credential helper
```

Or from a Git repo: `pip install git+https://github.com/Joseph-Kenga/lipa-mpesa.git`

## Quick start

```python
from lipa_mpesa import MpesaClient, MpesaConfig, SANDBOX_SHORTCODE, SANDBOX_PASSKEY

client = MpesaClient(MpesaConfig(
    consumer_key="...",
    consumer_secret="...",
    environment="sandbox",
    shortcode=SANDBOX_SHORTCODE,
    passkey=SANDBOX_PASSKEY,
    callback_url="https://yourdomain.co.ke/payments/stk/callback/",
))

res = client.stk_push(phone="0712345678", amount=1500, account_reference="HSE-A12")
print(res.checkout_request_id)   # save this to match the callback

status = client.stk_query(res.checkout_request_id)
if status.is_pending:
    ...                          # customer hasn't entered PIN yet
elif status.is_successful:
    ...
```

`stk_push` only confirms that the prompt reached the phone. The payment result arrives at
your callback URL (or via `stk_query`).

## Configuration

Three ways to build a client:

```python
MpesaClient(MpesaConfig(...))     # explicit
MpesaClient.from_env()            # MPESA_CONSUMER_KEY, MPESA_CONSUMER_SECRET, MPESA_SHORTCODE, ...
MpesaClient.from_django()         # reads settings.MPESA = {...}
```

| Setting | Used by | Notes |
|---|---|---|
| `consumer_key`, `consumer_secret` | everything | From your Daraja app |
| `environment` | everything | `"sandbox"` (default) or `"production"` |
| `shortcode` | STK, C2B, B2C… | Paybill number. For Buy Goods: the *store* number |
| `passkey` | STK | Lipa Na M-Pesa Online passkey |
| `till_number` | STK | Set it to switch STK Push to Buy Goods |
| `callback_url` | STK | Default HTTPS callback |
| `initiator_name`, `security_credential` | B2C, status, balance, reversal | See below |
| `result_url`, `timeout_url` | B2C, status, balance, reversal | Async result URLs |
| `timeout` | everything | HTTP timeout in seconds (default 30) |

Django example:

```python
# settings.py
MPESA = {
    "CONSUMER_KEY": os.environ["MPESA_CONSUMER_KEY"],
    "CONSUMER_SECRET": os.environ["MPESA_CONSUMER_SECRET"],
    "ENVIRONMENT": "sandbox",
    "SHORTCODE": "174379",
    "PASSKEY": os.environ["MPESA_PASSKEY"],
    "CALLBACK_URL": "https://yourdomain.co.ke/payments/stk/callback/",
}
```

Keep keys and passkeys in environment variables, never in Git.

## Handling callbacks

```python
from lipa_mpesa import parse_stk_callback, parse_c2b, parse_result, callback_ack, c2b_accept

cb = parse_stk_callback(request.body)   # bytes, str or dict
cb.is_successful, cb.receipt_number, cb.amount, cb.phone_number, cb.transaction_date

tx = parse_c2b(request.body)            # Paybill / Till confirmation
tx.transaction_id, tx.amount, tx.bill_ref_number, tx.full_name

r = parse_result(request.body)          # B2C / status / balance / reversal
r.is_successful, r.transaction_id, r.parameters["TransactionAmount"]
```

Always return HTTP 200 (`callback_ack()` / `c2b_accept()`), even for records you ignore,
otherwise Safaricom keeps retrying. Callback views must be `@csrf_exempt`.

A complete Django setup (model, views, URLs) is in [`examples/django/`](examples/django/).

## C2B (customers paying your Paybill directly)

```python
client.c2b_register_urls(
    confirmation_url="https://yourdomain.co.ke/payments/c2b/confirm/",
    validation_url="https://yourdomain.co.ke/payments/c2b/validate/",
)
client.c2b_simulate(phone="254708374149", amount=100, bill_ref_number="HSE-A12")  # sandbox only
```

Safaricom rejects URLs containing "mpesa" or "safaricom", so the library checks for that.

## B2C, status, balance, reversal

These need an API initiator and an encrypted **security credential**:

```python
from lipa_mpesa import generate_security_credential

credential = generate_security_credential("InitiatorPassword", "ProductionCertificate.cer")

client.b2c_payment(phone="0712345678", amount=5000, remarks="Deposit refund",
                   initiator_name="apiop", security_credential=credential,
                   result_url="https://yourdomain.co.ke/payments/result/",
                   timeout_url="https://yourdomain.co.ke/payments/timeout/")
client.transaction_status("SJQ7ABCDEF")
client.account_balance()
client.reversal("SJQ7ABCDEF", amount=100)
```

Download the sandbox or production certificate from the Daraja portal. These calls return an
acknowledgement only; the outcome is POSTed to `result_url`.

## Errors

All errors inherit from `MpesaError`:

| Exception | When |
|---|---|
| `MpesaConfigError` | Missing or invalid setting |
| `MpesaValidationError` | Bad phone, amount, URL or callback body (nothing was sent) |
| `MpesaAuthError` | Consumer key/secret rejected |
| `MpesaAPIError` | Daraja returned an error (`.error_code`, `.error_message`, `.response`) |
| `MpesaConnectionError` | Network failure or timeout |

## STK result codes you'll see

| Code | Meaning |
|---|---|
| 0 | Paid |
| 1 | Insufficient balance |
| 1032 | Cancelled by the customer |
| 1037 | Phone unreachable or no response |
| 2001 | Wrong PIN |

## Going live

1. Apply for "Go Live" on the Daraja portal and get production keys and passkey.
2. Set `ENVIRONMENT` to `"production"` and update shortcode, passkey and keys.
3. Make sure callback URLs are public HTTPS with a valid certificate.
4. Re-register C2B URLs on the production shortcode.

If Safaricom moves an endpoint to a newer version, override its path:
`client.paths["b2c"] = "/mpesa/b2c/v3/paymentrequest"`.

## Development

```bash
pip install -e ".[dev]"
pytest
```

Tests use a fake HTTP session, so they never call Safaricom.

## License

MIT

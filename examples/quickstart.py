"""Send a real sandbox STK Push.

1. Create an app at https://developer.safaricom.co.ke and copy its Consumer Key/Secret.
2. Set a public HTTPS callback URL (e.g. from https://webhook.site for a quick look).
3. Run:

    MPESA_CONSUMER_KEY=... MPESA_CONSUMER_SECRET=... \
    MPESA_CALLBACK_URL=https://webhook.site/your-id \
        python examples/quickstart.py 0712345678
"""

import os
import sys
import time

from lipa_mpesa import SANDBOX_PASSKEY, SANDBOX_SHORTCODE, MpesaClient, MpesaConfig

config = MpesaConfig(
    consumer_key=os.environ["MPESA_CONSUMER_KEY"],
    consumer_secret=os.environ["MPESA_CONSUMER_SECRET"],
    environment="sandbox",
    shortcode=SANDBOX_SHORTCODE,
    passkey=SANDBOX_PASSKEY,
    callback_url=os.environ["MPESA_CALLBACK_URL"],
)
client = MpesaClient(config)

phone = sys.argv[1] if len(sys.argv) > 1 else "0708374149"
res = client.stk_push(phone=phone, amount=1, account_reference="TEST001", description="Test")
print("Sent:", res.customer_message, res.checkout_request_id)

for _ in range(12):  # poll for up to ~1 minute
    time.sleep(5)
    status = client.stk_query(res.checkout_request_id)
    if not status.is_pending:
        print("Result:", status.result_code, status.result_desc)
        break
    print("Waiting for the customer…")

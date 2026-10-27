"""One-off script: register your Paybill confirmation/validation URLs with Safaricom.

Run once per shortcode (and again whenever the URLs change):

    MPESA_CONSUMER_KEY=... MPESA_CONSUMER_SECRET=... MPESA_SHORTCODE=600000 \
        python examples/register_c2b_urls.py https://yourdomain.co.ke
"""

import sys

from lipa_mpesa import MpesaClient

base = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "https://yourdomain.co.ke"
client = MpesaClient.from_env()
print(client.c2b_register_urls(
    confirmation_url=f"{base}/payments/c2b/confirm/",
    validation_url=f"{base}/payments/c2b/validate/",
    response_type="Completed",
))

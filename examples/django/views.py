"""Example Django views: send an STK Push, receive its callback, and accept Paybill (C2B) payments.

settings.py
-----------
    MPESA = {
        "CONSUMER_KEY": env("MPESA_CONSUMER_KEY"),
        "CONSUMER_SECRET": env("MPESA_CONSUMER_SECRET"),
        "ENVIRONMENT": "sandbox",            # "production" when you go live
        "SHORTCODE": "174379",
        "PASSKEY": env("MPESA_PASSKEY"),
        "CALLBACK_URL": "https://yourdomain.co.ke/payments/stk/callback/",
    }

Callbacks must reach your server over public HTTPS. For local testing,
expose runserver with a tunnel such as ngrok or cloudflared.
"""

import json
import logging
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from lipa_mpesa import (
    MpesaClient,
    MpesaError,
    MpesaValidationError,
    c2b_accept,
    c2b_reject,
    callback_ack,
    parse_c2b,
    parse_stk_callback,
)

from .models import MpesaTransaction

log = logging.getLogger(__name__)

_client = None


def mpesa():
    """One shared client per process, so the OAuth token is cached."""
    global _client
    if _client is None:
        _client = MpesaClient.from_django()
    return _client


# --------------------------------------------------------------------------- STK Push


@login_required
@require_POST
def stk_push(request):
    """POST phone, amount, reference → sends the PIN prompt to the customer's phone."""
    phone = request.POST.get("phone", "")
    amount = request.POST.get("amount", "")
    reference = request.POST.get("reference", "")

    try:
        res = mpesa().stk_push(phone=phone, amount=amount, account_reference=reference,
                               description="Rent payment")
    except MpesaValidationError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    except MpesaError as exc:
        log.exception("STK push failed")
        return JsonResponse({"ok": False, "error": str(exc)}, status=502)

    tx = MpesaTransaction.objects.create(
        kind=MpesaTransaction.Kind.STK,
        phone=phone,
        amount=Decimal(str(amount)),
        account_reference=reference,
        merchant_request_id=res.merchant_request_id,
        checkout_request_id=res.checkout_request_id,
        initiated_by=request.user,
    )
    return JsonResponse({"ok": True, "id": tx.pk, "message": res.customer_message,
                         "checkout_request_id": res.checkout_request_id})


@csrf_exempt
def stk_callback(request):
    """Safaricom POSTs the STK result here. Always answer 200 so it doesn't retry forever."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        cb = parse_stk_callback(request.body)
    except MpesaValidationError:
        log.warning("Bad STK callback: %s", request.body[:500])
        return JsonResponse(callback_ack())

    with transaction.atomic():
        tx = (MpesaTransaction.objects.select_for_update()
              .filter(checkout_request_id=cb.checkout_request_id).first())
        if tx is None:
            log.warning("STK callback for unknown CheckoutRequestID %s", cb.checkout_request_id)
            return JsonResponse(callback_ack())
        if tx.status != MpesaTransaction.Status.PENDING:
            return JsonResponse(callback_ack())  # duplicate delivery

        tx.result_code = cb.result_code
        tx.result_desc = cb.result_desc
        tx.raw_callback = cb.raw
        if cb.is_successful:
            tx.status = MpesaTransaction.Status.PAID
            tx.receipt_number = cb.receipt_number
            tx.paid_at = cb.transaction_date
            # TODO: record the payment against the tenant's invoice here,
            # e.g. Payment.objects.create(tenant=..., amount=cb.amount, reference=cb.receipt_number)
        elif cb.was_cancelled:
            tx.status = MpesaTransaction.Status.CANCELLED
        else:
            tx.status = MpesaTransaction.Status.FAILED
        tx.save()

    return JsonResponse(callback_ack())


@login_required
def stk_status(request, pk):
    """Poll from the browser after sending the prompt; queries Daraja if no callback yet."""
    tx = get_object_or_404(MpesaTransaction, pk=pk)
    if tx.status == MpesaTransaction.Status.PENDING and tx.checkout_request_id:
        try:
            q = mpesa().stk_query(tx.checkout_request_id)
            if not q.is_pending:
                tx.result_code = q.result_code
                tx.result_desc = q.result_desc
                tx.status = (MpesaTransaction.Status.PAID if q.is_successful
                             else MpesaTransaction.Status.CANCELLED if q.result_code == 1032
                             else MpesaTransaction.Status.FAILED)
                tx.save(update_fields=["result_code", "result_desc", "status", "updated_at"])
        except MpesaError:
            log.exception("STK query failed")
    return JsonResponse({"status": tx.status, "receipt": tx.receipt_number, "message": tx.result_desc})


# --------------------------------------------------------------------------- C2B (Paybill)


@csrf_exempt
def c2b_validate(request):
    """Optional: approve or reject a Paybill payment before it completes.

    Only called if your shortcode has external validation enabled by Safaricom.
    """
    try:
        tx = parse_c2b(request.body)
    except MpesaValidationError:
        return JsonResponse(c2b_reject())
    if not tx.bill_ref_number.strip():
        return JsonResponse(c2b_reject("C2B00012", "Invalid account number"))
    return JsonResponse(c2b_accept())


@csrf_exempt
def c2b_confirm(request):
    """Safaricom POSTs every completed Paybill/Till payment here."""
    try:
        tx = parse_c2b(request.body)
    except MpesaValidationError:
        log.warning("Bad C2B confirmation: %s", request.body[:500])
        return JsonResponse(c2b_accept())

    MpesaTransaction.objects.get_or_create(
        receipt_number=tx.transaction_id,
        defaults={
            "kind": MpesaTransaction.Kind.C2B,
            "status": MpesaTransaction.Status.PAID,
            "phone": tx.msisdn,
            "amount": Decimal(str(tx.amount)),
            "account_reference": tx.bill_ref_number,  # what the tenant typed as "account number"
            "paid_at": tx.transaction_time,
            "payer_name": tx.full_name,
            "raw_callback": tx.raw,
            "result_code": 0,
        },
    )
    # TODO: match tx.bill_ref_number to a tenant/house and allocate the payment.
    return JsonResponse(c2b_accept())

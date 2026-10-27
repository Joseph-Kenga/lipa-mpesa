"""Example model for tracking M-Pesa payments in a Django app.

Copy into one of your apps (e.g. ``payments/models.py``) and run makemigrations.
"""

from django.conf import settings
from django.db import models


class MpesaTransaction(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PAID = "paid", "Paid"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    class Kind(models.TextChoices):
        STK = "stk", "STK Push"
        C2B = "c2b", "Paybill / Till"

    kind = models.CharField(max_length=5, choices=Kind.choices, default=Kind.STK)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)

    phone = models.CharField(max_length=15)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    account_reference = models.CharField(max_length=50, blank=True)  # e.g. invoice or house number

    # STK Push identifiers (set when the prompt is sent)
    merchant_request_id = models.CharField(max_length=60, blank=True)
    checkout_request_id = models.CharField(max_length=60, blank=True, unique=True, null=True)

    # Filled in when the payment completes
    receipt_number = models.CharField(max_length=20, blank=True, unique=True, null=True)
    result_code = models.IntegerField(null=True, blank=True)
    result_desc = models.CharField(max_length=255, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    payer_name = models.CharField(max_length=150, blank=True)

    raw_callback = models.JSONField(null=True, blank=True)
    initiated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_kind_display()} {self.amount} from {self.phone} ({self.status})"

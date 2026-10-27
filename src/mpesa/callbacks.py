"""Parse the JSON M-Pesa POSTs to your callback, confirmation and result URLs."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional, Union

from .exceptions import MpesaValidationError
from .utils import parse_mpesa_datetime

JSONLike = Union[str, bytes, Dict[str, Any]]


def _load(payload: JSONLike) -> Dict[str, Any]:
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise MpesaValidationError(f"Callback body is not valid JSON: {exc}") from None
    if not isinstance(payload, dict):
        raise MpesaValidationError("Callback body must be a JSON object")
    return payload


def _items_to_dict(items) -> Dict[str, Any]:
    """Turn ``[{"Name"/"Key": x, "Value": y}, ...]`` into ``{x: y}``."""
    out: Dict[str, Any] = {}
    if isinstance(items, dict):
        items = [items]
    for item in items or []:
        key = item.get("Name") or item.get("Key")
        if key:
            out[key] = item.get("Value")
    return out


# --------------------------------------------------------------------------- STK


@dataclass
class STKCallback:
    """Result of an STK Push, as sent to your ``CallBackURL``.

    ``result_code == 0`` means the customer paid. Common failures:
    ``1032`` cancelled by user, ``1037`` phone unreachable / timed out,
    ``2001`` wrong PIN, ``1`` insufficient balance.
    """

    merchant_request_id: str
    checkout_request_id: str
    result_code: int
    result_desc: str
    amount: Optional[float] = None
    receipt_number: Optional[str] = None
    transaction_date: Optional[datetime] = None
    phone_number: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def is_successful(self) -> bool:
        return self.result_code == 0

    @property
    def was_cancelled(self) -> bool:
        return self.result_code == 1032


def parse_stk_callback(payload: JSONLike) -> STKCallback:
    """Parse the body M-Pesa POSTs to your STK Push callback URL.

    Example::

        cb = parse_stk_callback(request.body)
        if cb.is_successful:
            Payment.objects.filter(checkout_id=cb.checkout_request_id).update(
                receipt=cb.receipt_number, status="paid")
    """
    data = _load(payload)
    try:
        stk = data["Body"]["stkCallback"]
    except (KeyError, TypeError):
        raise MpesaValidationError("Not an STK callback: missing Body.stkCallback") from None

    meta = _items_to_dict((stk.get("CallbackMetadata") or {}).get("Item"))
    phone = meta.get("PhoneNumber")
    return STKCallback(
        merchant_request_id=stk.get("MerchantRequestID", ""),
        checkout_request_id=stk.get("CheckoutRequestID", ""),
        result_code=int(stk.get("ResultCode", -1)),
        result_desc=stk.get("ResultDesc", ""),
        amount=meta.get("Amount"),
        receipt_number=meta.get("MpesaReceiptNumber"),
        transaction_date=parse_mpesa_datetime(meta.get("TransactionDate")),
        phone_number=str(phone) if phone is not None else None,
        metadata=meta,
        raw=data,
    )


# --------------------------------------------------------------------------- C2B


@dataclass
class C2BTransaction:
    """A Paybill/Till payment, as sent to your C2B validation or confirmation URL."""

    transaction_type: str
    transaction_id: str
    transaction_time: Optional[datetime]
    amount: float
    business_shortcode: str
    bill_ref_number: str  # The "account number" the customer typed
    invoice_number: str
    org_account_balance: str
    third_party_trans_id: str
    msisdn: str  # May be masked or hashed by Safaricom
    first_name: str
    middle_name: str
    last_name: str
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def full_name(self) -> str:
        return " ".join(p for p in (self.first_name, self.middle_name, self.last_name) if p)


def parse_c2b(payload: JSONLike) -> C2BTransaction:
    """Parse a C2B validation or confirmation request body."""
    d = _load(payload)
    if "TransID" not in d:
        raise MpesaValidationError("Not a C2B payload: missing TransID")
    try:
        amount = float(d.get("TransAmount") or 0)
    except (TypeError, ValueError):
        amount = 0.0
    return C2BTransaction(
        transaction_type=d.get("TransactionType", ""),
        transaction_id=d.get("TransID", ""),
        transaction_time=parse_mpesa_datetime(d.get("TransTime")),
        amount=amount,
        business_shortcode=str(d.get("BusinessShortCode", "")),
        bill_ref_number=d.get("BillRefNumber", "") or "",
        invoice_number=d.get("InvoiceNumber", "") or "",
        org_account_balance=str(d.get("OrgAccountBalance", "") or ""),
        third_party_trans_id=d.get("ThirdPartyTransID", "") or "",
        msisdn=str(d.get("MSISDN", "") or ""),
        first_name=d.get("FirstName", "") or "",
        middle_name=d.get("MiddleName", "") or "",
        last_name=d.get("LastName", "") or "",
        raw=d,
    )


def c2b_accept(message: str = "Accepted") -> Dict[str, str]:
    """Response body that tells M-Pesa to accept a C2B validation (also fine for confirmation)."""
    return {"ResultCode": "0", "ResultDesc": message}


def c2b_reject(code: str = "C2B00016", message: str = "Rejected") -> Dict[str, str]:
    """Response body that tells M-Pesa to reject a C2B payment at validation.

    Documented codes include ``C2B00012`` invalid account number, ``C2B00013``
    invalid amount and ``C2B00016`` other error.
    """
    return {"ResultCode": code, "ResultDesc": message}


# ------------------------------------------------- B2C / status / balance / reversal


@dataclass
class ResultCallback:
    """Asynchronous result posted to your ``ResultURL``.

    Used by B2C, transaction status, account balance and reversal.
    ``parameters`` holds the ``ResultParameters`` items as a dict,
    e.g. ``TransactionAmount``, ``ReceiverPartyPublicName``, ``AccountBalance``.
    """

    result_type: int
    result_code: int
    result_desc: str
    originator_conversation_id: str
    conversation_id: str
    transaction_id: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    reference_data: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def is_successful(self) -> bool:
        return self.result_code == 0


def parse_result(payload: JSONLike) -> ResultCallback:
    """Parse a ``Result`` callback (B2C, status, balance, reversal)."""
    data = _load(payload)
    result = data.get("Result")
    if not isinstance(result, dict):
        raise MpesaValidationError("Not a result callback: missing Result")
    params = _items_to_dict((result.get("ResultParameters") or {}).get("ResultParameter"))
    ref = _items_to_dict((result.get("ReferenceData") or {}).get("ReferenceItem"))
    return ResultCallback(
        result_type=int(result.get("ResultType", 0) or 0),
        result_code=int(result.get("ResultCode", -1)),
        result_desc=result.get("ResultDesc", ""),
        originator_conversation_id=result.get("OriginatorConversationID", ""),
        conversation_id=result.get("ConversationID", ""),
        transaction_id=result.get("TransactionID", ""),
        parameters=params,
        reference_data=ref,
        raw=data,
    )


def callback_ack() -> Dict[str, Any]:
    """Generic acknowledgement body to return from any callback URL."""
    return {"ResultCode": 0, "ResultDesc": "Accepted"}

import json
from datetime import datetime

import pytest

from lipa_mpesa import (
    MpesaValidationError,
    c2b_accept,
    c2b_reject,
    parse_c2b,
    parse_result,
    parse_stk_callback,
)
from lipa_mpesa.utils import EAT

STK_SUCCESS = {
    "Body": {
        "stkCallback": {
            "MerchantRequestID": "29115-34620561-1",
            "CheckoutRequestID": "ws_CO_191220191020363925",
            "ResultCode": 0,
            "ResultDesc": "The service request is processed successfully.",
            "CallbackMetadata": {
                "Item": [
                    {"Name": "Amount", "Value": 1.00},
                    {"Name": "MpesaReceiptNumber", "Value": "NLJ7RT61SV"},
                    {"Name": "Balance"},
                    {"Name": "TransactionDate", "Value": 20261027102115},
                    {"Name": "PhoneNumber", "Value": 254712345678},
                ]
            },
        }
    }
}

STK_CANCELLED = {
    "Body": {
        "stkCallback": {
            "MerchantRequestID": "29115-34620561-1",
            "CheckoutRequestID": "ws_CO_191220191020363925",
            "ResultCode": 1032,
            "ResultDesc": "Request cancelled by user",
        }
    }
}


def test_stk_success_from_bytes():
    cb = parse_stk_callback(json.dumps(STK_SUCCESS).encode())
    assert cb.is_successful
    assert cb.receipt_number == "NLJ7RT61SV"
    assert cb.amount == 1.0
    assert cb.phone_number == "254712345678"
    assert cb.transaction_date == datetime(2026, 10, 27, 10, 21, 15, tzinfo=EAT)
    assert cb.metadata["Balance"] is None


def test_stk_cancelled():
    cb = parse_stk_callback(STK_CANCELLED)
    assert not cb.is_successful and cb.was_cancelled
    assert cb.receipt_number is None and cb.amount is None


def test_stk_bad_payload():
    with pytest.raises(MpesaValidationError):
        parse_stk_callback({"foo": "bar"})
    with pytest.raises(MpesaValidationError):
        parse_stk_callback(b"not json")


C2B = {
    "TransactionType": "Pay Bill",
    "TransID": "RKTQDM7W6S",
    "TransTime": "20261027102115",
    "TransAmount": "15000.00",
    "BusinessShortCode": "600638",
    "BillRefNumber": "HOUSE-A12",
    "InvoiceNumber": "",
    "OrgAccountBalance": "49197.00",
    "ThirdPartyTransID": "",
    "MSISDN": "2547 ***** 126",
    "FirstName": "Jane",
    "MiddleName": "",
    "LastName": "Doe",
}


def test_c2b():
    tx = parse_c2b(C2B)
    assert tx.transaction_id == "RKTQDM7W6S"
    assert tx.amount == 15000.0
    assert tx.bill_ref_number == "HOUSE-A12"
    assert tx.full_name == "Jane Doe"
    assert tx.transaction_time.year == 2026


def test_c2b_responses():
    assert c2b_accept() == {"ResultCode": "0", "ResultDesc": "Accepted"}
    assert c2b_reject("C2B00012", "Invalid account")["ResultCode"] == "C2B00012"


RESULT = {
    "Result": {
        "ResultType": 0,
        "ResultCode": 0,
        "ResultDesc": "The service request is processed successfully.",
        "OriginatorConversationID": "10571-7910404-1",
        "ConversationID": "AG_20191219_00004e48cf7e3533f581",
        "TransactionID": "NLJ41HAY6Q",
        "ResultParameters": {
            "ResultParameter": [
                {"Key": "TransactionAmount", "Value": 10},
                {"Key": "TransactionReceipt", "Value": "NLJ41HAY6Q"},
                {"Key": "ReceiverPartyPublicName", "Value": "254708374149 - John Doe"},
            ]
        },
        "ReferenceData": {"ReferenceItem": {"Key": "QueueTimeoutURL", "Value": "https://x/timeout"}},
    }
}


def test_result():
    r = parse_result(RESULT)
    assert r.is_successful
    assert r.transaction_id == "NLJ41HAY6Q"
    assert r.parameters["TransactionAmount"] == 10
    assert r.parameters["ReceiverPartyPublicName"].endswith("John Doe")
    assert r.reference_data["QueueTimeoutURL"] == "https://x/timeout"


def test_result_bad():
    with pytest.raises(MpesaValidationError):
        parse_result({"Body": {}})

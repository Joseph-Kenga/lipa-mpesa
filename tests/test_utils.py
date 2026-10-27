import base64
from datetime import datetime, timezone

import pytest

from lipa_mpesa import MpesaValidationError, normalize_amount, normalize_phone, parse_mpesa_datetime
from lipa_mpesa.utils import EAT, stk_password, timestamp


@pytest.mark.parametrize(
    "raw",
    ["0712345678", "712345678", "+254712345678", "254712345678", "254 712 345 678", "0712-345-678", 254712345678],
)
def test_normalize_phone_safaricom_formats(raw):
    assert normalize_phone(raw) == "254712345678"


def test_normalize_phone_01_prefix():
    assert normalize_phone("0110123456") == "254110123456"


@pytest.mark.parametrize("bad", ["", "abc", "0812345678", "07123", "2547123456789", "+1 555 123 4567"])
def test_normalize_phone_rejects(bad):
    with pytest.raises(MpesaValidationError):
        normalize_phone(bad)


@pytest.mark.parametrize("raw,expected", [(10, 10), ("250", 250), (10.4, 10), (10.5, 11), ("1", 1)])
def test_normalize_amount(raw, expected):
    assert normalize_amount(raw) == expected


@pytest.mark.parametrize("bad", [0, -5, "0.4", "abc", None])
def test_normalize_amount_rejects(bad):
    with pytest.raises(MpesaValidationError):
        normalize_amount(bad)


def test_timestamp_is_eat():
    utc = datetime(2026, 10, 26, 23, 1, 51, tzinfo=timezone.utc)
    assert timestamp(utc) == "20261027020151"  # UTC+3


def test_stk_password():
    pw = stk_password("174379", "passkey", "20261027020151")
    assert base64.b64decode(pw).decode() == "174379passkey20261027020151"


def test_parse_mpesa_datetime():
    dt = parse_mpesa_datetime(20261027020151)
    assert dt == datetime(2026, 10, 27, 2, 1, 51, tzinfo=EAT)
    assert parse_mpesa_datetime("") is None
    assert parse_mpesa_datetime("garbage") is None

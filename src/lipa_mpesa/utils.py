"""Helpers: phone numbers, amounts, timestamps, passwords and security credentials."""

from __future__ import annotations

import base64
import re
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Union

from .exceptions import MpesaConfigError, MpesaValidationError

# M-Pesa timestamps are always East Africa Time (UTC+3, no DST).
EAT = timezone(timedelta(hours=3), name="EAT")

_PHONE_RE = re.compile(r"^254(7|1)\d{8}$")


def normalize_phone(phone: Union[str, int]) -> str:
    """Return a Kenyan mobile number in the ``2547XXXXXXXX`` / ``2541XXXXXXXX`` format.

    Accepts ``0712345678``, ``712345678``, ``+254712345678``, ``254 712 345 678``,
    ``0112345678`` and similar.

    Raises:
        MpesaValidationError: if the number is not a valid Kenyan mobile number.
    """
    raw = str(phone).strip()
    digits = re.sub(r"[\s\-()]", "", raw).lstrip("+")
    if not digits.isdigit():
        raise MpesaValidationError(f"Invalid phone number: {phone!r}")

    if digits.startswith("0") and len(digits) == 10:
        digits = "254" + digits[1:]
    elif len(digits) == 9 and digits[0] in "71":
        digits = "254" + digits

    if not _PHONE_RE.match(digits):
        raise MpesaValidationError(
            f"Invalid Kenyan mobile number: {phone!r}. Expected e.g. 0712345678 or 254712345678."
        )
    return digits


def normalize_amount(amount: Union[int, float, str, Decimal]) -> int:
    """Return the amount as a whole number of shillings.

    M-Pesa only accepts whole shillings. Values with cents are rounded half-up
    (``10.5`` → ``11``). Amounts below 1 are rejected.
    """
    try:
        value = Decimal(str(amount)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        raise MpesaValidationError(f"Invalid amount: {amount!r}") from None
    if value < 1:
        raise MpesaValidationError(f"Amount must be at least KES 1, got {amount!r}")
    return int(value)


def timestamp(now: datetime | None = None) -> str:
    """Return the current time in EAT as ``YYYYMMDDHHMMSS``."""
    now = now or datetime.now(EAT)
    if now.tzinfo is None:
        now = now.replace(tzinfo=EAT)
    return now.astimezone(EAT).strftime("%Y%m%d%H%M%S")


def stk_password(shortcode: str, passkey: str, ts: str) -> str:
    """Return the Lipa Na M-Pesa password: ``base64(shortcode + passkey + timestamp)``."""
    return base64.b64encode(f"{shortcode}{passkey}{ts}".encode()).decode()


def parse_mpesa_datetime(value: Union[str, int, None]) -> datetime | None:
    """Turn an M-Pesa ``20261026230151``-style value into an aware ``datetime`` in EAT."""
    if value in (None, ""):
        return None
    text = str(value)
    for fmt in ("%Y%m%d%H%M%S", "%d.%m.%Y %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=EAT)
        except ValueError:
            continue
    return None


def generate_security_credential(initiator_password: str, certificate: Union[str, bytes]) -> str:
    """Encrypt the initiator password with Safaricom's public certificate.

    Needed for B2C, transaction status, account balance and reversal.

    Args:
        initiator_password: The password of the API initiator user from the M-Pesa org portal.
        certificate: Path to the ``.cer`` file Safaricom provides (sandbox or production),
            or the certificate contents as bytes / a PEM string.

    Returns:
        Base64 string to send as ``SecurityCredential``.

    Requires the optional ``cryptography`` package: ``pip install lipa-mpesa[crypto]``.
    """
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives.asymmetric import padding
    except ImportError:  # pragma: no cover - exercised only without the extra
        raise MpesaConfigError(
            "generate_security_credential needs the 'cryptography' package. "
            "Install it with: pip install lipa-mpesa[crypto]"
        ) from None

    if isinstance(certificate, str) and "-----BEGIN" not in certificate:
        with open(certificate, "rb") as fh:
            cert_bytes = fh.read()
    elif isinstance(certificate, str):
        cert_bytes = certificate.encode()
    else:
        cert_bytes = certificate

    try:
        if b"-----BEGIN" in cert_bytes:
            cert = x509.load_pem_x509_certificate(cert_bytes)
        else:
            cert = x509.load_der_x509_certificate(cert_bytes)
    except ValueError as exc:
        raise MpesaConfigError(f"Could not read the M-Pesa certificate: {exc}") from None

    encrypted = cert.public_key().encrypt(initiator_password.encode(), padding.PKCS1v15())
    return base64.b64encode(encrypted).decode()

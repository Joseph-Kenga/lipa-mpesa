"""Settings for the M-Pesa client, loadable from code, environment variables or Django settings."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from typing import Optional

from .exceptions import MpesaConfigError

SANDBOX_URL = "https://sandbox.safaricom.co.ke"
PRODUCTION_URL = "https://api.safaricom.co.ke"

# Publicly documented Daraja sandbox values for Lipa Na M-Pesa Online.
SANDBOX_SHORTCODE = "174379"
SANDBOX_PASSKEY = "bfb279f9aa9bdbcf158e97dd71a467cd2e0c893059b10f78e6b72ada1ed2c919"


@dataclass
class MpesaConfig:
    """Everything the client needs to talk to Daraja.

    Only ``consumer_key`` and ``consumer_secret`` are always required. The rest
    are needed by the specific API you call, and are checked at call time.
    """

    consumer_key: str
    consumer_secret: str
    environment: str = "sandbox"  # "sandbox" or "production"

    # Lipa Na M-Pesa Online (STK Push)
    shortcode: Optional[str] = None  # Paybill number, or Till's *store* number for Buy Goods
    passkey: Optional[str] = None
    till_number: Optional[str] = None  # Set for Buy Goods; PartyB becomes this till
    callback_url: Optional[str] = None  # Default STK callback URL

    # B2C / status / balance / reversal
    initiator_name: Optional[str] = None
    security_credential: Optional[str] = None  # Already-encrypted credential
    result_url: Optional[str] = None
    timeout_url: Optional[str] = None

    timeout: float = 30.0  # seconds for each HTTP request
    base_url: Optional[str] = field(default=None)  # Override the API host (e.g. for a mock)

    def __post_init__(self):
        self.environment = (self.environment or "sandbox").lower().strip()
        if self.environment not in ("sandbox", "production"):
            raise MpesaConfigError("environment must be 'sandbox' or 'production'")
        if not self.consumer_key or not self.consumer_secret:
            raise MpesaConfigError("consumer_key and consumer_secret are required")
        for name in ("shortcode", "till_number"):
            value = getattr(self, name)
            if value is not None:
                setattr(self, name, str(value).strip())

    @property
    def api_url(self) -> str:
        if self.base_url:
            return self.base_url.rstrip("/")
        return PRODUCTION_URL if self.environment == "production" else SANDBOX_URL

    @property
    def is_buy_goods(self) -> bool:
        return bool(self.till_number)

    # ---- Loaders ----

    @classmethod
    def from_env(cls, prefix: str = "MPESA_") -> "MpesaConfig":
        """Build a config from environment variables.

        Reads ``MPESA_CONSUMER_KEY``, ``MPESA_CONSUMER_SECRET``, ``MPESA_ENVIRONMENT``,
        ``MPESA_SHORTCODE``, ``MPESA_PASSKEY``, ``MPESA_TILL_NUMBER``, ``MPESA_CALLBACK_URL``,
        ``MPESA_INITIATOR_NAME``, ``MPESA_SECURITY_CREDENTIAL``, ``MPESA_RESULT_URL``,
        ``MPESA_TIMEOUT_URL``, ``MPESA_TIMEOUT`` and ``MPESA_BASE_URL``.
        """
        values = {}
        for f in fields(cls):
            raw = os.environ.get(prefix + f.name.upper())
            if raw is None or raw == "":
                continue
            values[f.name] = float(raw) if f.name == "timeout" else raw
        try:
            return cls(**values)
        except TypeError as exc:
            raise MpesaConfigError(
                f"Missing M-Pesa setting in the environment ({prefix}CONSUMER_KEY / "
                f"{prefix}CONSUMER_SECRET): {exc}"
            ) from None

    @classmethod
    def from_django(cls, setting_name: str = "MPESA") -> "MpesaConfig":
        """Build a config from a dict in Django settings, e.g. ``settings.MPESA = {...}``.

        Keys are the field names of this class, in upper or lower case.
        """
        from django.conf import settings  # imported lazily so Django stays optional

        data = getattr(settings, setting_name, None)
        if not isinstance(data, dict):
            raise MpesaConfigError(f"settings.{setting_name} must be a dict of M-Pesa settings")
        allowed = {f.name for f in fields(cls)}
        values = {k.lower(): v for k, v in data.items() if k.lower() in allowed}
        try:
            return cls(**values)
        except TypeError as exc:
            raise MpesaConfigError(f"Incomplete settings.{setting_name}: {exc}") from None

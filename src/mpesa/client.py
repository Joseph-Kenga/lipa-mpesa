"""HTTP client for the Safaricom M-Pesa Daraja API."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Union

import requests

from .config import MpesaConfig
from .exceptions import (
    MpesaAPIError,
    MpesaAuthError,
    MpesaConfigError,
    MpesaConnectionError,
    MpesaValidationError,
)
from .utils import normalize_amount, normalize_phone, stk_password, timestamp

log = logging.getLogger("mpesa")

Number = Union[int, float, str]

# Daraja endpoint paths. Override on the client (client.paths["b2c"] = "...") if
# Safaricom moves you to a newer version of an endpoint.
DEFAULT_PATHS = {
    "oauth": "/oauth/v1/generate?grant_type=client_credentials",
    "stk_push": "/mpesa/stkpush/v1/processrequest",
    "stk_query": "/mpesa/stkpushquery/v1/query",
    "c2b_register": "/mpesa/c2b/v1/registerurl",
    "c2b_simulate": "/mpesa/c2b/v1/simulate",
    "b2c": "/mpesa/b2c/v1/paymentrequest",
    "transaction_status": "/mpesa/transactionstatus/v1/query",
    "account_balance": "/mpesa/accountbalance/v1/query",
    "reversal": "/mpesa/reversal/v1/request",
}

# Daraja answers an STK query with this error while the customer hasn't acted yet.
_STK_PENDING_CODES = {"500.001.1001"}


@dataclass
class STKPushResponse:
    """Daraja's immediate answer to an STK Push.

    This only means the prompt was sent to the phone. Whether the customer paid
    arrives later at your callback URL (see ``parse_stk_callback``) or via ``stk_query``.
    Save ``checkout_request_id`` so you can match the callback to your record.
    """

    merchant_request_id: str
    checkout_request_id: str
    response_code: str
    response_description: str
    customer_message: str
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def is_accepted(self) -> bool:
        return str(self.response_code) == "0"


@dataclass
class STKQueryResponse:
    """Status of an STK Push.

    ``is_pending`` is True while the customer hasn't entered their PIN yet.
    Otherwise ``result_code == 0`` means paid, and anything else is a failure
    (``1032`` cancelled, ``1037`` timed out, ``2001`` wrong PIN, ``1`` insufficient funds).
    """

    checkout_request_id: str
    result_code: Optional[int]
    result_desc: str
    is_pending: bool = False
    merchant_request_id: str = ""
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def is_successful(self) -> bool:
        return not self.is_pending and self.result_code == 0


class MpesaClient:
    """Client for the M-Pesa Daraja API.

    Example::

        from mpesa import MpesaClient, MpesaConfig

        client = MpesaClient(MpesaConfig(
            consumer_key="...", consumer_secret="...",
            shortcode="174379", passkey="...",
            callback_url="https://example.com/mpesa/callback/",
        ))
        res = client.stk_push(phone="0712345678", amount=1, account_reference="INV001")
        print(res.checkout_request_id)

    The client is thread-safe and caches the OAuth token until shortly before it expires,
    so create one per process and reuse it.
    """

    TOKEN_REFRESH_MARGIN = 60  # seconds before expiry to fetch a new token

    def __init__(self, config: Optional[MpesaConfig] = None, session: Optional[requests.Session] = None, **kwargs):
        if config is None:
            config = MpesaConfig(**kwargs) if kwargs else MpesaConfig.from_env()
        elif kwargs:
            raise TypeError("Pass either a MpesaConfig or keyword settings, not both")
        self.config = config
        self.session = session or requests.Session()
        self.paths = dict(DEFAULT_PATHS)
        self._token: Optional[str] = None
        self._token_expires_at: float = 0.0
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ factories

    @classmethod
    def from_env(cls, prefix: str = "MPESA_") -> "MpesaClient":
        return cls(MpesaConfig.from_env(prefix))

    @classmethod
    def from_django(cls, setting_name: str = "MPESA") -> "MpesaClient":
        return cls(MpesaConfig.from_django(setting_name))

    # ------------------------------------------------------------------ auth

    def get_access_token(self, force_refresh: bool = False) -> str:
        """Return a valid OAuth access token, fetching a new one only when needed."""
        with self._lock:
            if not force_refresh and self._token and time.monotonic() < self._token_expires_at:
                return self._token

            url = self.config.api_url + self.paths["oauth"]
            try:
                resp = self.session.get(
                    url,
                    auth=(self.config.consumer_key, self.config.consumer_secret),
                    timeout=self.config.timeout,
                )
            except requests.RequestException as exc:
                raise MpesaConnectionError(f"Could not reach M-Pesa for a token: {exc}") from exc

            if resp.status_code != 200:
                body = _json_or_text(resp)
                raise MpesaAuthError(
                    f"M-Pesa rejected the consumer key/secret (HTTP {resp.status_code}): "
                    f"{body.get('errorMessage') or body.get('raw') or body}"
                )
            data = _json_or_text(resp)
            token = data.get("access_token")
            if not token:
                raise MpesaAuthError(f"No access_token in M-Pesa response: {data}")
            try:
                expires_in = int(data.get("expires_in", 3599))
            except (TypeError, ValueError):
                expires_in = 3599

            self._token = token
            self._token_expires_at = time.monotonic() + max(expires_in - self.TOKEN_REFRESH_MARGIN, 0)
            return token

    # ------------------------------------------------------------------ transport

    def _post(self, path_key: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = self.config.api_url + self.paths[path_key]
        log.debug("M-Pesa POST %s %s", url, {k: v for k, v in payload.items() if k not in ("Password", "SecurityCredential")})

        for attempt in (1, 2):
            headers = {
                "Authorization": f"Bearer {self.get_access_token(force_refresh=attempt == 2)}",
                "Content-Type": "application/json",
            }
            try:
                resp = self.session.post(url, json=payload, headers=headers, timeout=self.config.timeout)
            except requests.RequestException as exc:
                raise MpesaConnectionError(f"Could not reach M-Pesa: {exc}") from exc

            # An expired/invalid token gets one retry with a fresh token.
            if resp.status_code == 401 and attempt == 1:
                log.info("M-Pesa token rejected; refreshing and retrying once")
                continue
            break

        data = _json_or_text(resp)
        if resp.status_code >= 400 or "errorCode" in data:
            raise MpesaAPIError(
                status_code=resp.status_code,
                error_code=data.get("errorCode"),
                error_message=data.get("errorMessage") or data.get("raw"),
                response=data,
            )
        return data

    # ------------------------------------------------------------------ helpers

    def _require(self, **values):
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise MpesaConfigError(
                "Missing M-Pesa setting(s): " + ", ".join(missing)
                + ". Pass them to the method or set them on MpesaConfig."
            )

    def _initiator(self, initiator_name, security_credential, result_url, timeout_url):
        initiator_name = initiator_name or self.config.initiator_name
        security_credential = security_credential or self.config.security_credential
        result_url = result_url or self.config.result_url
        timeout_url = timeout_url or self.config.timeout_url
        self._require(
            initiator_name=initiator_name,
            security_credential=security_credential,
            result_url=result_url,
            timeout_url=timeout_url,
        )
        return initiator_name, security_credential, result_url, timeout_url

    # ------------------------------------------------------------------ STK Push

    def stk_push(
        self,
        phone: Union[str, int],
        amount: Number,
        account_reference: str,
        description: str = "Payment",
        callback_url: Optional[str] = None,
    ) -> STKPushResponse:
        """Send a Lipa Na M-Pesa Online (STK Push) prompt to the customer's phone.

        Args:
            phone: Customer's number in any common Kenyan format.
            amount: Whole shillings (cents are rounded).
            account_reference: Shown to the customer and on the statement. Max 12 characters
                (e.g. an invoice or tenant number). Longer values are cut to 12.
            description: Short note, max 13 characters (cut if longer).
            callback_url: HTTPS URL that receives the result. Defaults to ``config.callback_url``.
        """
        cfg = self.config
        callback_url = callback_url or cfg.callback_url
        self._require(shortcode=cfg.shortcode, passkey=cfg.passkey, callback_url=callback_url)
        if not callback_url.lower().startswith("https://"):
            raise MpesaValidationError("callback_url must be a public HTTPS URL")
        if not account_reference:
            raise MpesaValidationError("account_reference is required")

        msisdn = normalize_phone(phone)
        ts = timestamp()
        payload = {
            "BusinessShortCode": cfg.shortcode,
            "Password": stk_password(cfg.shortcode, cfg.passkey, ts),
            "Timestamp": ts,
            "TransactionType": "CustomerBuyGoodsOnline" if cfg.is_buy_goods else "CustomerPayBillOnline",
            "Amount": normalize_amount(amount),
            "PartyA": msisdn,
            "PartyB": cfg.till_number if cfg.is_buy_goods else cfg.shortcode,
            "PhoneNumber": msisdn,
            "CallBackURL": callback_url,
            "AccountReference": str(account_reference)[:12],
            "TransactionDesc": (description or "Payment")[:13],
        }
        data = self._post("stk_push", payload)
        return STKPushResponse(
            merchant_request_id=data.get("MerchantRequestID", ""),
            checkout_request_id=data.get("CheckoutRequestID", ""),
            response_code=str(data.get("ResponseCode", "")),
            response_description=data.get("ResponseDescription", ""),
            customer_message=data.get("CustomerMessage", ""),
            raw=data,
        )

    def stk_query(self, checkout_request_id: str) -> STKQueryResponse:
        """Check the status of an STK Push using its ``CheckoutRequestID``.

        Useful when the callback didn't arrive. Returns ``is_pending=True`` while the
        customer is still on the PIN prompt. Daraja rate-limits this endpoint, so poll
        sparingly (e.g. every 5–10 seconds, for at most a couple of minutes).
        """
        cfg = self.config
        self._require(shortcode=cfg.shortcode, passkey=cfg.passkey, checkout_request_id=checkout_request_id)
        ts = timestamp()
        payload = {
            "BusinessShortCode": cfg.shortcode,
            "Password": stk_password(cfg.shortcode, cfg.passkey, ts),
            "Timestamp": ts,
            "CheckoutRequestID": checkout_request_id,
        }
        try:
            data = self._post("stk_query", payload)
        except MpesaAPIError as exc:
            if exc.error_code in _STK_PENDING_CODES:
                return STKQueryResponse(
                    checkout_request_id=checkout_request_id,
                    result_code=None,
                    result_desc=exc.error_message or "The transaction is being processed",
                    is_pending=True,
                    raw=exc.response,
                )
            raise

        code = data.get("ResultCode")
        return STKQueryResponse(
            checkout_request_id=data.get("CheckoutRequestID", checkout_request_id),
            merchant_request_id=data.get("MerchantRequestID", ""),
            result_code=int(code) if code not in (None, "") else None,
            result_desc=data.get("ResultDesc", ""),
            is_pending=code in (None, ""),
            raw=data,
        )

    # ------------------------------------------------------------------ C2B

    def c2b_register_urls(
        self,
        confirmation_url: str,
        validation_url: str,
        response_type: str = "Completed",
        shortcode: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Register the URLs M-Pesa calls when someone pays your Paybill/Till directly.

        Args:
            response_type: What M-Pesa does if your validation URL can't be reached:
                ``"Completed"`` (accept the payment) or ``"Cancelled"`` (reject it).

        Note: Safaricom rejects URLs containing words such as "mpesa", "safaricom",
        "exec", "cmd" or "sql", so use paths like ``/payments/c2b/confirm/``.
        """
        shortcode = shortcode or self.config.shortcode
        self._require(shortcode=shortcode, confirmation_url=confirmation_url, validation_url=validation_url)
        if response_type not in ("Completed", "Cancelled"):
            raise MpesaValidationError("response_type must be 'Completed' or 'Cancelled'")
        for url in (confirmation_url, validation_url):
            if "mpesa" in url.lower() or "safaricom" in url.lower():
                raise MpesaValidationError(
                    f"Safaricom rejects C2B URLs containing 'mpesa' or 'safaricom': {url}"
                )
        return self._post(
            "c2b_register",
            {
                "ShortCode": shortcode,
                "ResponseType": response_type,
                "ConfirmationURL": confirmation_url,
                "ValidationURL": validation_url,
            },
        )

    def c2b_simulate(
        self,
        phone: Union[str, int],
        amount: Number,
        bill_ref_number: str = "",
        shortcode: Optional[str] = None,
        buy_goods: bool = False,
    ) -> Dict[str, Any]:
        """Simulate a customer paying your shortcode. **Sandbox only.**"""
        if self.config.environment != "sandbox":
            raise MpesaConfigError("c2b_simulate is only available in the sandbox")
        shortcode = shortcode or self.config.shortcode
        self._require(shortcode=shortcode)
        payload = {
            "ShortCode": shortcode,
            "CommandID": "CustomerBuyGoodsOnline" if buy_goods else "CustomerPayBillOnline",
            "Amount": normalize_amount(amount),
            "Msisdn": normalize_phone(phone),
        }
        if not buy_goods:
            payload["BillRefNumber"] = bill_ref_number or "TEST"
        return self._post("c2b_simulate", payload)

    # ------------------------------------------------------------------ B2C

    def b2c_payment(
        self,
        phone: Union[str, int],
        amount: Number,
        remarks: str = "Payment",
        occasion: str = "",
        command_id: str = "BusinessPayment",
        shortcode: Optional[str] = None,
        initiator_name: Optional[str] = None,
        security_credential: Optional[str] = None,
        result_url: Optional[str] = None,
        timeout_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send money from your B2C shortcode to a customer (refunds, salaries, payouts).

        Args:
            command_id: ``"BusinessPayment"``, ``"SalaryPayment"`` or ``"PromotionPayment"``.

        Returns Daraja's acknowledgement (``ConversationID``, ``OriginatorConversationID``).
        The final outcome is posted to ``result_url`` (parse it with ``parse_result``).
        """
        if command_id not in ("BusinessPayment", "SalaryPayment", "PromotionPayment"):
            raise MpesaValidationError("command_id must be BusinessPayment, SalaryPayment or PromotionPayment")
        shortcode = shortcode or self.config.shortcode
        self._require(shortcode=shortcode)
        initiator, credential, result_url, timeout_url = self._initiator(
            initiator_name, security_credential, result_url, timeout_url
        )
        return self._post(
            "b2c",
            {
                "InitiatorName": initiator,
                "SecurityCredential": credential,
                "CommandID": command_id,
                "Amount": normalize_amount(amount),
                "PartyA": shortcode,
                "PartyB": normalize_phone(phone),
                "Remarks": (remarks or "Payment")[:100],
                "QueueTimeOutURL": timeout_url,
                "ResultURL": result_url,
                "Occasion": (occasion or "")[:100],
            },
        )

    # ------------------------------------------------------------------ status / balance / reversal

    def transaction_status(
        self,
        transaction_id: str,
        remarks: str = "Status check",
        occasion: str = "",
        identifier_type: str = "4",
        shortcode: Optional[str] = None,
        initiator_name: Optional[str] = None,
        security_credential: Optional[str] = None,
        result_url: Optional[str] = None,
        timeout_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Look up any M-Pesa transaction by its receipt number (e.g. ``"SJQ7XXXXXX"``).

        ``identifier_type``: ``"4"`` shortcode (default), ``"2"`` till, ``"1"`` MSISDN.
        The details arrive at ``result_url``.
        """
        shortcode = shortcode or self.config.shortcode
        self._require(shortcode=shortcode, transaction_id=transaction_id)
        initiator, credential, result_url, timeout_url = self._initiator(
            initiator_name, security_credential, result_url, timeout_url
        )
        return self._post(
            "transaction_status",
            {
                "Initiator": initiator,
                "SecurityCredential": credential,
                "CommandID": "TransactionStatusQuery",
                "TransactionID": transaction_id,
                "PartyA": shortcode,
                "IdentifierType": identifier_type,
                "ResultURL": result_url,
                "QueueTimeOutURL": timeout_url,
                "Remarks": remarks or "Status check",
                "Occasion": occasion or "",
            },
        )

    def account_balance(
        self,
        remarks: str = "Balance check",
        identifier_type: str = "4",
        shortcode: Optional[str] = None,
        initiator_name: Optional[str] = None,
        security_credential: Optional[str] = None,
        result_url: Optional[str] = None,
        timeout_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Request the balance of your shortcode. The balance arrives at ``result_url``."""
        shortcode = shortcode or self.config.shortcode
        self._require(shortcode=shortcode)
        initiator, credential, result_url, timeout_url = self._initiator(
            initiator_name, security_credential, result_url, timeout_url
        )
        return self._post(
            "account_balance",
            {
                "Initiator": initiator,
                "SecurityCredential": credential,
                "CommandID": "AccountBalance",
                "PartyA": shortcode,
                "IdentifierType": identifier_type,
                "Remarks": remarks or "Balance check",
                "QueueTimeOutURL": timeout_url,
                "ResultURL": result_url,
            },
        )

    def reversal(
        self,
        transaction_id: str,
        amount: Number,
        remarks: str = "Reversal",
        occasion: str = "",
        shortcode: Optional[str] = None,
        initiator_name: Optional[str] = None,
        security_credential: Optional[str] = None,
        result_url: Optional[str] = None,
        timeout_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Reverse a payment received on your shortcode. The outcome arrives at ``result_url``."""
        shortcode = shortcode or self.config.shortcode
        self._require(shortcode=shortcode, transaction_id=transaction_id)
        initiator, credential, result_url, timeout_url = self._initiator(
            initiator_name, security_credential, result_url, timeout_url
        )
        return self._post(
            "reversal",
            {
                "Initiator": initiator,
                "SecurityCredential": credential,
                "CommandID": "TransactionReversal",
                "TransactionID": transaction_id,
                "Amount": normalize_amount(amount),
                "ReceiverParty": shortcode,
                "RecieverIdentifierType": "11",  # Daraja's own spelling
                "ResultURL": result_url,
                "QueueTimeOutURL": timeout_url,
                "Remarks": remarks or "Reversal",
                "Occasion": occasion or "",
            },
        )


def _json_or_text(resp: requests.Response) -> Dict[str, Any]:
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {"raw": data}
    except ValueError:
        return {"raw": resp.text}

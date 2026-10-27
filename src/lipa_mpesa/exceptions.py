"""Errors raised by the M-Pesa client."""


class MpesaError(Exception):
    """Base class for every error this library raises."""


class MpesaConfigError(MpesaError):
    """A required setting (key, secret, passkey, shortcode…) is missing or invalid."""


class MpesaValidationError(MpesaError):
    """An argument failed validation before any request was sent (bad phone, amount…)."""


class MpesaAuthError(MpesaError):
    """Daraja refused the consumer key/secret when requesting an access token."""


class MpesaAPIError(MpesaError):
    """Daraja returned an error response.

    Attributes:
        status_code: HTTP status code.
        error_code: Daraja ``errorCode`` (e.g. ``"400.002.02"``) or ``ResponseCode`` when present.
        error_message: Daraja's human-readable message.
        response: The parsed JSON body (or ``{"raw": text}`` if it wasn't JSON).
    """

    def __init__(self, status_code, error_code=None, error_message=None, response=None):
        self.status_code = status_code
        self.error_code = error_code
        self.error_message = error_message
        self.response = response or {}
        msg = f"M-Pesa API error (HTTP {status_code})"
        if error_code:
            msg += f" [{error_code}]"
        if error_message:
            msg += f": {error_message}"
        super().__init__(msg)


class MpesaConnectionError(MpesaError):
    """The request never got a response (timeout, DNS, TLS, connection reset)."""

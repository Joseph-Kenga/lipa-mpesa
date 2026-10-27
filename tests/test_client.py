import base64

import pytest
import requests

from lipa_mpesa import (
    MpesaAPIError,
    MpesaAuthError,
    MpesaClient,
    MpesaConfig,
    MpesaConfigError,
    MpesaConnectionError,
    MpesaValidationError,
)

from .conftest import FakeResponse, FakeSession


# ------------------------------------------------------------------ auth


def test_token_is_fetched_once_and_cached(client, session):
    assert client.get_access_token() == "tok-123"
    assert client.get_access_token() == "tok-123"
    gets = [c for c in session.calls if c["method"] == "GET"]
    assert len(gets) == 1
    assert gets[0]["auth"] == ("key", "secret")
    assert gets[0]["url"].startswith("https://sandbox.safaricom.co.ke/oauth/v1/generate")


def test_production_url(config):
    config.environment = "production"
    session = FakeSession()
    session.add("/oauth/", FakeResponse(200, {"access_token": "t", "expires_in": "3599"}))
    MpesaClient(config, session=session).get_access_token()
    assert session.calls[0]["url"].startswith("https://api.safaricom.co.ke/")


def test_bad_credentials_raise_auth_error(config):
    session = FakeSession()
    session.add("/oauth/", FakeResponse(400, {"errorCode": "400.008.01", "errorMessage": "Invalid Authentication passed"}))
    with pytest.raises(MpesaAuthError, match="Invalid Authentication"):
        MpesaClient(config, session=session).get_access_token()


def test_expired_token_is_refreshed_once(client, session):
    session.add(
        "/stkpush/",
        FakeResponse(401, {"errorCode": "404.001.03", "errorMessage": "Invalid Access Token"}),
        FakeResponse(200, {"MerchantRequestID": "m", "CheckoutRequestID": "ws_CO_1", "ResponseCode": "0",
                            "ResponseDescription": "ok", "CustomerMessage": "ok"}),
    )
    res = client.stk_push("0712345678", 1, "INV1")
    assert res.checkout_request_id == "ws_CO_1"
    assert len([c for c in session.calls if c["method"] == "GET"]) == 2


def test_connection_error(client, session):
    session.add("/stkpush/", requests.ConnectionError("boom"))
    with pytest.raises(MpesaConnectionError):
        client.stk_push("0712345678", 1, "INV1")


# ------------------------------------------------------------------ STK push


STK_OK = FakeResponse(200, {
    "MerchantRequestID": "29115-34620561-1",
    "CheckoutRequestID": "ws_CO_191220191020363925",
    "ResponseCode": "0",
    "ResponseDescription": "Success. Request accepted for processing",
    "CustomerMessage": "Success. Request accepted for processing",
})


def test_stk_push_payload(client, session):
    session.add("/stkpush/", STK_OK)
    res = client.stk_push("0712 345 678", "100.4", "TENANT-A12-OCTOBER", "Rent for October 2026")

    assert res.is_accepted
    assert res.checkout_request_id == "ws_CO_191220191020363925"

    call = session.posts()[0]
    assert call["url"].endswith("/mpesa/stkpush/v1/processrequest")
    assert call["headers"]["Authorization"] == "Bearer tok-123"
    body = call["json"]
    assert body["BusinessShortCode"] == "174379"
    assert body["TransactionType"] == "CustomerPayBillOnline"
    assert body["Amount"] == 100
    assert body["PartyA"] == body["PhoneNumber"] == "254712345678"
    assert body["PartyB"] == "174379"
    assert body["AccountReference"] == "TENANT-A12-O"  # cut to 12
    assert body["TransactionDesc"] == "Rent for Octo"  # cut to 13
    assert body["CallBackURL"] == "https://example.com/payments/stk/callback/"
    decoded = base64.b64decode(body["Password"]).decode()
    assert decoded == "174379" + client.config.passkey + body["Timestamp"]


def test_stk_push_buy_goods(config, session):
    config.till_number = "5000123"
    session.add("/stkpush/", STK_OK)
    MpesaClient(config, session=session).stk_push("0712345678", 1, "X")
    body = session.posts()[0]["json"]
    assert body["TransactionType"] == "CustomerBuyGoodsOnline"
    assert body["PartyB"] == "5000123"
    assert body["BusinessShortCode"] == "174379"


def test_stk_push_requires_https_callback(client):
    with pytest.raises(MpesaValidationError, match="HTTPS"):
        client.stk_push("0712345678", 1, "X", callback_url="http://localhost/cb")


def test_stk_push_missing_passkey(config, session):
    config.passkey = None
    with pytest.raises(MpesaConfigError, match="passkey"):
        MpesaClient(config, session=session).stk_push("0712345678", 1, "X")


def test_stk_push_api_error(client, session):
    session.add("/stkpush/", FakeResponse(400, {
        "requestId": "1", "errorCode": "400.002.02", "errorMessage": "Bad Request - Invalid PhoneNumber"}))
    with pytest.raises(MpesaAPIError) as exc:
        client.stk_push("0712345678", 1, "X")
    assert exc.value.error_code == "400.002.02"
    assert exc.value.status_code == 400
    assert "Invalid PhoneNumber" in str(exc.value)


# ------------------------------------------------------------------ STK query


def test_stk_query_success(client, session):
    session.add("/stkpushquery/", FakeResponse(200, {
        "ResponseCode": "0", "MerchantRequestID": "m", "CheckoutRequestID": "ws_CO_1",
        "ResultCode": "0", "ResultDesc": "The service request is processed successfully."}))
    res = client.stk_query("ws_CO_1")
    assert res.is_successful and not res.is_pending
    assert session.posts()[0]["json"]["CheckoutRequestID"] == "ws_CO_1"


def test_stk_query_cancelled(client, session):
    session.add("/stkpushquery/", FakeResponse(200, {
        "ResponseCode": "0", "CheckoutRequestID": "ws_CO_1", "ResultCode": "1032",
        "ResultDesc": "Request cancelled by user"}))
    res = client.stk_query("ws_CO_1")
    assert res.result_code == 1032 and not res.is_successful


def test_stk_query_pending(client, session):
    session.add("/stkpushquery/", FakeResponse(500, {
        "requestId": "1", "errorCode": "500.001.1001", "errorMessage": "The transaction is being processed"}))
    res = client.stk_query("ws_CO_1")
    assert res.is_pending and res.result_code is None and not res.is_successful


# ------------------------------------------------------------------ C2B


def test_c2b_register(client, session):
    session.add("/c2b/v1/registerurl", FakeResponse(200, {"ResponseCode": "0", "ResponseDescription": "Success"}))
    client.c2b_register_urls("https://example.com/payments/c2b/confirm/", "https://example.com/payments/c2b/validate/")
    body = session.posts()[0]["json"]
    assert body == {
        "ShortCode": "174379",
        "ResponseType": "Completed",
        "ConfirmationURL": "https://example.com/payments/c2b/confirm/",
        "ValidationURL": "https://example.com/payments/c2b/validate/",
    }


def test_c2b_register_rejects_mpesa_in_url(client):
    with pytest.raises(MpesaValidationError, match="mpesa"):
        client.c2b_register_urls("https://example.com/mpesa/confirm/", "https://example.com/v/")


def test_c2b_simulate_sandbox_only(config, session):
    config.environment = "production"
    with pytest.raises(MpesaConfigError):
        MpesaClient(config, session=session).c2b_simulate("0712345678", 10, "A1")


def test_c2b_simulate(client, session):
    session.add("/c2b/v1/simulate", FakeResponse(200, {"ResponseCode": "0"}))
    client.c2b_simulate("0712345678", 10, "HOUSE-A1")
    body = session.posts()[0]["json"]
    assert body["Msisdn"] == "254712345678" and body["BillRefNumber"] == "HOUSE-A1"


# ------------------------------------------------------------------ B2C / status / balance / reversal


ACK = FakeResponse(200, {"ConversationID": "AG_1", "OriginatorConversationID": "1-1", "ResponseCode": "0",
                         "ResponseDescription": "Accept the service request successfully."})


def test_b2c(client, session):
    session.add("/b2c/", ACK)
    res = client.b2c_payment("0712345678", 500, remarks="Deposit refund", command_id="BusinessPayment")
    assert res["ConversationID"] == "AG_1"
    body = session.posts()[0]["json"]
    assert body["InitiatorName"] == "testapi"
    assert body["SecurityCredential"] == "ENCRYPTED=="
    assert body["PartyA"] == "174379" and body["PartyB"] == "254712345678"
    assert body["Amount"] == 500
    assert body["ResultURL"].endswith("/result/") and body["QueueTimeOutURL"].endswith("/timeout/")


def test_b2c_bad_command(client):
    with pytest.raises(MpesaValidationError):
        client.b2c_payment("0712345678", 1, command_id="Nope")


def test_b2c_missing_initiator(config, session):
    config.security_credential = None
    with pytest.raises(MpesaConfigError, match="security_credential"):
        MpesaClient(config, session=session).b2c_payment("0712345678", 1)


def test_transaction_status(client, session):
    session.add("/transactionstatus/", ACK)
    client.transaction_status("SJQ7ABCDEF")
    body = session.posts()[0]["json"]
    assert body["CommandID"] == "TransactionStatusQuery"
    assert body["TransactionID"] == "SJQ7ABCDEF"
    assert body["Initiator"] == "testapi"


def test_account_balance(client, session):
    session.add("/accountbalance/", ACK)
    client.account_balance()
    body = session.posts()[0]["json"]
    assert body["CommandID"] == "AccountBalance" and body["IdentifierType"] == "4"


def test_reversal(client, session):
    session.add("/reversal/", ACK)
    client.reversal("SJQ7ABCDEF", 100)
    body = session.posts()[0]["json"]
    assert body["CommandID"] == "TransactionReversal"
    assert body["ReceiverParty"] == "174379" and body["RecieverIdentifierType"] == "11"
    assert body["Amount"] == 100


# ------------------------------------------------------------------ config


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("MPESA_CONSUMER_KEY", "k")
    monkeypatch.setenv("MPESA_CONSUMER_SECRET", "s")
    monkeypatch.setenv("MPESA_ENVIRONMENT", "Production")
    monkeypatch.setenv("MPESA_SHORTCODE", "600000")
    monkeypatch.setenv("MPESA_TIMEOUT", "12")
    cfg = MpesaConfig.from_env()
    assert cfg.environment == "production" and cfg.shortcode == "600000" and cfg.timeout == 12.0
    assert cfg.api_url == "https://api.safaricom.co.ke"


def test_config_from_env_missing(monkeypatch):
    for k in ("MPESA_CONSUMER_KEY", "MPESA_CONSUMER_SECRET"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(MpesaConfigError):
        MpesaConfig.from_env()


def test_config_bad_environment():
    with pytest.raises(MpesaConfigError):
        MpesaConfig(consumer_key="k", consumer_secret="s", environment="live")


def test_client_from_kwargs():
    client = MpesaClient(consumer_key="k", consumer_secret="s", shortcode=174379)
    assert client.config.shortcode == "174379"

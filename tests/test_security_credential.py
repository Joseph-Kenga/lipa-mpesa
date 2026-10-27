"""Check the security credential round-trips using a throwaway self-signed certificate."""

import base64
import datetime

import pytest

pytest.importorskip("cryptography")

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID

from lipa_mpesa import MpesaConfigError, generate_security_credential


@pytest.fixture(scope="module")
def keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(1).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    return key, cert


def _decrypt(key, credential):
    return key.decrypt(base64.b64decode(credential), padding.PKCS1v15()).decode()


def test_pem_file(tmp_path, keypair):
    key, cert = keypair
    path = tmp_path / "cert.cer"
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    assert _decrypt(key, generate_security_credential("Safaricom999!", str(path))) == "Safaricom999!"


def test_der_bytes(keypair):
    key, cert = keypair
    der = cert.public_bytes(serialization.Encoding.DER)
    assert _decrypt(key, generate_security_credential("pw", der)) == "pw"


def test_pem_string(keypair):
    key, cert = keypair
    pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    assert _decrypt(key, generate_security_credential("pw", pem)) == "pw"


def test_bad_cert():
    with pytest.raises(MpesaConfigError):
        generate_security_credential("pw", b"not a cert")

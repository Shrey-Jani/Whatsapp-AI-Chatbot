import hashlib
import hmac

from app.config import settings
from app.whatsapp import verify_signature

settings.app_secret = "topsecret"
BODY = b'{"hello":"world"}'
GOOD = "sha256=" + hmac.new(b"topsecret", BODY, hashlib.sha256).hexdigest()


def test_accepts_valid_signature():
    assert verify_signature(BODY, GOOD)


def test_rejects_tampered_body():
    assert not verify_signature(b'{"hello":"evil"}', GOOD)


def test_rejects_missing_or_malformed_header():
    assert not verify_signature(BODY, None)
    assert not verify_signature(BODY, "garbage")

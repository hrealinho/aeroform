import base64
import hashlib
import hmac
import json
import time
from app.core.config import settings


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def create_state(athlete_id: int, ttl_s: int = 900) -> str:
    payload = {"athlete_id": athlete_id, "exp": int(time.time()) + ttl_s}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(settings.app_secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64(sig)}"


def verify_state(state: str) -> int:
    try:
        body, signature = state.split(".", 1)
        expected = hmac.new(settings.app_secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _unb64(signature)):
            raise ValueError("invalid signature")
        payload = json.loads(_unb64(body))
        if int(payload["exp"]) < int(time.time()):
            raise ValueError("expired state")
        return int(payload["athlete_id"])
    except Exception as exc:
        raise ValueError("Invalid OAuth state") from exc

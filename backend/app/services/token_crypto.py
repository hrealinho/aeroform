import base64
import hashlib
from cryptography.fernet import Fernet
from app.core.config import settings


def _fernet() -> Fernet:
    # Deterministic development key derived from APP_SECRET. In production APP_SECRET must be a
    # long, random secret supplied by the deployment environment.
    digest = hashlib.sha256(settings.app_secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_token(value: str | None) -> str | None:
    if not value:
        return None
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_token(value: str | None) -> str | None:
    if not value:
        return None
    return _fernet().decrypt(value.encode("ascii")).decode("utf-8")

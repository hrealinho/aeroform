from __future__ import annotations
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode
import httpx
from sqlalchemy.orm import Session
from app.core.config import settings
from app.domain.models import IntegrationConnection
from app.services.token_crypto import decrypt_token, encrypt_token


class StravaError(RuntimeError):
    pass


def authorization_url(state: str) -> str:
    if not settings.strava_client_id:
        raise StravaError("STRAVA_CLIENT_ID is not configured")
    query = urlencode({
        "client_id": settings.strava_client_id,
        "response_type": "code",
        "redirect_uri": settings.strava_redirect_uri,
        "approval_prompt": "auto",
        "scope": "read,activity:read_all",
        "state": state,
    })
    return f"{settings.strava_oauth_base}/authorize?{query}"


def exchange_code(code: str) -> dict:
    if not settings.strava_client_id or not settings.strava_client_secret:
        raise StravaError("Strava OAuth credentials are not configured")
    with httpx.Client(timeout=30) as client:
        response = client.post(f"{settings.strava_oauth_base}/token", data={
            "client_id": settings.strava_client_id,
            "client_secret": settings.strava_client_secret,
            "code": code,
            "grant_type": "authorization_code",
        })
    if response.is_error:
        raise StravaError(f"Strava token exchange failed: {response.status_code} {response.text[:400]}")
    return response.json()


def _refresh(db: Session, connection: IntegrationConnection) -> str:
    refresh_token = decrypt_token(connection.refresh_token_encrypted)
    if not refresh_token or not settings.strava_client_id or not settings.strava_client_secret:
        raise StravaError("Strava refresh credentials are unavailable")
    with httpx.Client(timeout=30) as client:
        response = client.post(f"{settings.strava_oauth_base}/token", data={
            "client_id": settings.strava_client_id,
            "client_secret": settings.strava_client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        })
    if response.is_error:
        connection.status = "error"
        connection.last_error = f"Token refresh failed: {response.status_code}"
        db.commit()
        raise StravaError(connection.last_error)
    data = response.json()
    connection.access_token_encrypted = encrypt_token(data["access_token"])
    connection.refresh_token_encrypted = encrypt_token(data.get("refresh_token") or refresh_token)
    connection.expires_at = datetime.fromtimestamp(int(data["expires_at"]), tz=timezone.utc)
    connection.status = "connected"
    connection.last_error = None
    db.commit()
    return data["access_token"]


def access_token(db: Session, connection: IntegrationConnection) -> str:
    # Refresh one hour before expiry, matching Strava's recommended behavior.
    now = datetime.now(timezone.utc)
    expiry = connection.expires_at
    if expiry and expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    token = decrypt_token(connection.access_token_encrypted)
    if not token or not expiry or expiry <= now + timedelta(hours=1):
        return _refresh(db, connection)
    return token


def api_get(db: Session, connection: IntegrationConnection, path: str, params: dict | None = None) -> tuple[dict | list, httpx.Headers]:
    token = access_token(db, connection)
    with httpx.Client(timeout=45) as client:
        response = client.get(f"{settings.strava_api_base}{path}", params=params, headers={"Authorization": f"Bearer {token}"})
        if response.status_code == 401:
            token = _refresh(db, connection)
            response = client.get(f"{settings.strava_api_base}{path}", params=params, headers={"Authorization": f"Bearer {token}"})
    if response.is_error:
        raise StravaError(f"Strava API failed: {response.status_code} {response.text[:400]}")
    return response.json(), response.headers


def revoke(db: Session, connection: IntegrationConnection) -> None:
    token = decrypt_token(connection.refresh_token_encrypted) or decrypt_token(connection.access_token_encrypted)
    if token and settings.strava_client_id and settings.strava_client_secret:
        with httpx.Client(timeout=30) as client:
            response = client.post(
                f"{settings.strava_oauth_base}/revoke",
                data={"token": token, "token_type_hint": "refresh_token"},
                auth=(str(settings.strava_client_id), settings.strava_client_secret),
            )
        if response.status_code not in {200, 404}:
            raise StravaError(f"Strava revoke failed: {response.status_code} {response.text[:300]}")
    connection.access_token_encrypted = None
    connection.refresh_token_encrypted = None
    connection.status = "disconnected"
    connection.last_error = None
    db.commit()

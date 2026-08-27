"""Create the single Strava webhook subscription for this application.

Usage:
  STRAVA_CLIENT_ID=... STRAVA_CLIENT_SECRET=... STRAVA_WEBHOOK_VERIFY_TOKEN=... \
    python scripts/create_strava_webhook.py https://your-api.example.com/api/v1/strava/webhook
"""
import sys
import httpx
from app.core.config import settings


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/create_strava_webhook.py <public_callback_url>")
    if not settings.strava_client_id or not settings.strava_client_secret:
        raise SystemExit("STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET are required")
    response = httpx.post(
        f"{settings.strava_api_base}/push_subscriptions",
        data={
            "client_id": settings.strava_client_id,
            "client_secret": settings.strava_client_secret,
            "callback_url": sys.argv[1],
            "verify_token": settings.strava_webhook_verify_token,
        },
        timeout=30,
    )
    print(response.status_code, response.text)
    response.raise_for_status()


if __name__ == "__main__":
    main()

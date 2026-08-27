from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Endurance AI"
    app_secret: str = "change-me-in-production"
    database_url: str = "sqlite:///./endurance_ai.db"
    storage_path: str = "./data/raw"
    redis_url: str = "redis://localhost:6379/0"
    async_tasks: bool = False

    strava_client_id: str | None = None
    strava_client_secret: str | None = None
    strava_redirect_uri: str = "http://localhost:8000/api/v1/strava/callback"
    strava_frontend_redirect_uri: str = "http://localhost:3000/imports?strava=connected"
    strava_api_base: str = "https://www.strava.com/api/v3"
    strava_oauth_base: str = "https://www.strava.com/oauth"
    strava_webhook_verify_token: str = "change-me"
    strava_sync_page_size: int = 100
    strava_sync_streams: bool = False

    metric_version: str = "v0.2"
    fitness_tau_days: float = 42.0
    fatigue_tau_days: float = 7.0
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()

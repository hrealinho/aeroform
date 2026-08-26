from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Endurance AI"
    database_url: str = "sqlite:///./endurance_ai.db"
    storage_path: str = "./data/raw"
    redis_url: str = "redis://localhost:6379/0"
    strava_client_id: str | None = None
    strava_client_secret: str | None = None
    strava_redirect_uri: str = "http://localhost:8000/api/v1/strava/callback"
    metric_version: str = "v0.1"
    fitness_tau_days: float = 42.0
    fatigue_tau_days: float = 7.0
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()

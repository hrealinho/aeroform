from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Aeroform"
    app_version: str = "0.8.3"
    app_secret: str = "change-me-in-production"
    database_url: str = "sqlite:///./endurance_ai.db"
    storage_path: str = "./data/raw"
    redis_url: str = "redis://localhost:6379/0"
    async_tasks: bool = False
    # Comma-separated in the environment, e.g. CORS_ORIGINS=http://localhost:3000,https://app.example
    cors_origins_raw: str = Field(default="http://localhost:3000", alias="CORS_ORIGINS")

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_origins_raw.split(",") if o.strip()]


    strava_client_id: str | None = None
    strava_client_secret: str | None = None
    strava_redirect_uri: str = "http://localhost:8000/api/v1/strava/callback"
    strava_frontend_redirect_uri: str = "http://localhost:3000/imports?strava=connected"
    strava_api_base: str = "https://www.strava.com/api/v3"
    strava_oauth_base: str = "https://www.strava.com/oauth"
    strava_webhook_verify_token: str = "change-me"
    strava_sync_page_size: int = 100
    strava_sync_streams: bool = False

    # v0.4 AI provider contract. The local provider is fully deterministic and
    # requires no secret. A remote provider can be connected later through a
    # small JSON contract without coupling planning rules to one model vendor.
    # local     - deterministic, no credentials, used in tests
    # anthropic - Claude refines the deterministic seed (set ANTHROPIC_API_KEY or AI_API_KEY)
    # openai    - an OpenAI model does the same (set OPENAI_API_KEY or AI_API_KEY, and AI_MODEL)
    # http_json  - vendor-neutral JSON gateway
    ai_provider: str = "local"
    ai_model: str = "claude-opus-5"
    ai_endpoint: str | None = None
    ai_api_key: str | None = None
    ai_timeout_s: float = 30.0

    metric_version: str = "v0.5"
    fitness_tau_days: float = 42.0
    fatigue_tau_days: float = 7.0
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)


settings = Settings()

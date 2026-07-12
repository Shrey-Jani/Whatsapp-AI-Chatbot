from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"  # override via GEMINI_MODEL when you bump versions

    # Supabase (Postgres + Storage)
    database_url: str = "postgresql+asyncpg://user:pass@host:5432/postgres"
    supabase_url: str = ""
    supabase_service_key: str = ""

    # WhatsApp — Meta Cloud API (app-level; per-firm token lives on the Tenant row)
    verify_token: str = "changeme"
    app_secret: str = ""
    graph_api_version: str = "v21.0"


settings = Settings()

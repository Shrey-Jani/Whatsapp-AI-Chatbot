from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM — swap providers with one env var; no code change needed.
    llm_provider: str = "gemini"            # gemini | groq
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"  # override via GEMINI_MODEL when you bump versions
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"

    # Supabase (Postgres + Storage)
    database_url: str = "postgresql+asyncpg://user:pass@host:5432/postgres"
    supabase_url: str = ""
    supabase_service_key: str = ""

    # WhatsApp — Meta Cloud API (app-level; per-firm token lives on the Tenant row)
    verify_token: str = "changeme"
    app_secret: str = ""
    graph_api_version: str = "v21.0"

    # Admin dashboard
    admin_password: str = "changeme"

    # Active filing year — slips are auto-matched against this.
    tax_year: int = 2025


settings = Settings()

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM — swap providers with one env var; no code change needed.
    llm_provider: str = "gemini"            # gemini | groq
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"  # override via GEMINI_MODEL when you bump versions
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"

    # Supabase — Postgres only (files go to local disk or R2, see storage_backend)
    database_url: str = "postgresql+asyncpg://user:pass@host:5432/postgres"

    # File storage: "local" (disk, needs a persistent volume) or "r2" (Cloudflare R2).
    storage_backend: str = "local"          # local | r2
    storage_dir: str = "uploaded_files"     # local backend: where slips are written
    r2_account_id: str = ""                 # r2 backend: from the Cloudflare dashboard
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket: str = "taxbot-docs"

    # WhatsApp — Meta Cloud API (app-level; per-firm token lives on the Tenant row)
    verify_token: str = "changeme"
    app_secret: str = ""
    graph_api_version: str = "v21.0"

    # Admin dashboard
    admin_password: str = "changeme"

    # Active filing year — slips are auto-matched against this.
    tax_year: int = 2025

    # SIN encryption at rest (Fernet key). Generate one with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    # Empty = SINs stored in plaintext (dev only). Back up this key — losing it loses the SINs.
    sin_encryption_key: str = ""


settings = Settings()

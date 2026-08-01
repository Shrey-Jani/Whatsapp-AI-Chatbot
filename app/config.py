from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM — swap providers with one env var; no code change needed.
    llm_provider: str = "gemini"            # gemini | groq
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"  # override via GEMINI_MODEL when you bump versions
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"

    # Supabase (Postgres only — files are stored on local disk, see storage_dir)
    database_url: str = "postgresql+asyncpg://user:pass@host:5432/postgres"

    # Uploaded slips are written here on the host's disk. Use an absolute path in prod
    # (a persistent volume). On ephemeral hosts the disk is wiped on restart.
    storage_dir: str = "uploaded_files"

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

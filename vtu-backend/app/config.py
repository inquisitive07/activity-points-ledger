from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Default targets the Docker database from the Mac (host port 5433). Inside Docker, .env overrides this with db:5432.
    database_url: str = "postgresql+psycopg://vtu:vtu_dev_password@localhost:5433/vtu_ledger"
    jwt_secret: str = "change-me-in-production"
    access_token_minutes: int = 480
    # Private local file storage for uploads (MVP). Object storage (S3-compatible) is a later phase.
    storage_dir: str = "./storage"
    max_upload_bytes: int = 10 * 1024 * 1024


settings = Settings()

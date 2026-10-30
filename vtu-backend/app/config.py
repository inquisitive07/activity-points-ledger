from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The Vite development server (npm run dev). Production deployments set CORS_ORIGINS to their own frontend origin.
DEV_CORS_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173"


def parse_origins(value: str) -> list[str]:
    """Comma-separated origins -> list. Every origin must be explicit http(s); "*" is refused."""
    origins = [o.strip().rstrip("/") for o in value.split(",") if o.strip()]
    for origin in origins:
        if "*" in origin or not origin.startswith(("http://", "https://")):
            raise ValueError(f"CORS origin {origin!r} must be an explicit http(s) origin, not a wildcard")
    return origins


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Default targets the Docker database from the Mac (host port 5433). Inside Docker, .env overrides this with db:5432.
    database_url: str = "postgresql+psycopg://vtu:vtu_dev_password@localhost:5433/vtu_ledger"
    jwt_secret: str = "change-me-in-production"
    access_token_minutes: int = 480
    # Private local file storage for uploads (MVP). Object storage (S3-compatible) is a later phase.
    storage_dir: str = "./storage"
    max_upload_bytes: int = 10 * 1024 * 1024
    # Browser origins allowed to call the API: comma-separated in the environment, e.g.
    # CORS_ORIGINS=https://ledger.example.edu. "*" is refused: only listed origins are allowed.
    cors_origins: str = DEV_CORS_ORIGINS
    # A QUEUED or PROCESSING OCR batch with no result after this many minutes may be marked FAILED by the Dean
    # (POST /ocr/batches/{id}/mark-failed), for example after the API process was restarted mid-run.
    ocr_stale_minutes: int = 30

    @field_validator("cors_origins")
    @classmethod
    def explicit_origins(cls, v):
        parse_origins(v)
        return v

    @field_validator("ocr_stale_minutes")
    @classmethod
    def positive_minutes(cls, v):
        if v < 1:
            raise ValueError("OCR_STALE_MINUTES must be at least 1")
        return v

    @property
    def cors_origin_list(self) -> list[str]:
        return parse_origins(self.cors_origins)


settings = Settings()

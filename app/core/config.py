from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    app_name: str = "CAEPI API"
    log_level: str = "INFO"
    api_prefix: str = "/api/v1"
    database_url: str = "postgresql+psycopg://caepi:caepi@localhost:5432/caepi"
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"
    import_api_key: str | None = None  # Compatibilidade para ferramentas locais legadas.

    admin_username: str | None = None
    admin_password: str | None = None
    admin_session_secret: str | None = None
    admin_cookie_secure: bool = False

    storage_backend: Literal["seaweedfs", "r2"] = "seaweedfs"
    s3_endpoint_url: str = "http://localhost:8333"
    s3_access_key_id: str = "caepi-local"
    s3_secret_access_key: str = "change-this-local-secret"
    s3_bucket: str = "caepi-documents"
    s3_region: str = "us-east-1"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()

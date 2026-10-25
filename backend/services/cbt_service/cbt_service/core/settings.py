from pydantic_settings import BaseSettings
from typing import Literal

from pydantic import ConfigDict


class Settings(BaseSettings):
    model_config = ConfigDict(env_file=".env", env_file_encoding="utf-8")

    ENVIRONMENT: str = "dev"

    # Database
    DATABASE_URL: str = "" #  "postgresql://admin:admin@localhost:5432/item_bank" # 

    POSTGRES_SERVER: str | None = None
    POSTGRES_PORT: int | None = None
    POSTGRES_DB: str | None = None
    POSTGRES_USER: str | None = None
    POSTGRES_PASSWORD: str | None = None

    # Auth Secret Key
    SECRET_KEY: str
    JWT_SECRET: str
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    JWT_ALGORITHM: str
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    AUTH_SERVICE_URL: str = "http://localhost:9000/api/v1"
    INTERNAL_SECRET: str | None = None  # must match auth service


    OTP_SECRET: str
    
    # Redis
    REDIS_URL: str | None = None

    # ------------------------------------------------------------------
    # Uploaded media (images in question / option rich text)
    # See MEDIA_STORAGE.md. "local" = folder on this server, "spaces" = DO Spaces.
    # ------------------------------------------------------------------
    STORAGE_BACKEND: Literal["local", "spaces"] = "local"
    MEDIA_MAX_IMAGE_MB: int = 5
    # Public URL prefix embedded in question HTML. Prefer a domain you own
    # (https://media.example.com) so storage can change without breaking links.
    # Defaults: local -> http://localhost:8000{MEDIA_URL_PATH};
    #           spaces -> https://{SPACES_BUCKET}.{SPACES_REGION}.cdn.digitaloceanspaces.com
    MEDIA_PUBLIC_BASE_URL: str | None = None

    # local backend
    MEDIA_ROOT: str = "media"        # folder files are written to
    MEDIA_URL_PATH: str = "/media"   # where this app serves MEDIA_ROOT
    MEDIA_SERVE_LOCAL: bool = True   # set False when nginx serves MEDIA_ROOT instead

    # DigitalOcean Spaces backend
    SPACES_KEY: str | None = None
    SPACES_SECRET: str | None = None
    SPACES_BUCKET: str | None = None
    SPACES_REGION: str = "fra1"
    SPACES_ENDPOINT_URL: str | None = None  # default https://{SPACES_REGION}.digitaloceanspaces.com
    # REDIS_HOST: str = "localhost"
    # REDIS_PORT: int = 6379


settings = Settings()

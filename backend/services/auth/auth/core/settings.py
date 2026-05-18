from pydantic_settings import BaseSettings
from pydantic import ConfigDict

class Settings(BaseSettings):
    model_config = ConfigDict(env_file=".env", env_file_encoding="utf-8")

    POSTGRES_SERVER: str | None = None
    POSTGRES_PORT: int | None = None
    POSTGRES_DB: str | None = None
    POSTGRES_USER: str | None = None
    POSTGRES_PASSWORD: str | None = None
    DATABASE_URL: str | None = "postgresql://admin:admin@localhost:5432/ulrea_cbt"

    # Auth Secret Key
    SECRET_KEY: str
    JWT_SECRET: str
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    JWT_ALGORITHM: str
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7


    OTP_SECRET: str
    ENVIRONMENT: str = "dev"
    INTERNAL_SECRET: str
    
    # Redis
    REDIS_URL: str | None = None
    # REDIS_HOST: str = "localhost"
    # REDIS_PORT: int = 6379
    
    # Mail
    MAIL_USERNAME: str
    MAIL_PASSWORD: str
    MAIL_FROM: str
    MAIL_SERVER: str = "smtp.gmail.com"
    MAIL_PORT: int = 587

    FRONTEND_URL: str | None = "http://localhost:3000"

settings = Settings()  # raises immediately if any required field is missing
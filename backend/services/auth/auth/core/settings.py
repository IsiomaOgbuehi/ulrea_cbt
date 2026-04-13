from pydantic_settings import BaseSettings
from pydantic import ConfigDict

class Settings(BaseSettings):
    model_config = ConfigDict(env_file=".env", env_file_encoding="utf-8")

    POSTGRES_SERVER: str
    POSTGRES_PORT: str
    POSTGRES_DB: str
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str

    # Auth Secret Key
    SECRET_KEY: str
    JWT_SECRET: str
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    JWT_ALGORITHM: str
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7


    OTP_SECRET: str
    ENVIRONMENT: str = "dev"
    
    # Redis
    REDIS_URL: str
    # REDIS_HOST: str = "localhost"
    # REDIS_PORT: int = 6379
    
    # Mail
    MAIL_USERNAME: str
    MAIL_PASSWORD: str
    MAIL_FROM: str
    MAIL_SERVER: str = "smtp.gmail.com"
    MAIL_PORT: int = 587

settings = Settings()  # raises immediately if any required field is missing
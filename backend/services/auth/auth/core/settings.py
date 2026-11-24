from pydantic_settings import BaseSettings
from typing import Literal

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
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    JWT_ALGORITHM: str
    # Sessions slide: each refresh pushes the end out again, up to a hard limit.
    # Staff: signed out after REFRESH_TOKEN_EXPIRE_DAYS unused, SESSION_MAX_DAYS at most.
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    SESSION_MAX_DAYS: int = 30
    # Students often share computers: a much shorter idle limit.
    STUDENT_SESSION_IDLE_HOURS: int = 4
    STUDENT_SESSION_MAX_DAYS: int = 7


    OTP_SECRET: str
    ENVIRONMENT: str = "dev"
    INTERNAL_SECRET: str
    CBT_SERVICE_URL: str
    
    # Redis
    REDIS_URL: str | None = None
    # REDIS_HOST: str = "localhost"
    # REDIS_PORT: int = 6379
    
    # Mail. EMAIL_PROVIDER picks who sends: resend | zoho_cpaas | smtp.
    # Only the chosen provider's settings are needed.
    EMAIL_PROVIDER: Literal["resend", "zoho_cpaas", "smtp"] = "resend"
    MAIL_FROM: str                       # "Juwazi <no-reply@juwazi.com>" or Juwazi <noreply@ulrea.com.ng> or a bare address
    RESEND_API_KEY: str = ""
    # Zoho CPaaS (formerly ZeptoMail): the Send Mail token from the mail agent's
    # SMTP/API page. The URL depends on the account's data centre; copy it from
    # that page if it isn't this one.
    ZOHO_CPAAS_API_KEY: str = ""
    ZOHO_CPAAS_API_URL: str = "https://cpaas.zoho.com/v1.1/email"
    # SMTP (e.g. a school's own mail server). Port 465 = SSL; otherwise STARTTLS when MAIL_STARTTLS.
    MAIL_SERVER: str | None = ""
    MAIL_PORT: int = 587
    MAIL_USERNAME: str | None = ""
    MAIL_PASSWORD: str | None = ""
    MAIL_STARTTLS: bool = True


    FRONTEND_URL: str | None = "http://localhost:3000"
    PAYSTACK_SECRET_KEY: str = ""
    PAYSTACK_PUBLIC_KEY: str  = ""      # used by frontend

    # Payments: which provider takes payments. Switching is this one setting
    # (each provider's keys must be set). flutterwave | paystack
    PAYMENT_PROVIDER: str = "flutterwave"
    FLUTTERWAVE_SECRET_KEY: str = ""
    # The "secret hash" set on the Flutterwave dashboard's webhook settings;
    # Flutterwave sends it back in the verif-hash header.
    FLUTTERWAVE_WEBHOOK_HASH: str = ""
    FRONTEND_URL: str

settings = Settings()  # raises immediately if any required field is missing
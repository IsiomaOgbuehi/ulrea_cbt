from sqlmodel import Field
from pydantic import BaseModel, EmailStr
from auth.utility.otp.otp_enums import OtpPurpose

class OTPRequestSchema(BaseModel):
    identifier: EmailStr | str = Field(description="Email or phone")
    purpose: str # OtpPurpose # = Field(examples=["login", "forgot_password"])


class OTPVerifySchema(BaseModel):
    identifier: EmailStr | str
    purpose: str
    otp: str = Field(min_length=4, max_length=6)


class OTPResponse(BaseModel):
    message: str
    otp: str | None = None


class OTPVerifyResponse(BaseModel):
    message: str
    verified: bool
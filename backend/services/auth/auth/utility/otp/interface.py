from dataclasses import dataclass
from .otp_enums import OtpPurpose, OtpChannel
from datetime import datetime

@dataclass
class StoreOtp:
    otp_harsh: str
    purpose: OtpPurpose
    identifier: str
    channel: OtpChannel
    attempts: int = 0
    max_attempts: int = 5
    created_at: datetime
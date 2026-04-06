from dataclasses import dataclass
from .otp_enums import OtpPurpose, OtpChannel
import time

@dataclass
class StoreOtp:
    tenant_id: str
    otp_harsh: str | None
    purpose: OtpPurpose
    identifier: str
    channel: OtpChannel
    attempts: int = 0
    max_attempts: int = 5
    created_at: time = int(time.time())
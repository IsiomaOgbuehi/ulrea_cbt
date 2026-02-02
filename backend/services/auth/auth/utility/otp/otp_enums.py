from enum import Enum

class OtpPurpose(str, Enum):
    LOGIN = 'login'
    FORGOT_PASSWORD = 'forgot_password'
    SIGNUP = 'signup'
    CHANGE_PHONE = 'change_phone'
    CHANGE_PASSWORD = 'change_password'
    VERIFY_ACCOUNT = 'verify_account'

class OtpChannel(str, Enum):
    EMAIL = 'email'
    WHATSAPP = 'whatsapp'
    PHONE = 'phone'
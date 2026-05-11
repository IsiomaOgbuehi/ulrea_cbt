import os
from fastapi_mail import ConnectionConfig, FastMail, MessageSchema, MessageType
from pydantic import EmailStr
from auth.core.settings import settings

# SMTP Configuration (Load these from your .env)
conf = ConnectionConfig(
    MAIL_USERNAME = settings.MAIL_USERNAME,
    MAIL_PASSWORD = settings.MAIL_PASSWORD,
    MAIL_FROM = settings.MAIL_FROM,
    MAIL_PORT = settings.MAIL_PORT,
    MAIL_SERVER = settings.MAIL_SERVER,
    MAIL_STARTTLS = True,
    MAIL_SSL_TLS = False,
    USE_CREDENTIALS = True,
    VALIDATE_CERTS = True,
    SUPPRESS_SEND= settings.ENVIRONMENT in ("test", "dev"),
)

class EmailService:

    @staticmethod
    async def send_otp_email(email_to: str, otp: str):
        # A simple, clean HTML template
        html_content = f"""
        <html>
            <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.6;">
                <div style="max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #ddd; border-radius: 10px;">
                    <h2 style="color: #4A90E2; text-align: center;">Verification Code</h2>
                    <p>Hello,</p>
                    <p>Your one-time password (OTP) for secure access is:</p>
                    <div style="text-align: center; margin: 30px 0;">
                        <span style="font-size: 32px; font-weight: bold; letter-spacing: 5px; color: #000; background: #f4f4f4; padding: 10px 20px; border-radius: 5px;">
                            {otp}
                        </span>
                    </div>
                    <p>This code is valid for <strong>5 minutes</strong>. If you did not request this, please ignore this email.</p>
                    <hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;">
                    <p style="font-size: 12px; color: #888; text-align: center;">
                        This is an automated message, please do not reply.
                    </p>
                </div>
            </body>
        </html>
        """

        message = MessageSchema(
            subject="OTP Verification Code",
            recipients=[email_to],
            body=html_content,
            subtype=MessageType.html
        )

        fm = FastMail(conf)
        await fm.send_message(message)

    @staticmethod
    def mask_email(email: str) -> str:
        local, domain = email.split("@")
        return f"{local[0]}***@{domain}"  # j***@cbtech.com
    
    @staticmethod
    async def send_staff_welcome_email(email: str, firstname: str, temp_password: str):
        html_content = f"""
        <html>
            <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.6;">
                <div style="max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #ddd; border-radius: 10px;">
                    <h2 style="color: #4A90E2; text-align: center;">Welcome to the Platform</h2>
                    <p>Hello {firstname},</p>
                    <p>An account has been created for you. Use the temporary password below to log in for the first time:</p>
                    <div style="text-align: center; margin: 30px 0;">
                        <span style="font-size: 24px; font-weight: bold; letter-spacing: 3px; color: #000; background: #f4f4f4; padding: 10px 20px; border-radius: 5px;">
                            {temp_password}
                        </span>
                    </div>
                    <p><strong>You will be required to change this password on your first login.</strong></p>
                    <p>If you did not expect this email, please contact your administrator immediately.</p>
                    <hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;">
                    <p style="font-size: 12px; color: #888; text-align: center;">
                        This is an automated message, please do not reply.
                    </p>
                </div>
            </body>
        </html>
        """

        message = MessageSchema(
            subject="Your Account Has Been Created",
            recipients=[email],
            body=html_content,
            subtype=MessageType.html
        )

        fm = FastMail(conf)
        await fm.send_message(message)
        

    @staticmethod
    async def send_staff_activation_email(
        email: str,
        firstname: str,
        activation_link: str,
    ):
        html_content = f"""
        <html>
            <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.6;">
                <div style="max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #ddd; border-radius: 10px;">

                    <h2 style="color: #4A90E2; text-align: center;">
                        Activate Your Account
                    </h2>

                    <p>Hello {firstname},</p>

                    <p>
                        Your staff account has been created successfully.
                    </p>

                    <p>
                        Click the button below to activate your account and set your password:
                    </p>

                    <div style="text-align: center; margin: 30px 0;">
                        <a
                            href="{activation_link}"
                            style="
                                background-color: #4A90E2;
                                color: white;
                                padding: 14px 24px;
                                text-decoration: none;
                                border-radius: 6px;
                                font-weight: bold;
                                display: inline-block;
                            "
                        >
                            Activate Account
                        </a>
                    </div>

                    <p>
                        This activation link will expire in 24 hours.
                    </p>

                    <p>
                        If you did not expect this email, please ignore it.
                    </p>

                    <hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;">

                    <p style="font-size: 12px; color: #888; text-align: center;">
                        This is an automated message, please do not reply.
                    </p>

                </div>
            </body>
        </html>
        """

        message = MessageSchema(
            subject="Activate Your Account",
            recipients=[email],
            body=html_content,
            subtype=MessageType.html
        )

        fm = FastMail(conf)
        await fm.send_message(message)

    @staticmethod
    async def send_student_access_code_email(
        email: str,
        firstname: str,
        access_code: str,
    ):
        html_content = f"""
        <html>
            <body style="font-family: Arial, sans-serif;">
                <div style="max-width: 600px; margin: auto; padding: 20px; border: 1px solid #ddd; border-radius: 10px;">
                    
                    <h2 style="color: #4A90E2;">
                        Student Access Code
                    </h2>

                    <p>Hello {firstname},</p>

                    <p>
                        Your student account has been created.
                    </p>

                    <p>
                        Use the access code below to complete your first login setup:
                    </p>

                    <div style="text-align:center; margin:30px 0;">
                        <span style="
                            font-size:28px;
                            font-weight:bold;
                            letter-spacing:4px;
                            background:#f4f4f4;
                            padding:12px 20px;
                            border-radius:6px;
                        ">
                            {access_code}
                        </span>
                    </div>

                    <p>
                        You will be asked to set your security question during setup.
                    </p>

                    <hr>

                    <p style="font-size:12px;color:#888;">
                        This is an automated message.
                    </p>
                </div>
            </body>
        </html>
        """

        message = MessageSchema(
            subject="Your Student Access Code",
            recipients=[email],
            body=html_content,
            subtype=MessageType.html,
        )

        fm = FastMail(conf)
        await fm.send_message(message)
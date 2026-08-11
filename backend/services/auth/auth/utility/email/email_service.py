import os
# from fastapi_mail import ConnectionConfig, FastMail, MessageSchema, MessageType
# from pydantic import EmailStr
import resend
from auth.core.settings import settings

resend.api_key = settings.RESEND_API_KEY

SUPPRESS_SEND = settings.ENVIRONMENT in ("test", "dev")

class EmailService:

    @staticmethod
    async def _send_email(
        *,
        email_to: str,
        subject: str,
        html: str,
    ):
        if SUPPRESS_SEND:
            return
        
        params: resend.Emails.SendParams = {
            "from": settings.MAIL_FROM,
            "to": [email_to],
            "subject": subject,
            "html": html,
        }

        return await resend.Emails.send_async(params)

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

        await EmailService._send_email(
            email_to=email_to,
            subject="OTP Verification Code",
            html=html_content,
        )

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

        await EmailService._send_email(
            email_to=email,
            subject="Your Account Has Been Created",
            html=html_content,
        )
        

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

        await EmailService._send_email(
            email_to=email,
            subject="Activate Your Account",
            html=html_content,
        )

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

        await EmailService._send_email(
            email_to=email,
            subject="Your Student Access Code",
            html=html_content,
        )

    

    @staticmethod
    async def send_password_reset_email(email: str, firstname: str, reset_link: str):
        html_content = f"""
        <html>
            <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.6;">
                <div style="max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #ddd; border-radius: 10px;">

                    <h2 style="color: #4A90E2; text-align: center;">Reset Your Password</h2>

                    <p>Hello {firstname},</p>

                    <p>We received a request to reset your password. Click the button below to set a new one:</p>

                    <div style="text-align: center; margin: 30px 0;">
                        
                            href="{reset_link}"
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
                            Reset Password
                        </a>
                    </div>

                    <p>This link will expire in <strong>1 hour</strong>.</p>

                    <p>If you did not request a password reset, you can safely ignore this email.</p>

                    <hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;">
                    <p style="font-size: 12px; color: #888; text-align: center;">
                        This is an automated message, please do not reply.
                    </p>
                </div>
            </body>
        </html>
        """
        await EmailService._send_email(
            email_to=email,
            subject="Reset Your Password",
            html=html_content,
        )


    @staticmethod
    async def send_added_to_org_email(email: str, firstname: str, org_name: str, role: str):
        html_content = f"""
        <html>
            <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.6;">
                <div style="max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #ddd; border-radius: 10px;">

                    <h2 style="color: #4A90E2; text-align: center;">You've Been Added to an Organization</h2>

                    <p>Hello {firstname},</p>

                    <p>You have been added to <strong>{org_name}</strong> as <strong>{role}</strong>.</p>

                    <p>Log in to your existing account to access your new organization.</p>

                    <hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;">
                    <p style="font-size: 12px; color: #888; text-align: center;">
                        This is an automated message, please do not reply.
                    </p>
                </div>
            </body>
        </html>
        """
        
        await EmailService._send_email(
            email_to=email,
            subject=f"You've been added to {org_name}",
            html=html_content,
        )
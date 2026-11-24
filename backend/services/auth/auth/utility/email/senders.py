"""
Where emails go out: one sender per provider, picked by settings.EMAIL_PROVIDER.

- resend:    Resend's API (RESEND_API_KEY).
- zoho_cpaas: Zoho CPaaS email API, formerly ZeptoMail (ZOHO_CPAAS_API_KEY; ZOHO_CPAAS_API_URL for
             another region, as shown in the Zoho CPaaS dashboard).
- smtp:      any SMTP server (MAIL_SERVER, MAIL_PORT, MAIL_USERNAME,
             MAIL_PASSWORD): a school's own mail server on an intranet.

Every sender raises EmailSendError when the provider refuses the email, so
callers handle failures the same way whichever provider is used.
"""
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import parseaddr
from typing import Literal, Protocol

import httpx
import resend
from starlette.concurrency import run_in_threadpool

from auth.core.settings import settings

EmailProvider = Literal["resend", "zoho_cpaas", "smtp"]

TIMEOUT_SECONDS: float = 15.0


class EmailSendError(Exception):
    """The provider didn't accept the email."""


class EmailSender(Protocol):
    async def send(self, *, to: str, subject: str, html: str) -> None: ...


def sender_address() -> tuple[str, str]:
    """MAIL_FROM as (name, address); "Juwazi <no-reply@juwazi.com>" or a bare address."""
    name, address = parseaddr(settings.MAIL_FROM)
    if not address:
        raise EmailSendError("MAIL_FROM is not a valid email address.")
    return name, address


class ResendSender:
    def __init__(self, api_key: str) -> None:
        resend.api_key = api_key

    async def send(self, *, to: str, subject: str, html: str) -> None:
        params: resend.Emails.SendParams = {
            "from": settings.MAIL_FROM,
            "to": [to],
            "subject": subject,
            "html": html,
        }
        try:
            await resend.Emails.send_async(params)
        except Exception as e:
            raise EmailSendError(f"Resend refused the email: {e}") from e


class ZohoCpaasSender:
    def __init__(self, api_key: str, api_url: str) -> None:
        # The dashboard shows the key with or without its prefix; accept both.
        token: str = api_key.strip()
        self._authorization: str = token if token.startswith("Zoho-enczapikey") else f"Zoho-enczapikey {token}"
        self._url: str = api_url

    async def send(self, *, to: str, subject: str, html: str) -> None:
        name, address = sender_address()
        sender: dict[str, str] = {"address": address}
        if name:
            sender["name"] = name
        body: dict[str, object] = {
            "from": sender,
            "to": [{"email_address": {"address": to}}],
            "subject": subject,
            "htmlbody": html,
        }
        headers: dict[str, str] = {
            "Authorization": self._authorization,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
                response = await client.post(self._url, json=body, headers=headers)
        except httpx.HTTPError as e:
            raise EmailSendError(f"Couldn't reach Zoho CPaaS: {e!r}") from e
        if response.status_code >= 300:
            raise EmailSendError(f"Zoho CPaaS refused the email ({response.status_code}): {response.text[:500]}")


class SmtpSender:
    def __init__(self, host: str, port: int, username: str, password: str, starttls: bool) -> None:
        self._host: str = host
        self._port: int = port
        self._username: str = username
        self._password: str = password
        self._starttls: bool = starttls

    def _send_sync(self, message: EmailMessage) -> None:
        context: ssl.SSLContext = ssl.create_default_context()
        server: smtplib.SMTP
        if self._port == 465:
            server = smtplib.SMTP_SSL(self._host, self._port, timeout=TIMEOUT_SECONDS, context=context)
        else:
            server = smtplib.SMTP(self._host, self._port, timeout=TIMEOUT_SECONDS)
        with server:
            if self._port != 465 and self._starttls:
                server.starttls(context=context)
            if self._username:
                server.login(self._username, self._password)
            server.send_message(message)

    async def send(self, *, to: str, subject: str, html: str) -> None:
        message = EmailMessage()
        message["From"] = settings.MAIL_FROM
        message["To"] = to
        message["Subject"] = subject
        message.set_content("This email needs an email app that shows HTML.")
        message.add_alternative(html, subtype="html")
        try:
            await run_in_threadpool(self._send_sync, message)
        except (smtplib.SMTPException, OSError) as e:
            raise EmailSendError(f"The SMTP server refused the email: {e!r}") from e


def build_sender(provider: EmailProvider) -> EmailSender:
    if provider == "zoho_cpaas":
        if not settings.ZOHO_CPAAS_API_KEY:
            raise EmailSendError("EMAIL_PROVIDER is zoho_cpaas but ZOHO_CPAAS_API_KEY is not set.")
        return ZohoCpaasSender(settings.ZOHO_CPAAS_API_KEY, settings.ZOHO_CPAAS_API_URL)
    if provider == "smtp":
        if not settings.MAIL_SERVER:
            raise EmailSendError("EMAIL_PROVIDER is smtp but MAIL_SERVER is not set.")
        return SmtpSender(
            settings.MAIL_SERVER, settings.MAIL_PORT,
            settings.MAIL_USERNAME or "", settings.MAIL_PASSWORD or "", settings.MAIL_STARTTLS,
        )
    if not settings.RESEND_API_KEY:
        raise EmailSendError("EMAIL_PROVIDER is resend but RESEND_API_KEY is not set.")
    return ResendSender(settings.RESEND_API_KEY)


_sender: EmailSender | None = None


def get_sender() -> EmailSender:
    """The configured sender, built on first use."""
    global _sender
    if _sender is None:
        _sender = build_sender(settings.EMAIL_PROVIDER)
    return _sender

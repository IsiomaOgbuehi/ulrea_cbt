"""Email providers: the right one is picked, and each sends what its API expects."""
import json
from email.message import EmailMessage
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from auth.utility.email import senders
from auth.utility.email.senders import (
    EmailSendError, ResendSender, SmtpSender, ZohoCpaasSender, build_sender,
)

HTML = "<p>Hello</p>"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_build_sender_picks_the_provider_and_needs_its_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(senders.settings, "ZOHO_CPAAS_API_KEY", "")
    with pytest.raises(EmailSendError, match="ZOHO_CPAAS_API_KEY"):
        build_sender("zoho_cpaas")
    monkeypatch.setattr(senders.settings, "ZOHO_CPAAS_API_KEY", "abc")
    assert isinstance(build_sender("zoho_cpaas"), ZohoCpaasSender)

    monkeypatch.setattr(senders.settings, "MAIL_SERVER", "")
    with pytest.raises(EmailSendError, match="MAIL_SERVER"):
        build_sender("smtp")
    monkeypatch.setattr(senders.settings, "MAIL_SERVER", "mail.school.local")
    assert isinstance(build_sender("smtp"), SmtpSender)

    monkeypatch.setattr(senders.settings, "RESEND_API_KEY", "")
    with pytest.raises(EmailSendError, match="RESEND_API_KEY"):
        build_sender("resend")
    monkeypatch.setattr(senders.settings, "RESEND_API_KEY", "re_test")
    assert isinstance(build_sender("resend"), ResendSender)


@pytest.mark.anyio
async def test_zoho_cpaas_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(senders.settings, "MAIL_FROM", "Juwazi <no-reply@juwazi.com>")
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"message": "OK"})

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    with patch.object(senders.httpx, "AsyncClient", lambda **kw: real_client(transport=transport, **kw)):
        await ZohoCpaasSender("abc123", "https://cpaas.zoho.com/v1.1/email").send(to="ada@school.ng", subject="Hi", html=HTML)

    assert seen["url"] == "https://cpaas.zoho.com/v1.1/email"
    assert seen["auth"] == "Zoho-enczapikey abc123"
    assert seen["body"] == {
        "from": {"address": "no-reply@juwazi.com", "name": "Juwazi"},
        "to": [{"email_address": {"address": "ada@school.ng"}}],
        "subject": "Hi",
        "htmlbody": HTML,
    }


@pytest.mark.anyio
async def test_zoho_cpaas_refusal_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(senders.settings, "MAIL_FROM", "no-reply@juwazi.com")
    transport = httpx.MockTransport(lambda r: httpx.Response(400, json={"error": {"code": "TM_3004"}}))
    real_client = httpx.AsyncClient
    with patch.object(senders.httpx, "AsyncClient", lambda **kw: real_client(transport=transport, **kw)):
        with pytest.raises(EmailSendError, match="400"):
            await ZohoCpaasSender("Zoho-enczapikey abc", "https://x.test/email").send(to="a@b.ng", subject="Hi", html=HTML)


@pytest.mark.anyio
async def test_smtp_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(senders.settings, "MAIL_FROM", "Juwazi <no-reply@school.local>")
    sent: list[EmailMessage] = []
    sender = SmtpSender("mail.school.local", 587, "", "", True)
    with patch.object(SmtpSender, "_send_sync", lambda self, m: sent.append(m)):
        await sender.send(to="ada@school.local", subject="Hi", html=HTML)
    m = sent[0]
    assert m["To"] == "ada@school.local" and m["From"] == "Juwazi <no-reply@school.local>"
    html_part = m.get_body(preferencelist=("html",))
    assert html_part is not None and HTML in html_part.get_content()


@pytest.mark.anyio
async def test_resend_refusal_raises() -> None:
    with patch.object(senders.resend.Emails, "send_async", AsyncMock(side_effect=RuntimeError("bad key"))):
        with pytest.raises(EmailSendError, match="bad key"):
            await ResendSender("re_test").send(to="a@b.ng", subject="Hi", html=HTML)

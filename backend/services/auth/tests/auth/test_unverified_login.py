"""Signing in to an account that never finished verifying or activating."""
from typing import Any
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from .test_billing import _auth
from ..conftest import SIGNUP_PAYLOAD, USER_EMAIL, do_signup, get_super_admin_token

LOGIN = "/api/v1/auth/login"
OTP_REQUEST = "/api/v1/auth/otp/request"
OTP_VERIFY = "/api/v1/auth/otp/verify"
PASSWORD: str = SIGNUP_PAYLOAD["user"]["password"]


def _login(client: TestClient, email: str, password: str) -> Any:
    return client.post(LOGIN, data={"username": email, "password": password})


def test_owner_who_skipped_verification_verifies_at_sign_in(client: TestClient) -> None:
    do_signup(client)                                     # never entered the sign-up code
    r = _login(client, USER_EMAIL, PASSWORD)
    assert r.status_code == 403
    detail = r.json()["detail"]
    assert detail["code"] == "ACCOUNT_NOT_VERIFIED" and detail["identifier"] == USER_EMAIL

    # Signing in again straight away still points to the code (no 429).
    again = _login(client, USER_EMAIL, PASSWORD)
    assert again.status_code == 403 and again.json()["detail"]["code"] == "ACCOUNT_NOT_VERIFIED"

    otp = client.post(OTP_REQUEST, json={"identifier": USER_EMAIL, "purpose": "login_verification"}).json()["otp"]
    v = client.post(OTP_VERIFY, json={"identifier": USER_EMAIL, "purpose": "login_verification", "otp": otp})
    assert v.status_code == 200, v.json()
    assert v.json()["token"]["access_token"]               # signed in to their organisation

    ok = _login(client, USER_EMAIL, PASSWORD)
    assert ok.status_code == 200, ok.json()
    assert ok.json()["organization"]["name"] == SIGNUP_PAYLOAD["organization"]["name"]


def test_staff_who_never_activated_get_a_new_link(client: TestClient) -> None:
    token = get_super_admin_token(client)
    staff = client.post(
        "/api/v1/users/staff/create",
        json={"firstname": "Ada", "lastname": "Late", "email": "ada.late@cbtech.com", "phone": "+1000000099", "role": "teacher"},
        headers=_auth(token),
    ).json()
    sent = AsyncMock()
    with patch("auth.api.v1.routes.auth.EmailService.send_staff_activation_email", sent):
        r = _login(client, "ada.late@cbtech.com", staff["temporary_password"])
        assert r.status_code == 403, r.json()
        assert r.json()["detail"]["code"] == "ACTIVATION_REQUIRED"
        assert sent.await_count == 1
        assert "/activate-staff-account?token=" in sent.await_args.kwargs["activation_link"]
        _login(client, "ada.late@cbtech.com", staff["temporary_password"])
        assert sent.await_count == 1                       # at most one email a minute
    assert _login(client, "ada.late@cbtech.com", "wrong").status_code == 401

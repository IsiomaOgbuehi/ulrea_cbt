from tests.conftest import (
    do_signup, do_request_otp, do_verify_otp,
    do_full_signup, SIGNUP_PAYLOAD, USER_EMAIL
)


# ============================================================
# TESTS
# ============================================================

def test_signup(client):
    """Signup returns otp_required=True, otp_sent_to, and user is unverified."""
    response = client.post('/api/v1/auth/signup', json=SIGNUP_PAYLOAD)

    assert response.status_code == 200, response.json()
    data = response.json()

    assert data['organization']['name'] == "Test Org"
    assert data['user']['phone'] == "+1848234593"
    assert "id" in data['organization']
    assert "id" in data['user']
    assert data['otp_required'] is True
    assert data['otp_sent_to'] is not None
    assert data['user']['verified'] is False  # not verified yet


def test_signup_otp_verify_issues_token(client):
    """Full signup flow: signup → verify OTP → token issued."""
    signup_data = do_signup(client)

    assert signup_data['otp_required'] is True
    otp = signup_data.get('otp')
    assert otp is not None, "OTP should be in response in dev mode"

    verify_data = do_verify_otp(client, USER_EMAIL, otp)

    assert verify_data['verified'] is True
    assert 'token' in verify_data
    assert 'access_token' in verify_data['token']


def test_signup_otp_resend(client):
    """User can request a fresh OTP after signup."""
    do_signup(client)

    otp = do_request_otp(client, USER_EMAIL)
    verify_data = do_verify_otp(client, USER_EMAIL, otp)

    assert verify_data['verified'] is True


def test_cannot_verify_with_wrong_otp(client):
    """Wrong OTP returns 400."""
    do_signup(client)

    response = client.post(
        '/api/v1/auth/otp/verify',
        json={"identifier": USER_EMAIL, "purpose": "signup", "otp": "000000"}
    )
    assert response.status_code == 400


def test_cannot_login_before_otp_verified(client):
    """SUPER_ADMIN who hasn't verified OTP cannot log in."""
    """Unverified account is rejected at login with 403."""
    do_signup(client)  # account exists but OTP not verified

    response = client.post(
        '/api/v1/auth/login',
        data={'username': USER_EMAIL, 'password': 'chekicicici'}
    )
    assert response.status_code == 403, response.json()
    assert "verified" in response.json()['detail'].lower()


def test_login(client):
    """Verified account can log in and returns user and organization."""
    do_full_signup(client)

    response = client.post(
        '/api/v1/auth/login',
        data={'username': USER_EMAIL, 'password': 'chekicicici'}
    )
    assert response.status_code == 200, response.json()
    data = response.json()

    assert 'access_token' in data
    assert data['token_type'] == 'bearer'

    assert 'user' in data
    assert data['user']['email'] == USER_EMAIL
    assert data['user']['verified'] is True

    assert 'organization' in data
    assert data['organization']['name'] == SIGNUP_PAYLOAD['organization']['name']


def test_login_returns_refresh_token(client):
    """Login response includes both access and refresh tokens."""
    do_full_signup(client)

    response = client.post(
        '/api/v1/auth/login',
        data={'username': USER_EMAIL, 'password': 'chekicicici'}
    )
    assert response.status_code == 200, response.json()
    data = response.json()

    assert 'access_token' in data
    assert 'refresh_token' in data
    assert data['token_type'] == 'bearer'


def test_refresh_token_issues_new_access_token(client):
    """Valid refresh token returns a new access token."""
    do_full_signup(client)

    login_data = client.post(
        '/api/v1/auth/login',
        data={'username': USER_EMAIL, 'password': 'chekicicici'}
    ).json()

    refresh_token = login_data['refresh_token']

    response = client.post(
        '/api/v1/auth/token/refresh',
        json={"refresh_token": refresh_token}
    )
    assert response.status_code == 200, response.json()
    data = response.json()

    assert 'access_token' in data
    # new access token should differ from the original
    assert data['access_token'] != login_data['access_token']


def test_refresh_token_rejects_access_token(client):
    """Passing an access token to the refresh endpoint is rejected."""
    do_full_signup(client)

    login_data = client.post(
        '/api/v1/auth/login',
        data={'username': USER_EMAIL, 'password': 'chekicicici'}
    ).json()

    response = client.post(
        '/api/v1/auth/token/refresh',
        json={"refresh_token": login_data['access_token']}  # wrong token type
    )
    assert response.status_code == 401


def test_refresh_token_rejected_after_logout(client):
    """Refresh token is blacklisted on logout and cannot be reused."""
    do_full_signup(client)

    login_data = client.post(
        '/api/v1/auth/login',
        data={'username': USER_EMAIL, 'password': 'chekicicici'}
    ).json()

    access_token = login_data['access_token']
    refresh_token = login_data['refresh_token']

    # Logout — blacklists both tokens
    logout_response = client.post(
        '/api/v1/auth/logout',
        headers={"Authorization": f"Bearer {access_token}"},
        json={"refresh_token": refresh_token}
    )
    assert logout_response.status_code == 200

    # Try to use the refresh token after logout
    response = client.post(
        '/api/v1/auth/token/refresh',
        json={"refresh_token": refresh_token}
    )
    assert response.status_code == 401


def test_logout(client):
    """Verified account can log out successfully."""
    do_full_signup(client)

    login_data = client.post(
        '/api/v1/auth/login',
        data={'username': USER_EMAIL, 'password': 'chekicicici'}
    ).json()

    response = client.post(
        '/api/v1/auth/logout',
        headers={"Authorization": f"Bearer {login_data['access_token']}"},
        json={"refresh_token": login_data['refresh_token']}  # now required
    )
    assert response.status_code == 200
    assert response.json()['detail'] == 'Successfully logged out'
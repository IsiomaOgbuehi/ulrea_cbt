from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine
from sqlalchemy.orm import sessionmaker
from auth.main import app
from auth.database.database import database
import pytest
from sqlmodel import SQLModel
from sqlalchemy.pool import StaticPool
from unittest.mock import patch, AsyncMock, MagicMock

SQLALCHEMY_DATABASE_URL = 'sqlite:///:memory:'

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={'check_same_thread': False},
    poolclass=StaticPool
)

SIGNUP_PAYLOAD = {
    'organization': {
        'name': 'Test Org',
        'verified': False,
        'address': 'Test Address',
        'email': 'amycole@gmail.com',
        'phone': '+2348039361659',
        'organization_type': 'school'
    },
    'user': {
        'firstname': 'John',
        'lastname': 'Doe',
        'email': 'johndoe@cbtech.com',
        'phone': '+1848234593',
        'role': 'super_admin',
        'password': 'chekicicici',
        'confirm_password': 'chekicicici'
    }
}

USER_EMAIL = SIGNUP_PAYLOAD['user']['email']


# ============================================================
# FIXTURES
# ============================================================

def override_get_db():
    with Session(engine) as session:
        yield session


@pytest.fixture(scope="function")
def client():
    SQLModel.metadata.create_all(bind=engine)
    app.dependency_overrides[database.get_session] = override_get_db
    with TestClient(app) as c:
        yield c
    SQLModel.metadata.drop_all(bind=engine)
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def mock_email():
    with patch(
        "auth.utility.email.email_service.EmailService.send_otp_email",
        new_callable=AsyncMock
    ) as mock:
        yield mock


@pytest.fixture(autouse=True)
def mock_redis():
    store = {}

    async def fake_set(key, value, ex=None): store[key] = value
    async def fake_get(key): return store.get(key)
    async def fake_delete(key): store.pop(key, None)
    async def fake_incr(key):
        store[key] = store.get(key, 0) + 1
        return store[key]
    async def fake_decr(key):
        store[key] = store.get(key, 0) - 1
        return store[key]
    async def fake_expire(key, ttl): pass
    async def fake_ttl(key): return 300
    async def fake_ping(): return True
    async def fake_aclose(): pass

    mock = MagicMock()
    mock.set = fake_set
    mock.get = fake_get
    mock.delete = fake_delete
    mock.incr = fake_incr
    mock.decr = fake_decr
    mock.expire = fake_expire
    mock.ttl = fake_ttl
    mock.ping = fake_ping
    mock.aclose = fake_aclose

    with patch("auth.utility.otp.otp_service.redis_client", mock), \
         patch("auth.api.v1.routes.auth.redis_client", mock):
        yield store


# ============================================================
# HELPERS
# ============================================================

def do_signup(client, payload=None):
    response = client.post('/api/v1/auth/signup', json=payload or SIGNUP_PAYLOAD)
    assert response.status_code == 200, response.json()
    return response.json()


def do_request_otp(client, email):
    response = client.post(
        '/api/v1/auth/otp/request',
        json={"identifier": email, "purpose": "signup"}
    )
    assert response.status_code == 200, response.json()
    otp = response.json().get("otp")
    assert otp is not None, "OTP not returned — is ENVIRONMENT=dev?"
    return otp


def do_verify_otp(client, email, otp):
    response = client.post(
        '/api/v1/auth/otp/verify',
        json={"identifier": email, "purpose": "signup", "otp": otp}
    )
    assert response.status_code == 200, response.json()
    return response.json()


def do_full_signup(client, email=USER_EMAIL):
    """signup → extract OTP from dev response → verify OTP → returns token."""
    payload = {**SIGNUP_PAYLOAD, 'user': {**SIGNUP_PAYLOAD['user'], 'email': email}}
    signup_data = do_signup(client, payload)

    otp = signup_data.get('otp') or do_request_otp(client, email)
    return do_verify_otp(client, email, otp)


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
    """Unverified account is rejected at login with 403."""
    do_signup(client)  # account exists but OTP not verified

    response = client.post(
        '/api/v1/auth/login',
        data={'username': USER_EMAIL, 'password': 'chekicicici'}
    )
    assert response.status_code == 403, response.json()


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
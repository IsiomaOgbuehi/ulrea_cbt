import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from sqlmodel import Session, SQLModel
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from auth.main import app
from auth.database.database import database

# ============================================================
# DATABASE
# ============================================================

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

    with patch("auth.utility.redis.redis_client.redis_client", mock), \
     patch("auth.api.v1.routes.auth.redis_client", mock), \
     patch("auth.dependencies.auth_dependencies.redis_client", mock), \
     patch("auth.utility.email.email_service.EmailService.send_otp_email", new_callable=AsyncMock), \
     patch("auth.utility.email.email_service.EmailService.send_staff_welcome_email",new_callable=AsyncMock), \
     patch("auth.utility.otp.otp_service.redis_client", mock):
        yield store


# ============================================================
# HELPERS — importable by any test file
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


def get_super_admin_token(client) -> str:
    verify_data = do_full_signup(client)
    return verify_data['token']['access_token']
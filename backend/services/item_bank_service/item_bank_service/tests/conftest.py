import pytest
from unittest.mock import patch, MagicMock
from uuid import uuid4
from sqlmodel import Session, SQLModel
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from item_bank_service.main import app
from item_bank_service.database.database import database

# ============================================================
# TEST DATABASE
# ============================================================

SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

# ============================================================
# SHARED TEST DATA
# ============================================================

SUPER_ADMIN_ID = uuid4()
ADMIN_ID = uuid4()
TEACHER_ID = uuid4()
ORG_ID = uuid4()


def make_token_payload(user_id, org_id, role, verified=True):
    return {
        "sub": str(user_id),
        "org_id": str(org_id),
        "role": role,
        "verified": verified,
        "type": "access",
        "jti": str(uuid4()),
        "exp": 9999999999,
    }


SUPER_ADMIN_PAYLOAD = make_token_payload(SUPER_ADMIN_ID, ORG_ID, "super_admin")
ADMIN_PAYLOAD = make_token_payload(ADMIN_ID, ORG_ID, "admin")
TEACHER_PAYLOAD = make_token_payload(TEACHER_ID, ORG_ID, "teacher")


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

TEST_SECRET = "test-secret-key-that-is-long-enough-for-hmac-sha256"  # ← at least 32 bytes


def make_auth_header(payload: dict) -> dict:
    """
    Creates a mock Authorization header by patching JWT decode.
    No real token needed in tests.
    """
    import jwt
    token = jwt.encode(payload, TEST_SECRET, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def super_admin_headers():
    return make_auth_header(SUPER_ADMIN_PAYLOAD)


@pytest.fixture
def admin_headers():
    return make_auth_header(ADMIN_PAYLOAD)


@pytest.fixture
def teacher_headers():
    return make_auth_header(TEACHER_PAYLOAD)


@pytest.fixture(autouse=True)
def mock_jwt(monkeypatch):
    """
    Patch JWT decode in dependencies to use test secret.
    This means tests don't need a real JWT_SECRET env var.
    """
    import jwt as pyjwt
    original_decode = pyjwt.decode

    def fake_decode(token, key, algorithms, **kwargs):
        # Always decode with test secret in tests
        return original_decode(token, TEST_SECRET, algorithms=algorithms)

    monkeypatch.setattr("item_bank_service.dependencies.jwt.decode", fake_decode)


@pytest.fixture(autouse=True)
def mock_redis(monkeypatch):
    store = {}

    async def fake_set(key, value, ex=None): store[key] = value
    async def fake_get(key): return store.get(key)
    async def fake_delete(key): store.pop(key, None)

    mock = MagicMock()
    mock.set = fake_set
    mock.get = fake_get
    mock.delete = fake_delete

    monkeypatch.setattr("item_bank_service.core.redis.redis_client.redis_client", mock)
    monkeypatch.setattr("item_bank_service.api.v1.routes.subjects.redis_client", mock)
    yield store

@pytest.fixture(autouse=True)
def mock_auth_client(monkeypatch):
    """Prevent real HTTP calls to auth service in tests."""
    async def fake_get_users_bulk(self, user_ids):
        # Return a fake UserSummary for each requested id
        from item_bank_service.schemas.schemas import UserSummary
        return {
            str(uid): UserSummary(
                id=uid,
                firstname="Test",
                lastname="User",
                email=f"{uid}@test.com",
                role="teacher",
            )
            for uid in user_ids
        }

    monkeypatch.setattr(
        "item_bank_service.clients.auth_client.AuthClient.get_users_bulk",
        fake_get_users_bulk,
    )

# ============================================================
# HELPERS
# ============================================================

def create_subject(client, headers, name="Mathematics", description="Math subject"):
    response = client.post(
        "/api/v1/subjects",
        json={"name": name, "description": description},
        headers=headers,
    )
    assert response.status_code == 200, response.json()
    return response.json()


def assign_teacher(client, headers, subject_id, teacher_id):
    response = client.post(
        f"/api/v1/subjects/{subject_id}/assign",
        json={"user_ids": [str(teacher_id)]},
        headers=headers,
    )
    assert response.status_code == 200, response.json()
    return response.json()

import pytest
from unittest.mock import MagicMock
from uuid import uuid4
from sqlmodel import Session, SQLModel
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from exam_service.main import app
from exam_service.database.database import database

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

TEST_SECRET = "test-secret-key-that-is-long-enough-for-hmac-sha256"


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


def make_auth_header(payload: dict) -> dict:
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
    import jwt as pyjwt
    original_decode = pyjwt.decode

    def fake_decode(token, key, algorithms, **kwargs):
        return original_decode(token, TEST_SECRET, algorithms=algorithms)

    monkeypatch.setattr("exam_service.dependencies.jwt.decode", fake_decode)


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

    monkeypatch.setattr("exam_service.api.v1.routes.exams.redis_client", mock, raising=False)
    yield store


# ============================================================
# HELPERS
# ============================================================

def create_exam(client, headers, subject_id=None, **kwargs):
    from uuid import uuid4
    payload = {
        "title": kwargs.pop("title", "Math Final Exam"),
        "subject_id": str(subject_id or uuid4()),
        "duration_minutes": 60,
        **kwargs,
    }
    response = client.post("/api/v1/exams", json=payload, headers=headers)
    assert response.status_code == 200, response.json()
    return response.json()


def add_items(client, headers, exam_id, item_ids):
    response = client.post(
        f"/api/v1/exams/{exam_id}/items",
        json={"item_ids": [str(i) for i in item_ids]},
        headers=headers,
    )
    assert response.status_code == 200, response.json()
    return response.json()
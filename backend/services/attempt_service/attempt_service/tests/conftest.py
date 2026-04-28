import pytest
from unittest.mock import MagicMock
from uuid import uuid4
from sqlmodel import Session, SQLModel
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from attempt_service.main import app
from attempt_service.database.database import database

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

STUDENT_ID = uuid4()
TEACHER_ID = uuid4()
ADMIN_ID = uuid4()
ORG_ID = uuid4()
EXAM_ID = uuid4()
ASSIGNMENT_ID = uuid4()

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


STUDENT_PAYLOAD = make_token_payload(STUDENT_ID, ORG_ID, "student")
TEACHER_PAYLOAD = make_token_payload(TEACHER_ID, ORG_ID, "teacher")
ADMIN_PAYLOAD = make_token_payload(ADMIN_ID, ORG_ID, "admin")


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
def student_headers():
    return make_auth_header(STUDENT_PAYLOAD)


@pytest.fixture
def teacher_headers():
    return make_auth_header(TEACHER_PAYLOAD)


@pytest.fixture
def admin_headers():
    return make_auth_header(ADMIN_PAYLOAD)


@pytest.fixture(autouse=True)
def mock_jwt(monkeypatch):
    import jwt as pyjwt
    original_decode = pyjwt.decode

    def fake_decode(token, key, algorithms, **kwargs):
        return original_decode(token, TEST_SECRET, algorithms=algorithms)

    monkeypatch.setattr("attempt_service.dependencies.jwt.decode", fake_decode)


# ============================================================
# HELPERS
# ============================================================

def start_attempt(client, headers, exam_id=None, assignment_id=None):
    response = client.post(
        "/api/v1/attempts",
        json={
            "exam_id": str(exam_id or EXAM_ID),
            "assignment_id": str(assignment_id or ASSIGNMENT_ID),
        },
        headers=headers,
    )
    assert response.status_code == 200, response.json()
    return response.json()


def save_response(client, headers, attempt_id, item_id=None, answer=None):
    iid = str(item_id or uuid4())
    response = client.post(
        f"/api/v1/attempts/{attempt_id}/responses",
        json={
            "item_id": iid,
            "exam_item_id": iid,
            "answer": answer or ["A"],
            "time_spent_seconds": 30,
            "is_flagged": False,
        },
        headers=headers,
    )
    assert response.status_code == 200, response.json()
    return response.json()

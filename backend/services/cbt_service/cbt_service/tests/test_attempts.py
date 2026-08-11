import pytest
from uuid import uuid4
from sqlmodel import Session, SQLModel, select

from .conftest import (
    STUDENT_ID, EXAM_ID, ASSIGNMENT_ID, ORG_ID, engine, make_auth_header
)
from cbt_service.database.models.exam import ExamModel
from cbt_service.database.models.enums.exam_enum import ExamStatus

TEACHER_ID = uuid4()


@pytest.fixture(autouse=True)
def seed_attempt_exam(client):
    """
    EXAM_ID/ASSIGNMENT_ID in conftest are bare UUID constants with no
    backing DB row. start() now correctly requires the exam to exist,
    so seed it directly — depends on `client` to guarantee tables
    exist first (schema is created inside the client fixture).
    """
    with Session(engine) as session:
        existing = session.exec(
            select(ExamModel).where(ExamModel.id == EXAM_ID)
        ).first()
        if not existing:
            exam = ExamModel(
                id=EXAM_ID,
                org_id=ORG_ID,
                subject_id=uuid4(),
                created_by=TEACHER_ID,
                title="Seeded Attempt Test Exam",
                duration_minutes=60,
                status=ExamStatus.ACTIVE,
                max_attempts=1,
            )
            session.add(exam)
            session.commit()
    yield

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


def test_student_can_start_attempt(client, student_headers):
    attempt = start_attempt(client, student_headers)
    assert attempt["status"] == "started"
    assert attempt["attempt_number"] == 1
    assert attempt["exam_id"] == str(EXAM_ID)


# def test_cannot_start_duplicate_attempt(client, student_headers):
#     start_attempt(client, student_headers)

#     response = client.post(
#         "/api/v1/attempts",
#         json={"exam_id": str(EXAM_ID), "assignment_id": str(ASSIGNMENT_ID)},
#         headers=student_headers,
#     )
#     assert response.status_code == 400
#     assert "in progress" in response.json()["detail"].lower()

def test_starting_attempt_again_resumes_existing(client, student_headers):
    """
    POST /attempts while an attempt is already STARTED returns the
    same attempt (idempotent resume) instead of erroring — this
    supports refresh/reconnect mid-exam.
    """
    first = start_attempt(client, student_headers)

    response = client.post(
        "/api/v1/attempts",
        json={"exam_id": str(EXAM_ID), "assignment_id": str(ASSIGNMENT_ID)},
        headers=student_headers,
    )
    assert response.status_code == 200
    second = response.json()
    assert second["id"] == first["id"]
    assert second["status"] == "started"


def test_cannot_start_new_attempt_after_max_attempts_reached(client, student_headers):
    attempt = start_attempt(client, student_headers)
    client.post(f"/api/v1/attempts/{attempt['id']}/submit", headers=student_headers)

    response = client.post(
        "/api/v1/attempts",
        json={"exam_id": str(EXAM_ID), "assignment_id": str(ASSIGNMENT_ID)},
        headers=student_headers,
    )
    assert response.status_code == 400
    assert "maximum attempts" in response.json()["detail"].lower()


def test_student_can_save_response(client, student_headers):
    attempt = start_attempt(client, student_headers)
    item_id = uuid4()

    response = save_response(client, student_headers, attempt["id"], item_id, ["B"])
    assert response["answer"] == ["B"]
    assert response["attempt_id"] == attempt["id"]


def test_save_response_is_upsert(client, student_headers):
    """Saving the same item twice updates, not duplicates."""
    attempt = start_attempt(client, student_headers)
    item_id = uuid4()

    save_response(client, student_headers, attempt["id"], item_id, ["A"])
    save_response(client, student_headers, attempt["id"], item_id, ["B"])  # update

    responses = client.get(
        f"/api/v1/attempts/{attempt['id']}/responses",
        headers=student_headers,
    ).json()

    assert len(responses) == 1
    assert responses[0]["answer"] == ["B"]


def test_can_flag_response_for_review(client, student_headers):
    attempt = start_attempt(client, student_headers)
    item_id = uuid4()
    iid = str(item_id)

    response = client.post(
        f"/api/v1/attempts/{attempt['id']}/responses",
        json={
            "item_id": iid,
            "exam_item_id": iid,
            "answer": ["A"],
            "time_spent_seconds": 10,
            "is_flagged": True,     # student flagged for review
        },
        headers=student_headers,
    )
    assert response.status_code == 200
    assert response.json()["is_flagged"] is True


def test_cannot_save_response_to_submitted_attempt(client, student_headers):
    attempt = start_attempt(client, student_headers)

    # Submit
    client.post(
        f"/api/v1/attempts/{attempt['id']}/submit",
        headers=student_headers,
    )

    # Try to save after submission
    item_id = str(uuid4())
    response = client.post(
        f"/api/v1/attempts/{attempt['id']}/responses",
        json={
            "item_id": item_id,
            "exam_item_id": item_id,
            "answer": ["A"],
            "time_spent_seconds": 5,
            "is_flagged": False,
        },
        headers=student_headers,
    )
    assert response.status_code == 400


def test_submit_attempt(client, student_headers):
    attempt = start_attempt(client, student_headers)
    save_response(client, student_headers, attempt["id"])

    response = client.post(
        f"/api/v1/attempts/{attempt['id']}/submit",
        headers=student_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "submitted"
    assert data["submitted_at"] is not None


def test_student_cannot_view_other_students_attempt(client, student_headers):
    attempt = start_attempt(client, student_headers)

    other_payload = {
        "sub": str(uuid4()),        # different student
        "org_id": str(ORG_ID),
        "role": "student",
        "verified": True,
        "type": "access",
        "jti": str(uuid4()),
        "exp": 9999999999,
    }
    other_headers = make_auth_header(other_payload)

    response = client.get(
        f"/api/v1/attempts/{attempt['id']}",
        headers=other_headers,
    )
    assert response.status_code == 404


def test_teacher_can_manually_review_response(client, student_headers, teacher_headers):
    attempt = start_attempt(client, student_headers)
    resp = save_response(client, student_headers, attempt["id"])

    # Submit the attempt first
    client.post(f"/api/v1/attempts/{attempt['id']}/submit", headers=student_headers)

    review_response = client.post(
        f"/api/v1/attempts/responses/{resp['id']}/review",
        json={"response_id": resp["id"], "marks_awarded": 4.5, "review_notes": "Good answer."},
        headers=teacher_headers,
    )
    assert review_response.status_code == 200
    data = review_response.json()
    assert data["marks_awarded"] == 4.5
    assert data["is_correct"] is True


def test_student_cannot_manually_review(client, student_headers):
    attempt = start_attempt(client, student_headers)
    resp = save_response(client, student_headers, attempt["id"])

    response = client.post(
        f"/api/v1/attempts/responses/{resp['id']}/review",
        json={"response_id": resp["id"], "marks_awarded": 5.0},
        headers=student_headers,     # student trying to review
    )
    assert response.status_code == 403

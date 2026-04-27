# exam_service/tests/test_exams.py
import pytest
from uuid import uuid4
from .conftest import create_exam, add_items, make_auth_header, ORG_ID, TEST_SECRET


# ============================================================
# EXAM CRUD
# ============================================================

def test_teacher_can_create_exam(client, teacher_headers):
    exam = create_exam(client, teacher_headers)
    assert exam["status"] == "draft"
    assert exam["title"] == "Math Final Exam"
    assert exam["total_marks"] == 0.0


def test_teacher_only_sees_own_exams(client, teacher_headers, admin_headers):
    create_exam(client, teacher_headers, title="Teacher Exam")
    create_exam(client, admin_headers, title="Admin Exam")

    response = client.get("/api/v1/exams", headers=teacher_headers)
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["title"] == "Teacher Exam"


def test_admin_sees_all_exams(client, teacher_headers, admin_headers):
    create_exam(client, teacher_headers)
    create_exam(client, admin_headers)

    response = client.get("/api/v1/exams", headers=admin_headers)
    assert response.status_code == 200
    assert len(response.json()) == 2


def test_teacher_cannot_edit_other_teachers_exam(client, teacher_headers):
    from exam_service.tests.conftest import make_auth_header, ORG_ID
    other_teacher_payload = {
        "sub": str(uuid4()),
        "org_id": str(ORG_ID),
        "role": "teacher",
        "verified": True,
        "type": "access",
        "jti": str(uuid4()),
        "exp": 9999999999,
    }
    other_headers = make_auth_header(other_teacher_payload)
    exam = create_exam(client, teacher_headers)

    response = client.patch(
        f"/api/v1/exams/{exam['id']}",
        json={"title": "Hacked Title"},
        headers=other_headers,
    )
    assert response.status_code == 403


def test_cannot_edit_non_draft_exam(client, teacher_headers, admin_headers):
    exam = create_exam(client, teacher_headers)
    items = add_items(client, teacher_headers, exam["id"], [uuid4()])

    # Submit for approval
    client.post(f"/api/v1/exams/{exam['id']}/submit", headers=teacher_headers)

    # Try to edit
    response = client.patch(
        f"/api/v1/exams/{exam['id']}",
        json={"title": "New Title"},
        headers=teacher_headers,
    )
    assert response.status_code == 400


# ============================================================
# APPROVAL FLOW
# ============================================================

def test_full_approval_flow(client, teacher_headers, admin_headers):
    exam = create_exam(client, teacher_headers)

    # Can't submit without items
    response = client.post(f"/api/v1/exams/{exam['id']}/submit", headers=teacher_headers)
    assert response.status_code == 400
    assert "question" in response.json()["detail"].lower()

    # Add items then submit
    add_items(client, teacher_headers, exam["id"], [uuid4(), uuid4()])
    response = client.post(f"/api/v1/exams/{exam['id']}/submit", headers=teacher_headers)
    assert response.status_code == 200
    assert response.json()["status"] == "pending"

    # Admin approves
    response = client.post(
        f"/api/v1/exams/{exam['id']}/approval",
        json={"action": "approve"},
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    assert response.json()["approved_by"] is not None


def test_rejection_requires_reason(client, teacher_headers, admin_headers):
    exam = create_exam(client, teacher_headers)
    add_items(client, teacher_headers, exam["id"], [uuid4()])
    client.post(f"/api/v1/exams/{exam['id']}/submit", headers=teacher_headers)

    response = client.post(
        f"/api/v1/exams/{exam['id']}/approval",
        json={"action": "reject"},  # missing reason
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_rejected_exam_can_be_resubmitted(client, teacher_headers, admin_headers):
    exam = create_exam(client, teacher_headers)
    add_items(client, teacher_headers, exam["id"], [uuid4()])
    client.post(f"/api/v1/exams/{exam['id']}/submit", headers=teacher_headers)
    client.post(
        f"/api/v1/exams/{exam['id']}/approval",
        json={"action": "reject", "rejection_reason": "Needs more questions"},
        headers=admin_headers,
    )

    # Teacher fixes it and resubmits
    client.patch(f"/api/v1/exams/{exam['id']}", json={"title": "Fixed Exam"}, headers=teacher_headers)
    response = client.post(f"/api/v1/exams/{exam['id']}/submit", headers=teacher_headers)
    assert response.status_code == 200
    assert response.json()["status"] == "pending"


def test_teacher_cannot_approve(client, teacher_headers):
    exam = create_exam(client, teacher_headers)
    add_items(client, teacher_headers, exam["id"], [uuid4()])
    client.post(f"/api/v1/exams/{exam['id']}/submit", headers=teacher_headers)

    response = client.post(
        f"/api/v1/exams/{exam['id']}/approval",
        json={"action": "approve"},
        headers=teacher_headers,
    )
    assert response.status_code == 403


# ============================================================
# ITEMS
# ============================================================

def test_duplicate_items_not_added(client, teacher_headers):
    exam = create_exam(client, teacher_headers)
    item_id = uuid4()

    add_items(client, teacher_headers, exam["id"], [item_id])
    add_items(client, teacher_headers, exam["id"], [item_id])  # duplicate

    response = client.get(f"/api/v1/exams/{exam['id']}/items", headers=teacher_headers)
    assert len(response.json()) == 1


def test_remove_item_updates_total_marks(client, teacher_headers):
    exam = create_exam(client, teacher_headers)
    items = add_items(client, teacher_headers, exam["id"], [uuid4(), uuid4()])

    exam_after = client.get(f"/api/v1/exams/{exam['id']}", headers=teacher_headers).json()
    assert exam_after["total_marks"] == 2.0

    client.delete(f"/api/v1/exams/{exam['id']}/items/{items[0]['id']}", headers=teacher_headers)

    exam_after = client.get(f"/api/v1/exams/{exam['id']}", headers=teacher_headers).json()
    assert exam_after["total_marks"] == 1.0  # ← was asserting 1.0, which is correct


# ============================================================
# STUDENT ASSIGNMENT
# ============================================================

def test_students_can_only_be_assigned_to_approved_exam(client, teacher_headers, admin_headers):
    exam = create_exam(client, teacher_headers)

    response = client.post(
        f"/api/v1/exams/{exam['id']}/assign",
        json={"student_ids": [str(uuid4())]},
        headers=admin_headers,
    )
    assert response.status_code == 400


def test_assign_students_to_approved_exam(client, teacher_headers, admin_headers):
    exam = create_exam(client, teacher_headers)
    add_items(client, teacher_headers, exam["id"], [uuid4()])
    client.post(f"/api/v1/exams/{exam['id']}/submit", headers=teacher_headers)
    client.post(f"/api/v1/exams/{exam['id']}/approval", json={"action": "approve"}, headers=admin_headers)

    students = [str(uuid4()), str(uuid4()), str(uuid4())]
    response = client.post(
        f"/api/v1/exams/{exam['id']}/assign",
        json={"student_ids": students},
        headers=teacher_headers,
    )
    assert response.status_code == 200
    assert len(response.json()) == 3


# ============================================================
# AUDIT LOG
# ============================================================

def test_audit_log_records_lifecycle(client, teacher_headers, admin_headers):
    exam = create_exam(client, teacher_headers)
    add_items(client, teacher_headers, exam["id"], [uuid4()])
    client.post(f"/api/v1/exams/{exam['id']}/submit", headers=teacher_headers)
    client.post(f"/api/v1/exams/{exam['id']}/approval", json={"action": "approve"}, headers=admin_headers)

    response = client.get(f"/api/v1/exams/{exam['id']}/audit", headers=admin_headers)
    assert response.status_code == 200

    actions = [entry["action"] for entry in response.json()]
    assert "created" in actions
    assert "submitted_for_approval" in actions
    assert "approved" in actions


def test_teacher_cannot_view_audit_log(client, teacher_headers):
    exam = create_exam(client, teacher_headers)

    response = client.get(f"/api/v1/exams/{exam['id']}/audit", headers=teacher_headers)
    assert response.status_code == 403

import pytest
from .conftest import (
    create_subject, assign_teacher,
    SUPER_ADMIN_ID, ADMIN_ID, TEACHER_ID, ORG_ID, TEST_SECRET
)


def test_admin_can_create_subject(client, admin_headers):
    response = client.post(
        "/api/v1/subjects",
        json={"name": "Physics", "description": "Physics subject"},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.json()
    data = response.json()
    assert data["name"] == "Physics"
    assert data["status"] == "active"
    assert "id" in data


def test_teacher_cannot_create_subject(client, teacher_headers):
    response = client.post(
        "/api/v1/subjects",
        json={"name": "Physics"},
        headers=teacher_headers,
    )
    assert response.status_code == 403


def test_admin_can_list_all_subjects(client, admin_headers):
    create_subject(client, admin_headers, "Math")
    create_subject(client, admin_headers, "Physics")

    response = client.get("/api/v1/subjects", headers=admin_headers)
    assert response.status_code == 200
    assert len(response.json()) == 2


def test_teacher_only_sees_assigned_subjects(client, admin_headers, teacher_headers):
    math = create_subject(client, admin_headers, "Math")
    physics = create_subject(client, admin_headers, "Physics")

    # Assign teacher to math only
    assign_teacher(client, admin_headers, math["id"], TEACHER_ID)

    response = client.get("/api/v1/subjects", headers=teacher_headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["name"] == "Math"


def test_teacher_cannot_view_unassigned_subject(client, admin_headers, teacher_headers):
    subject = create_subject(client, admin_headers, "Chemistry")

    response = client.get(f"/api/v1/subjects/{subject['id']}", headers=teacher_headers)
    assert response.status_code == 403


def test_teacher_can_view_assigned_subject(client, admin_headers, teacher_headers):
    subject = create_subject(client, admin_headers, "Biology")
    assign_teacher(client, admin_headers, subject["id"], TEACHER_ID)

    response = client.get(f"/api/v1/subjects/{subject['id']}", headers=teacher_headers)
    assert response.status_code == 200
    assert response.json()["name"] == "Biology"


def test_admin_can_update_subject(client, admin_headers):
    subject = create_subject(client, admin_headers, "Old Name")

    response = client.patch(
        f"/api/v1/subjects/{subject['id']}",
        json={"name": "New Name", "description": "Updated"},
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert response.json()["name"] == "New Name"


def test_admin_can_archive_subject(client, admin_headers):
    subject = create_subject(client, admin_headers, "To Archive")

    response = client.delete(f"/api/v1/subjects/{subject['id']}", headers=admin_headers)
    assert response.status_code == 200
    assert response.json()["status"] == "archived"

    # Archived subject no longer appears in list
    list_response = client.get("/api/v1/subjects", headers=admin_headers)
    names = [s["name"] for s in list_response.json()]
    assert "To Archive" not in names


def test_admin_can_assign_multiple_staff(client, admin_headers):
    from uuid import uuid4
    subject = create_subject(client, admin_headers, "Assigned Subject")
    user1 = str(uuid4())
    user2 = str(uuid4())

    response = client.post(
        f"/api/v1/subjects/{subject['id']}/assign",
        json={"user_ids": [user1, user2]},
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert len(response.json()) == 2


def test_duplicate_assignment_is_idempotent(client, admin_headers):
    subject = create_subject(client, admin_headers, "Math")
    assign_teacher(client, admin_headers, subject["id"], TEACHER_ID)

    # Assign same teacher again — should not error or duplicate
    response = client.post(
        f"/api/v1/subjects/{subject['id']}/assign",
        json={"user_ids": [str(TEACHER_ID)]},
        headers=admin_headers,
    )
    assert response.status_code == 200

    # Verify only one assignment exists
    assignments_response = client.get(
        f"/api/v1/subjects/{subject['id']}/assignments",
        headers=admin_headers,
    )
    assert len(assignments_response.json()) == 1


def test_admin_can_unassign_staff(client, admin_headers, teacher_headers):
    subject = create_subject(client, admin_headers, "Math")
    assign_teacher(client, admin_headers, subject["id"], TEACHER_ID)

    # Unassign
    response = client.delete(
        f"/api/v1/subjects/{subject['id']}/assign/{TEACHER_ID}",
        headers=admin_headers,
    )
    assert response.status_code == 204

    # Teacher can no longer see the subject
    view_response = client.get(f"/api/v1/subjects/{subject['id']}", headers=teacher_headers)
    assert view_response.status_code == 403


def test_cannot_access_other_org_subject(client, admin_headers):
    from uuid import uuid4
    from unittest.mock import patch
    import jwt

    subject = create_subject(client, admin_headers, "Org A Subject")

    # Create a token for a different org
    other_org_payload = {
        "sub": str(uuid4()),
        "org_id": str(uuid4()),     # different org
        "role": "admin",
        "verified": True,
        "type": "access",           # ← this was missing
        "jti": str(uuid4()),
        "exp": 9999999999,
    }
    import jwt
    other_token = jwt.encode(other_org_payload, TEST_SECRET, algorithm="HS256")
    other_headers = {"Authorization": f"Bearer {other_token}"}

    response = client.get(f"/api/v1/subjects/{subject['id']}", headers=other_headers)
    assert response.status_code == 404  # not 403 — don't reveal existence

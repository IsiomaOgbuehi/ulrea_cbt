from uuid import UUID
import copy
from tests.conftest import do_full_signup, SIGNUP_PAYLOAD

# ============================================================
# PAYLOADS
# ============================================================

TEACHER_PAYLOAD = {
    "firstname": "Bob",
    "lastname": "Teacher",
    "email": "bob@cbtech.com",
    "phone": "+1000000002",
    "role": "teacher",
}

TEACHER_2_PAYLOAD = {
    "firstname": "Carol",
    "lastname": "Teacher",
    "email": "carol@cbtech.com",
    "phone": "+1000000099",
    "role": "teacher",
}

STUDENT_PAYLOAD = {
    "firstname": "Charlie",
    "lastname": "Student",
    "phone": "+1000000003",
    "institution_id": "STU/2024/001",
}

STUDENT_2_PAYLOAD = {
    "firstname": "Diana",
    "lastname": "Student",
    "phone": "+1000000004",
    "institution_id": "STU/2024/002",
}

COHORT_PAYLOAD = {
    "name": "JSS 1A",
    "description": "Junior Secondary School 1A",
}

COHORT_2_PAYLOAD = {
    "name": "JSS 1B",
    "description": "Junior Secondary School 1B",
}


# ============================================================
# HELPERS
# ============================================================

def get_super_admin_token(client) -> str:
    return do_full_signup(client)["token"]["access_token"]


def _activate_staff(client, user_id: str, password: str = "newSecurePass123!") -> dict:
    from auth.utility.jwt.token_activation import create_staff_activation_token
    activation_token = create_staff_activation_token(user_id)
    resp = client.post(
        "/api/v1/users/staff/activate",
        json={
            "token": activation_token,
            "password": password,
            "confirm_password": password,
        },
    )
    assert resp.status_code == 200, resp.json()
    return resp.json()


def _create_and_activate_teacher(client, super_token: str, payload: dict = TEACHER_PAYLOAD) -> tuple[str, str]:
    """Returns (teacher_id, teacher_token)."""
    resp = client.post(
        "/api/v1/users/staff/create",
        json=payload,
        headers={"Authorization": f"Bearer {super_token}"},
    )
    assert resp.status_code == 200, resp.json()
    teacher_id = resp.json()["id"]
    teacher_token = _activate_staff(client, teacher_id)["access_token"]
    return teacher_id, teacher_token


def _create_cohort(client, token: str, payload: dict = COHORT_PAYLOAD) -> dict:
    resp = client.post(
        "/api/v1/cohorts",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.json()
    return resp.json()


def _create_student(client, token: str, payload: dict = STUDENT_PAYLOAD) -> dict:
    resp = client.post(
        "/api/v1/users/students/create",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.json()
    access_code = resp.json()['access_code']

    # Student completes first login setup
    setup_response = client.post(
        "/api/v1/users/student/init",
        json={
            "access_code": access_code,
            "favorite_question": "What is your pet's name?",
            "favorite_answer": "Fluffy",
        }
    )
    assert setup_response.status_code == 200, setup_response.json()
    assert 'access_token' in setup_response.json()

    return resp.json()


def _assign_teacher(client, token: str, cohort_id: str, teacher_id: str) -> dict:
    resp = client.post(
        f"/api/v1/cohorts/{cohort_id}/teachers",
        json={"teacher_id": teacher_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.json()
    return resp.json()


def _add_students_to_cohort(client, token: str, cohort_id: str, student_ids: list[str]) -> dict:
    resp = client.post(
        f"/api/v1/cohorts/{cohort_id}/members",
        json={"student_ids": student_ids},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.json()
    return resp.json()

def _get_cohort_member_ids(client, token: str, cohort_id: str) -> list[str]:
    resp = client.get(
        f"/api/v1/cohorts/{cohort_id}/members",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200, resp.json()

    return [
        m["student_id"]
        for m in resp.json()["items"]
    ]


# ============================================================
# COHORT CRUD
# ============================================================

def test_admin_can_create_cohort(client):
    """Super admin creates a cohort — returns cohort with id and name."""
    token = get_super_admin_token(client)
    data = _create_cohort(client, token)

    assert data["name"] == COHORT_PAYLOAD["name"]
    assert "id" in data


def test_admin_can_list_cohorts(client):
    """Admin sees all cohorts in the org."""
    token = get_super_admin_token(client)
    _create_cohort(client, token, COHORT_PAYLOAD)
    _create_cohort(client, token, COHORT_2_PAYLOAD)

    resp = client.get("/api/v1/cohorts", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.json()
    names = [c["name"] for c in resp.json()]
    assert "JSS 1A" in names
    assert "JSS 1B" in names


def test_admin_can_filter_cohorts_by_status(client):
    """Status query param filters returned cohorts."""
    token = get_super_admin_token(client)
    _create_cohort(client, token)

    resp = client.get(
        "/api/v1/cohorts?status=active",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.json()
    for cohort in resp.json():
        assert cohort["status"] == "active"


def test_admin_can_get_cohort_by_id(client):
    token = get_super_admin_token(client)
    cohort = _create_cohort(client, token)
    cohort_id = cohort["id"]

    resp = client.get(
        f"/api/v1/cohorts/{cohort_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.json()
    assert resp.json()["id"] == cohort_id


def test_admin_can_update_cohort(client):
    token = get_super_admin_token(client)
    cohort_id = _create_cohort(client, token)["id"]

    resp = client.patch(
        f"/api/v1/cohorts/{cohort_id}",
        json={"name": "JSS 1A Updated"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.json()
    assert resp.json()["name"] == "JSS 1A Updated"


def test_admin_can_archive_cohort(client):
    token = get_super_admin_token(client)
    cohort_id = _create_cohort(client, token)["id"]

    resp = client.delete(
        f"/api/v1/cohorts/{cohort_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.json()
    assert resp.json()["status"] == "archived"


def test_admin_can_graduate_cohort(client):
    token = get_super_admin_token(client)
    cohort_id = _create_cohort(client, token)["id"]

    resp = client.post(
        f"/api/v1/cohorts/{cohort_id}/graduate",
        json={"graduation_date": "2025-06-01"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.json()
    assert resp.json()["status"] == "graduated"


def test_teacher_cannot_create_cohort(client):
    """Teachers do not have permission to create cohorts."""
    token = get_super_admin_token(client)
    _, teacher_token = _create_and_activate_teacher(client, token)

    resp = client.post(
        "/api/v1/cohorts",
        json=COHORT_PAYLOAD,
        headers={"Authorization": f"Bearer {teacher_token}"},
    )
    assert resp.status_code == 403, resp.json()


def test_teacher_cannot_list_all_cohorts(client):
    """LIST /cohorts is admin-only — teacher is rejected."""
    token = get_super_admin_token(client)
    _, teacher_token = _create_and_activate_teacher(client, token)

    resp = client.get("/api/v1/cohorts", headers={"Authorization": f"Bearer {teacher_token}"})
    assert resp.status_code == 403, resp.json()


# ============================================================
# COHORT MEMBERS
# ============================================================

def test_admin_can_add_students_to_cohort(client):
    token = get_super_admin_token(client)
    cohort_id = _create_cohort(client, token)["id"]
    student_id = _create_student(client, token)["id"]

    result = _add_students_to_cohort(client, token, cohort_id, [student_id])
    assert result["added"] == 1


def test_admin_can_view_cohort_members(client):
    token = get_super_admin_token(client)
    cohort_id = _create_cohort(client, token)["id"]
    student_id = _create_student(client, token)["id"]
    _add_students_to_cohort(client, token, cohort_id, [student_id])

    resp = client.get(
        f"/api/v1/cohorts/{cohort_id}/members",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200, resp.json()

    member_student_ids = [
        m["student_id"]
        for m in resp.json()["items"]
    ]

    assert student_id in member_student_ids


def test_admin_can_remove_student_from_cohort(client):
    token = get_super_admin_token(client)
    cohort_id = _create_cohort(client, token)["id"]
    student_id = _create_student(client, token)["id"]
    _add_students_to_cohort(client, token, cohort_id, [student_id])

    resp = client.delete(
        f"/api/v1/cohorts/{cohort_id}/members/{student_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 204

    members_resp = client.get(
        f"/api/v1/cohorts/{cohort_id}/members",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert members_resp.status_code == 200, members_resp.json()

    member_student_ids = [
        m["student_id"]
        for m in members_resp.json()["items"]
    ]

    assert student_id not in member_student_ids


def test_add_duplicate_student_to_cohort_is_idempotent(client):
    """Adding the same student twice should not error or double-count."""
    token = get_super_admin_token(client)
    cohort_id = _create_cohort(client, token)["id"]
    student_id = _create_student(client, token)["id"]

    _add_students_to_cohort(client, token, cohort_id, [student_id])
    result = _add_students_to_cohort(client, token, cohort_id, [student_id])

    # Should report 0 newly added (already a member)
    assert result["added"] == 0 or result.get("skipped", 0) >= 1


# ============================================================
# TEACHER-COHORT ASSIGNMENT
# ============================================================

def test_admin_can_assign_teacher_to_cohort(client):
    token = get_super_admin_token(client)
    cohort_id = _create_cohort(client, token)["id"]
    teacher_id, _ = _create_and_activate_teacher(client, token)

    data = _assign_teacher(client, token, cohort_id, teacher_id)

    assert data["teacher_id"] == teacher_id
    assert data["cohort_id"] == cohort_id
    assert "assigned_at" in data


def test_assigning_same_teacher_twice_returns_400(client):
    """Duplicate assignment is rejected."""
    token = get_super_admin_token(client)
    cohort_id = _create_cohort(client, token)["id"]
    teacher_id, _ = _create_and_activate_teacher(client, token)

    _assign_teacher(client, token, cohort_id, teacher_id)

    resp = client.post(
        f"/api/v1/cohorts/{cohort_id}/teachers",
        json={"teacher_id": teacher_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400, resp.json()


def test_admin_can_unassign_teacher_from_cohort(client):
    token = get_super_admin_token(client)
    cohort_id = _create_cohort(client, token)["id"]
    teacher_id, _ = _create_and_activate_teacher(client, token)
    _assign_teacher(client, token, cohort_id, teacher_id)

    resp = client.delete(
        f"/api/v1/cohorts/{cohort_id}/teachers/{teacher_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 204


def test_unassigning_nonexistent_teacher_returns_404(client):
    token = get_super_admin_token(client)
    cohort_id = _create_cohort(client, token)["id"]
    teacher_id, _ = _create_and_activate_teacher(client, token)

    # Never assigned — should 404
    resp = client.delete(
        f"/api/v1/cohorts/{cohort_id}/teachers/{teacher_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404, resp.json()


def test_admin_can_list_teachers_in_cohort(client):
    token = get_super_admin_token(client)
    cohort_id = _create_cohort(client, token)["id"]
    teacher_id_1, _ = _create_and_activate_teacher(client, token, TEACHER_PAYLOAD)
    teacher_id_2, _ = _create_and_activate_teacher(client, token, TEACHER_2_PAYLOAD)

    _assign_teacher(client, token, cohort_id, teacher_id_1)
    _assign_teacher(client, token, cohort_id, teacher_id_2)

    resp = client.get(
        f"/api/v1/cohorts/{cohort_id}/teachers",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.json()
    teacher_ids = [a["teacher_id"] for a in resp.json()]
    assert teacher_id_1 in teacher_ids
    assert teacher_id_2 in teacher_ids


def test_teacher_cannot_assign_teachers(client):
    """A teacher cannot assign other teachers to cohorts."""
    token = get_super_admin_token(client)
    cohort_id = _create_cohort(client, token)["id"]
    teacher_id_1, teacher_token = _create_and_activate_teacher(client, token, TEACHER_PAYLOAD)
    teacher_id_2, _ = _create_and_activate_teacher(client, token, TEACHER_2_PAYLOAD)

    resp = client.post(
        f"/api/v1/cohorts/{cohort_id}/teachers",
        json={"teacher_id": teacher_id_2},
        headers={"Authorization": f"Bearer {teacher_token}"},
    )
    assert resp.status_code == 403, resp.json()


# ============================================================
# TEACHER ROW-LEVEL ACCESS (require_cohort_access)
# ============================================================

def test_assigned_teacher_can_view_cohort_members(client):
    """Teacher assigned to a cohort can list its students."""
    token = get_super_admin_token(client)
    cohort_id = _create_cohort(client, token)["id"]
    student_id = _create_student(client, token)["id"]
    _add_students_to_cohort(client, token, cohort_id, [student_id])

    teacher_id, teacher_token = _create_and_activate_teacher(client, token)
    _assign_teacher(client, token, cohort_id, teacher_id)

    resp = client.get(
        f"/api/v1/cohorts/{cohort_id}/members",
        headers={"Authorization": f"Bearer {teacher_token}"},
    )

    assert resp.status_code == 200, resp.json()

    member_student_ids = [
        m["student_id"]
        for m in resp.json()["items"]
    ]

    assert student_id in member_student_ids


def test_unassigned_teacher_cannot_view_cohort_members(client):
    """Teacher NOT assigned to a cohort is rejected with 403."""
    token = get_super_admin_token(client)
    cohort_id = _create_cohort(client, token)["id"]
    _, teacher_token = _create_and_activate_teacher(client, token)

    # No assignment made
    resp = client.get(
        f"/api/v1/cohorts/{cohort_id}/members",
        headers={"Authorization": f"Bearer {teacher_token}"},
    )
    assert resp.status_code == 403, resp.json()


def test_assigned_teacher_can_see_co_teachers(client):
    """An assigned teacher can list other teachers in the same cohort."""
    token = get_super_admin_token(client)
    cohort_id = _create_cohort(client, token)["id"]
    teacher_id_1, teacher_token_1 = _create_and_activate_teacher(client, token, TEACHER_PAYLOAD)
    teacher_id_2, _ = _create_and_activate_teacher(client, token, TEACHER_2_PAYLOAD)

    _assign_teacher(client, token, cohort_id, teacher_id_1)
    _assign_teacher(client, token, cohort_id, teacher_id_2)

    resp = client.get(
        f"/api/v1/cohorts/{cohort_id}/teachers",
        headers={"Authorization": f"Bearer {teacher_token_1}"},
    )
    assert resp.status_code == 200, resp.json()
    teacher_ids = [a["teacher_id"] for a in resp.json()]
    assert teacher_id_2 in teacher_ids


def test_unassigned_teacher_cannot_see_cohort_teachers(client):
    """Teacher not in a cohort cannot see who is assigned to it."""
    token = get_super_admin_token(client)
    cohort_id = _create_cohort(client, token)["id"]
    _, teacher_token = _create_and_activate_teacher(client, token)

    resp = client.get(
        f"/api/v1/cohorts/{cohort_id}/teachers",
        headers={"Authorization": f"Bearer {teacher_token}"},
    )
    assert resp.status_code == 403, resp.json()


# ============================================================
# MY COHORTS
# ============================================================

def test_teacher_can_list_their_assigned_cohorts(client):
    """GET /my-cohorts returns only cohorts the teacher is assigned to."""
    token = get_super_admin_token(client)
    cohort_id_1 = _create_cohort(client, token, COHORT_PAYLOAD)["id"]
    cohort_id_2 = _create_cohort(client, token, COHORT_2_PAYLOAD)["id"]
    teacher_id, teacher_token = _create_and_activate_teacher(client, token)

    # Assign to only one cohort
    _assign_teacher(client, token, cohort_id_1, teacher_id)

    resp = client.get(
        "/api/v1/cohorts/my-cohorts",
        headers={"Authorization": f"Bearer {teacher_token}"},
    )
    assert resp.status_code == 200, resp.json()
    cohort_ids = [c["id"] for c in resp.json()]
    assert cohort_id_1 in cohort_ids
    assert cohort_id_2 not in cohort_ids


def test_teacher_my_cohorts_includes_student_count(client):
    """Each cohort in /my-cohorts includes an accurate student_count."""
    token = get_super_admin_token(client)
    cohort_id = _create_cohort(client, token)["id"]
    student_id_1 = _create_student(client, token, STUDENT_PAYLOAD)["id"]
    student_id_2 = _create_student(client, token, STUDENT_2_PAYLOAD)["id"]
    _add_students_to_cohort(client, token, cohort_id, [student_id_1, student_id_2])

    teacher_id, teacher_token = _create_and_activate_teacher(client, token)
    _assign_teacher(client, token, cohort_id, teacher_id)

    resp = client.get(
        "/api/v1/cohorts/my-cohorts",
        headers={"Authorization": f"Bearer {teacher_token}"},
    )
    assert resp.status_code == 200, resp.json()
    cohort = next(c for c in resp.json() if c["id"] == cohort_id)
    assert cohort["student_count"] == 2


def test_teacher_with_no_assignments_gets_empty_my_cohorts(client):
    token = get_super_admin_token(client)
    _create_cohort(client, token)  # exists but not assigned to anyone
    _, teacher_token = _create_and_activate_teacher(client, token)

    resp = client.get(
        "/api/v1/cohorts/my-cohorts",
        headers={"Authorization": f"Bearer {teacher_token}"},
    )
    assert resp.status_code == 200, resp.json()
    assert resp.json() == []


def test_teacher_with_no_assignments_gets_empty_list(client):
    token = get_super_admin_token(client)

    _create_cohort(client, token)

    _, teacher_token = _create_and_activate_teacher(client, token)

    resp = client.get(
        "/api/v1/cohorts/my-cohorts",
        headers={"Authorization": f"Bearer {teacher_token}"},
    )

    assert resp.status_code == 200
    assert resp.json() == []


# ============================================================
# UPDATE STUDENT
# ============================================================

def test_admin_can_update_student(client):
    token = get_super_admin_token(client)
    student_id = _create_student(client, token)["id"]

    resp = client.patch(
        f"/api/v1/users/{student_id}",
        json={"firstname": "Charles", "lastname": "Studentson"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.json()
    data = resp.json()
    assert data["firstname"] == "Charles"
    assert data["lastname"] == "Studentson"


def test_update_student_partial_payload_only_changes_given_fields(client):
    token = get_super_admin_token(client)
    student_id = _create_student(client, token)["id"]

    resp = client.patch(
        f"/api/v1/users/{student_id}",
        json={"lastname": "NewLastName"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.json()
    data = resp.json()
    assert data["lastname"] == "NewLastName"
    assert data["firstname"] == STUDENT_PAYLOAD["firstname"]


def test_update_student_nonexistent_id_returns_404(client):
    token = get_super_admin_token(client)
    fake_id = "00000000-0000-0000-0000-000000000000"

    resp = client.patch(
        f"/api/v1/users/{fake_id}",
        json={"firstname": "Nobody"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404, resp.json()


def test_teacher_cannot_update_student(client):
    token = get_super_admin_token(client)
    student_id = _create_student(client, token)["id"]
    _, teacher_token = _create_and_activate_teacher(client, token)

    resp = client.patch(
        f"/api/v1/users/{student_id}",
        json={"firstname": "ShouldFail"},
        headers={"Authorization": f"Bearer {teacher_token}"},
    )
    assert resp.status_code == 403, resp.json()


def test_update_staff_id_via_student_endpoint_returns_404(client):
    """A staff (non-student) user_id passed here should not be editable via this endpoint."""
    token = get_super_admin_token(client)
    teacher_id, _ = _create_and_activate_teacher(client, token)

    resp = client.patch(
        f"/api/v1/users/{teacher_id}",
        json={"firstname": "ShouldFail"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404, resp.json()

    
# ============================================================
# MOVE STUDENT COHORT
# ============================================================

def test_admin_can_move_student_to_new_cohort(client):
    token = get_super_admin_token(client)
    cohort_1_id = _create_cohort(client, token, COHORT_PAYLOAD)["id"]
    cohort_2_id = _create_cohort(client, token, COHORT_2_PAYLOAD)["id"]
    student_id = _create_student(client, token)["id"]
    _add_students_to_cohort(client, token, cohort_1_id, [student_id])

    resp = client.put(
        f"/api/v1/users/{student_id}/cohort",
        json={"cohort_id": cohort_2_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.json()

def test_move_student_cohort_removes_from_old_cohort(client):
    token = get_super_admin_token(client)
    cohort_1_id = _create_cohort(client, token, COHORT_PAYLOAD)["id"]
    cohort_2_id = _create_cohort(client, token, COHORT_2_PAYLOAD)["id"]
    student_id = _create_student(client, token)["id"]
    _add_students_to_cohort(client, token, cohort_1_id, [student_id])

    client.put(
        f"/api/v1/users/{student_id}/cohort",
        json={"cohort_id": cohort_2_id},
        headers={"Authorization": f"Bearer {token}"},
    )

    old_members = _get_cohort_member_ids(client, token, cohort_1_id)
    new_members = _get_cohort_member_ids(client, token, cohort_2_id)
    assert student_id not in old_members
    assert student_id in new_members

def test_move_student_to_nonexistent_cohort_returns_404(client):
    token = get_super_admin_token(client)
    student_id = _create_student(client, token)["id"]
    fake_cohort_id = "00000000-0000-0000-0000-000000000000"

    resp = client.put(
        f"/api/v1/users/{student_id}/cohort",
        json={"cohort_id": fake_cohort_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404, resp.json()


def test_move_nonexistent_student_returns_404(client):
    token = get_super_admin_token(client)
    cohort_id = _create_cohort(client, token)["id"]
    fake_student_id = "00000000-0000-0000-0000-000000000000"

    resp = client.put(
        f"/api/v1/users/{fake_student_id}/cohort",
        json={"cohort_id": cohort_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404, resp.json()

def test_move_student_to_graduated_cohort_returns_400(client):
    token = get_super_admin_token(client)
    cohort_1_id = _create_cohort(client, token, COHORT_PAYLOAD)["id"]
    cohort_2_id = _create_cohort(client, token, COHORT_2_PAYLOAD)["id"]
    student_id = _create_student(client, token)["id"]
    _add_students_to_cohort(client, token, cohort_1_id, [student_id])

    graduate_resp = client.post(
        f"/api/v1/cohorts/{cohort_2_id}/graduate",
        json={"graduation_date": "2025-06-01"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert graduate_resp.status_code == 200, graduate_resp.json()

    resp = client.put(
        f"/api/v1/users/{student_id}/cohort",
        json={"cohort_id": cohort_2_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400, resp.json()


def test_teacher_cannot_move_student_cohort(client):
    token = get_super_admin_token(client)
    cohort_id = _create_cohort(client, token)["id"]
    student_id = _create_student(client, token)["id"]
    _, teacher_token = _create_and_activate_teacher(client, token)

    resp = client.put(
        f"/api/v1/users/{student_id}/cohort",
        json={"cohort_id": cohort_id},
        headers={"Authorization": f"Bearer {teacher_token}"},
    )
    assert resp.status_code == 403, resp.json()


# ============================================================
# LIST STAFF
# ============================================================

def test_admin_can_list_staff(client):
    token = get_super_admin_token(client)
    _create_and_activate_teacher(client, token, TEACHER_PAYLOAD)
    _create_and_activate_teacher(client, token, TEACHER_2_PAYLOAD)

    resp = client.get(
        "/api/v1/users/staff",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.json()
    data = resp.json()
    assert data["total"] >= 2
    emails = [s["email"] for s in data["staff"]]
    assert TEACHER_PAYLOAD["email"] in emails
    assert TEACHER_2_PAYLOAD["email"] in emails


def test_list_staff_excludes_students(client):
    token = get_super_admin_token(client)
    _create_and_activate_teacher(client, token)
    _create_student(client, token)

    resp = client.get(
        "/api/v1/users/staff",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.json()
    for staff in resp.json()["staff"]:
        assert staff["role"] != "student"


def test_list_staff_filter_by_name(client):
    token = get_super_admin_token(client)
    _create_and_activate_teacher(client, token, TEACHER_PAYLOAD)
    _create_and_activate_teacher(client, token, TEACHER_2_PAYLOAD)

    resp = client.get(
        "/api/v1/users/staff?name=Carol",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.json()
    data = resp.json()
    names = [(s["firstname"], s["lastname"]) for s in data["staff"]]
    assert ("Carol", "Teacher") in names
    assert ("Bob", "Teacher") not in names


def test_list_staff_filter_by_cohort(client):
    token = get_super_admin_token(client)
    cohort_id = _create_cohort(client, token)["id"]
    teacher_id_1, _ = _create_and_activate_teacher(client, token, TEACHER_PAYLOAD)
    teacher_id_2, _ = _create_and_activate_teacher(client, token, TEACHER_2_PAYLOAD)
    _assign_teacher(client, token, cohort_id, teacher_id_1)

    resp = client.get(
        f"/api/v1/users/staff?cohort_id={cohort_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.json()
    ids = [s["id"] for s in resp.json()["staff"]]
    assert teacher_id_1 in ids
    assert teacher_id_2 not in ids


def test_list_staff_pagination(client):
    token = get_super_admin_token(client)
    _create_and_activate_teacher(client, token, TEACHER_PAYLOAD)
    _create_and_activate_teacher(client, token, TEACHER_2_PAYLOAD)

    resp = client.get(
        "/api/v1/users/staff?page=1&per_page=1",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.json()
    data = resp.json()
    assert data["per_page"] == 1
    assert len(data["staff"]) == 1
    assert data["total"] >= 2


def test_teacher_cannot_list_staff(client):
    token = get_super_admin_token(client)
    _, teacher_token = _create_and_activate_teacher(client, token)

    resp = client.get(
        "/api/v1/users/staff",
        headers={"Authorization": f"Bearer {teacher_token}"},
    )
    assert resp.status_code == 403, resp.json()



# ============================================================
# LIST STUDENTS
# ============================================================

def test_admin_can_list_students(client):
    token = get_super_admin_token(client)
    _create_student(client, token, STUDENT_PAYLOAD)
    _create_student(client, token, STUDENT_2_PAYLOAD)

    resp = client.get(
        "/api/v1/users/students",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.json()
    data = resp.json()
    assert data["total"] >= 2
    firstnames = [s["firstname"] for s in data["students"]]
    assert "Charlie" in firstnames
    assert "Diana" in firstnames


def test_list_students_filter_by_status(client):
    token = get_super_admin_token(client)
    # Fully activated (init completed) -> active membership
    _create_student(client, token, STUDENT_PAYLOAD)

    # Created but not yet completed first-login setup -> pending membership
    create_resp = client.post(
        "/api/v1/users/students/create",
        json=STUDENT_2_PAYLOAD,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert create_resp.status_code == 200, create_resp.json()

    resp = client.get(
        "/api/v1/users/students?status=active",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.json()
    for student in resp.json()["students"]:
        assert student["status"] == "active"
    firstnames = [s["firstname"] for s in resp.json()["students"]]
    assert "Charlie" in firstnames
    assert "Diana" not in firstnames


def test_list_students_filter_by_cohort(client):
    token = get_super_admin_token(client)
    cohort_id = _create_cohort(client, token)["id"]
    student_id_1 = _create_student(client, token, STUDENT_PAYLOAD)["id"]
    student_id_2 = _create_student(client, token, STUDENT_2_PAYLOAD)["id"]
    _add_students_to_cohort(client, token, cohort_id, [student_id_1])

    resp = client.get(
        f"/api/v1/users/students?cohort_id={cohort_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.json()
    ids = [s["id"] for s in resp.json()["students"]]
    assert student_id_1 in ids
    assert student_id_2 not in ids


def test_list_students_excludes_staff(client):
    token = get_super_admin_token(client)
    _create_and_activate_teacher(client, token)
    _create_student(client, token)

    resp = client.get(
        "/api/v1/users/students",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.json()
    for student in resp.json()["students"]:
        assert student["email"] != TEACHER_PAYLOAD["email"]


def test_list_students_pagination(client):
    token = get_super_admin_token(client)
    _create_student(client, token, STUDENT_PAYLOAD)
    _create_student(client, token, STUDENT_2_PAYLOAD)

    resp = client.get(
        "/api/v1/users/students?page=1&per_page=1",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.json()
    data = resp.json()
    assert data["per_page"] == 1
    assert len(data["students"]) == 1
    assert data["total"] >= 2


def test_teacher_cannot_list_students(client):
    token = get_super_admin_token(client)
    _create_student(client, token)
    _, teacher_token = _create_and_activate_teacher(client, token)

    resp = client.get(
        "/api/v1/users/students",
        headers={"Authorization": f"Bearer {teacher_token}"},
    )
    assert resp.status_code == 403, resp.json()


# ============================================================
# STUDENT SEARCH BY NAME
# ============================================================

def test_search_students_by_firstname(client):
    token = get_super_admin_token(client)
    _create_student(client, token, STUDENT_PAYLOAD)
    _create_student(client, token, STUDENT_2_PAYLOAD)

    resp = client.get(
        "/api/v1/users/students?name=Diana",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.json()
    firstnames = [s["firstname"] for s in resp.json()["students"]]
    assert "Diana" in firstnames
    assert "Charlie" not in firstnames


def test_search_staff_by_lastname(client):
    """Both teachers share lastname 'Teacher', so a lastname search returns both."""
    token = get_super_admin_token(client)
    _create_and_activate_teacher(client, token, TEACHER_PAYLOAD)
    _create_and_activate_teacher(client, token, TEACHER_2_PAYLOAD)

    resp = client.get(
        "/api/v1/users/staff?name=Teacher",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.json()
    names = [(s["firstname"], s["lastname"]) for s in resp.json()["staff"]]
    assert ("Bob", "Teacher") in names
    assert ("Carol", "Teacher") in names
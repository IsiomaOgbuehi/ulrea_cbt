import jwt as pyjwt

from io import BytesIO
from openpyxl import Workbook

from sqlmodel import select, Session
from ..conftest import engine

from auth.database.schema.cohort.cohort_db import CohortModel, CohortMember
from auth.database.schema.user.user_db import UserModel
from auth.database.schema.membership.membership_db import OrgMembership

from tests.conftest import (
    do_signup, do_request_otp, do_verify_otp,
    do_full_signup, SIGNUP_PAYLOAD, USER_EMAIL
)


ADMIN_PAYLOAD = {
    "firstname": "Alice",
    "lastname": "Admin",
    "email": "alice@cbtech.com",
    "phone": "+1000000001",
    "role": "admin",
}

TEACHER_PAYLOAD = {
    "firstname": "Bob",
    "lastname": "Teacher",
    "email": "bob@cbtech.com",
    "phone": "+1000000002",
    "role": "teacher",
}

STUDENT_PAYLOAD = {
    "firstname": "Charlie",
    "lastname": "Student",
    "phone": "+1000000003",
    "institution_id": "STU/2024/001",   # required reg number
}


def create_student_excel(rows):
    workbook = Workbook()
    worksheet = workbook.active

    worksheet.append([
        "firstname",
        "lastname",
        "othername",
        "email",
        "phone",
        "institution_id",
        "access_code",
    ])

    for row in rows:
        worksheet.append([
            row.get("firstname"),
            row.get("lastname"),
            row.get("othername"),
            row.get("email"),
            row.get("phone"),
            row.get("institution_id"),
            row.get("access_code"),
        ])

    output = BytesIO()
    workbook.save(output)
    output.seek(0)

    return output


def get_super_admin_token(client) -> str:
    verify_data = do_full_signup(client)
    return verify_data['token']['access_token']


def _activate_staff(client, user_id: str, password: str = "newSecurePass123!") -> dict:
    """Helper: generate activation token and complete staff activation flow."""
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


# ============================================================
# STAFF MANAGEMENT
# ============================================================

def test_super_admin_can_create_admin(client):
    token = get_super_admin_token(client)

    response = client.post(
        "/api/v1/users/staff/create",
        json=ADMIN_PAYLOAD,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.json()
    data = response.json()
    assert data['role'] == 'admin'
    assert data['is_first_login'] is True
    assert 'temporary_password' in data


def test_super_admin_can_create_student(client):
    token = get_super_admin_token(client)

    response = client.post(
        "/api/v1/users/students/create",
        json=STUDENT_PAYLOAD,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.json()
    data = response.json()
    assert data['role'] == 'student'
    assert len(data['access_code']) == 11
    assert data['is_first_login'] is True


def test_admin_can_create_teacher(client):
    """Smoke test: super admin creates an admin, admin activates, admin creates teacher."""
    super_token = get_super_admin_token(client)

    # Create admin
    create_resp = client.post(
        "/api/v1/users/staff/create",
        json=ADMIN_PAYLOAD,
        headers={"Authorization": f"Bearer {super_token}"},
    )
    assert create_resp.status_code == 200, create_resp.json()
    admin_id = create_resp.json()["id"]

    # Admin activates account
    activate_data = _activate_staff(client, admin_id)
    admin_token = activate_data["access_token"]

    # Admin creates a teacher
    response = client.post(
        "/api/v1/users/staff/create",
        json=TEACHER_PAYLOAD,
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200, response.json()
    assert response.json()["role"] == "teacher"


def test_staff_first_login_setup(client):
    """Staff creation → activation token flow (temp-password login is deprecated)."""
    token = get_super_admin_token(client)

    create_response = client.post(
        "/api/v1/users/staff/create",
        json=TEACHER_PAYLOAD,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert create_response.status_code == 200, create_response.json()
    created_user = create_response.json()

    # Activate via token — temp-password login is no longer supported
    activate_data = _activate_staff(client, created_user["id"])
    assert "access_token" in activate_data

    # Login with newly set password works
    final_login = client.post(
        "/api/v1/auth/login",
        data={"username": TEACHER_PAYLOAD["email"], "password": "newSecurePass123!"},
    )
    assert final_login.status_code == 200, final_login.json()


def test_staff_account_activation(client):
    from auth.utility.jwt.token_activation import create_staff_activation_token

    token = get_super_admin_token(client)

    create_response = client.post(
        "/api/v1/users/staff/create",
        json=TEACHER_PAYLOAD,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert create_response.status_code == 200, create_response.json()
    created_user = create_response.json()
    assert created_user["is_first_login"] is True

    activation_token = create_staff_activation_token(created_user["id"])

    activate_response = client.post(
        "/api/v1/users/staff/activate",
        json={
            "token": activation_token,
            "password": "newSecurePass123!",
            "confirm_password": "newSecurePass123!",
        },
    )
    assert activate_response.status_code == 200, activate_response.json()
    activate_data = activate_response.json()
    assert activate_data["detail"] == "Account activated successfully."
    assert "access_token" in activate_data
    assert "refresh_token" in activate_data

    # Login with new password
    login_response = client.post(
        "/api/v1/auth/login",
        data={"username": TEACHER_PAYLOAD["email"], "password": "newSecurePass123!"},
    )
    assert login_response.status_code == 200, login_response.json()
    assert "access_token" in login_response.json()


def test_teacher_cannot_create_users(client):
    super_token = get_super_admin_token(client)

    # Create and activate teacher
    create_resp = client.post(
        "/api/v1/users/staff/create",
        json=TEACHER_PAYLOAD,
        headers={"Authorization": f"Bearer {super_token}"},
    )
    assert create_resp.status_code == 200, create_resp.json()
    teacher_id = create_resp.json()["id"]

    activate_data = _activate_staff(client, teacher_id)
    teacher_token = activate_data["access_token"]

    # Teacher tries to create another user — role check fires before anything else
    response = client.post(
        "/api/v1/users/staff/create",
        json=ADMIN_PAYLOAD,
        headers={"Authorization": f"Bearer {teacher_token}"},
    )
    assert response.status_code == 403, response.json()


# ============================================================
# STUDENT MANAGEMENT
# ============================================================

def test_student_first_login_setup_and_login(client):
    token = get_super_admin_token(client)

    create_response = client.post(
        "/api/v1/users/students/create",
        json=STUDENT_PAYLOAD,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert create_response.status_code == 200, create_response.json()
    access_code = create_response.json()['access_code']

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

    # Student logs in with access code + favorite answer
    login_response = client.post(
        "/api/v1/users/student/login",
        json={
            "access_code": access_code,
            "favorite_answer": "Fluffy",
        }
    )
    assert login_response.status_code == 200, login_response.json()
    assert 'access_token' in login_response.json()


def test_student_cannot_use_staff_login(client):
    token = get_super_admin_token(client)

    client.post(
        "/api/v1/users/students/create",
        json=STUDENT_PAYLOAD,
        headers={"Authorization": f"Bearer {token}"},
    )

    # Students have no email/password — staff login should reject them
    response = client.post(
        "/api/v1/auth/login",
        data={"username": "notanemail@x.com", "password": "anything"}
    )
    assert response.status_code == 401


def test_student_can_fetch_security_question(client):
    token = get_super_admin_token(client)

    create_response = client.post(
        "/api/v1/users/students/create",
        json=STUDENT_PAYLOAD,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert create_response.status_code == 200, create_response.json()
    access_code = create_response.json()["access_code"]

    # Complete first-time setup
    setup_response = client.post(
        "/api/v1/users/student/init",
        json={
            "access_code": access_code,
            "favorite_question": "What is your pet's name?",
            "favorite_answer": "Fluffy",
        },
    )
    assert setup_response.status_code == 200, setup_response.json()

    # Fetch security question
    response = client.post(
        "/api/v1/users/student/login/question",
        json={"access_code": access_code},
    )
    assert response.status_code == 200, response.json()
    assert response.json()["favorite_question"] == "What is your pet's name?"


def test_student_question_invalid_access_code(client):
    response = client.post(
        "/api/v1/users/student/login/question",
        json={"access_code": "INVALID123"},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Invalid access code."


def test_student_question_requires_first_time_setup(client):
    token = get_super_admin_token(client)

    create_response = client.post(
        "/api/v1/users/students/create",
        json=STUDENT_PAYLOAD,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert create_response.status_code == 200, create_response.json()
    access_code = create_response.json()["access_code"]

    # No setup done yet — question endpoint must reject
    response = client.post(
        "/api/v1/users/student/login/question",
        json={"access_code": access_code},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Please complete first-time setup first."


# ============================================================
# DEBUG (keep until redis path confirmed stable, then delete)
# ============================================================

def test_debug_redis_path(client):
    from auth.dependencies import auth_dependencies
    import auth.api.v1.routes.users as users_module

    token = get_super_admin_token(client)

    print("\nauth_dependencies redis_client:", auth_dependencies.redis_client)
    print("users module redis_client:", getattr(users_module, 'redis_client', 'NOT FOUND'))

    response = client.post(
        "/api/v1/users/staff/create",
        json=ADMIN_PAYLOAD,
        headers={"Authorization": f"Bearer {token}"},
    )
    print("Response:", response.json())
    print("Status:", response.status_code)












# --------

def _decode_user_id(token: str) -> str:
    """Test-only helper — pulls `sub` out of a token without verifying signature."""
    payload = pyjwt.decode(token, options={"verify_signature": False})
    return payload["sub"]


# ============================================================
# ADMIN UPDATE USER
# ============================================================

def test_admin_can_update_staff_email(client):
    token = get_super_admin_token(client)

    create_resp = client.post(
        "/api/v1/users/staff/create",
        json=TEACHER_PAYLOAD,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert create_resp.status_code == 200, create_resp.json()
    teacher_id = create_resp.json()["id"]

    update_resp = client.patch(
        f"/api/v1/users/staff/{teacher_id}",
        json={"email": "corrected@cbtech.com"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert update_resp.status_code == 200, update_resp.json()
    assert update_resp.json()["email"] == "corrected@cbtech.com"


def test_cannot_update_to_duplicate_email(client):
    token = get_super_admin_token(client)

    client.post(
        "/api/v1/users/staff/create",
        json=ADMIN_PAYLOAD,
        headers={"Authorization": f"Bearer {token}"},
    )
    teacher_resp = client.post(
        "/api/v1/users/staff/create",
        json=TEACHER_PAYLOAD,
        headers={"Authorization": f"Bearer {token}"},
    )
    teacher_id = teacher_resp.json()["id"]

    response = client.patch(
        f"/api/v1/users/staff/{teacher_id}",
        json={"email": ADMIN_PAYLOAD["email"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 409, response.json()


def test_teacher_cannot_update_other_users(client):
    token = get_super_admin_token(client)

    teacher_resp = client.post(
        "/api/v1/users/staff/create",
        json=TEACHER_PAYLOAD,
        headers={"Authorization": f"Bearer {token}"},
    )
    teacher_id = teacher_resp.json()["id"]
    teacher_token = _activate_staff(client, teacher_id)["access_token"]

    another_resp = client.post(
        "/api/v1/users/staff/create",
        json=ADMIN_PAYLOAD,
        headers={"Authorization": f"Bearer {token}"},
    )
    another_id = another_resp.json()["id"]

    response = client.patch(
        f"/api/v1/users/staff/{another_id}",
        json={"firstname": "Hacked"},
        headers={"Authorization": f"Bearer {teacher_token}"},
    )
    assert response.status_code == 403, response.json()


# ============================================================
# ADMIN DELETE USER
# ============================================================

def test_admin_can_delete_never_activated_staff(client):
    token = get_super_admin_token(client)

    create_resp = client.post(
        "/api/v1/users/staff/create",
        json=TEACHER_PAYLOAD,
        headers={"Authorization": f"Bearer {token}"},
    )
    teacher_id = create_resp.json()["id"]

    delete_resp = client.delete(
        f"/api/v1/users/{teacher_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert delete_resp.status_code == 200, delete_resp.json()
    assert delete_resp.json()["action"] == "deleted"

    # confirm gone
    followup = client.patch(
        f"/api/v1/users/staff/{teacher_id}",
        json={"firstname": "Ghost"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert followup.status_code == 404


def test_cannot_delete_active_user_without_force(client):
    token = get_super_admin_token(client)

    create_resp = client.post(
        "/api/v1/users/staff/create",
        json=TEACHER_PAYLOAD,
        headers={"Authorization": f"Bearer {token}"},
    )
    teacher_id = create_resp.json()["id"]
    _activate_staff(client, teacher_id)  # now activated

    response = client.delete(
        f"/api/v1/users/{teacher_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400
    assert "force" in response.json()["detail"].lower()


def test_admin_cannot_force_delete_active_user(client):
    """force=true requires super_admin — a plain admin gets 403 even with the flag."""
    super_token = get_super_admin_token(client)

    admin_create = client.post(
        "/api/v1/users/staff/create",
        json=ADMIN_PAYLOAD,
        headers={"Authorization": f"Bearer {super_token}"},
    )
    admin_id = admin_create.json()["id"]
    admin_token = _activate_staff(client, admin_id)["access_token"]

    teacher_create = client.post(
        "/api/v1/users/staff/create",
        json=TEACHER_PAYLOAD,
        headers={"Authorization": f"Bearer {super_token}"},
    )
    teacher_id = teacher_create.json()["id"]
    _activate_staff(client, teacher_id)  # now activated

    response = client.delete(
        f"/api/v1/users/{teacher_id}?force=true",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 403


def test_super_admin_can_force_delete_active_user(client):
    super_token = get_super_admin_token(client)

    create_resp = client.post(
        "/api/v1/users/staff/create",
        json=TEACHER_PAYLOAD,
        headers={"Authorization": f"Bearer {super_token}"},
    )
    teacher_id = create_resp.json()["id"]
    _activate_staff(client, teacher_id)  # now activated

    response = client.delete(
        f"/api/v1/users/{teacher_id}?force=true",
        headers={"Authorization": f"Bearer {super_token}"},
    )
    assert response.status_code == 200
    assert response.json()["action"] == "deleted"


def test_cannot_delete_super_admin(client):
    token = get_super_admin_token(client)
    super_admin_id = _decode_user_id(token)

    response = client.delete(
        f"/api/v1/users/{super_admin_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.json()['detail'] == 'You cannot delete your own account.'
    # assert response.status_code == 403


def test_cannot_delete_self(client):
    super_token = get_super_admin_token(client)

    admin_create = client.post(
        "/api/v1/users/staff/create",
        json=ADMIN_PAYLOAD,
        headers={"Authorization": f"Bearer {super_token}"},
    )
    admin_id = admin_create.json()["id"]
    admin_token = _activate_staff(client, admin_id)["access_token"]

    response = client.delete(
        f"/api/v1/users/{admin_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 400
    # assert "yourself" in response.json()["detail"].lower()


# Bulk create without cohort
def test_bulk_create_students_without_cohort(client):
    token = get_super_admin_token(client)

    excel_file = create_student_excel([
        {
            "firstname": "John",
            "lastname": "Doe",
            "email": "john.bulk@example.com",
            "institution_id": "848348349",
        },
        {
            "firstname": "Jane",
            "lastname": "Doe",
            "email": "jane.bulk@example.com",
            "institution_id": "848390696",
        },
    ])

    response = client.post(
        "/api/v1/users/students/create/bulk",
        files={
            "file": (
                "students.xlsx",
                excel_file.getvalue(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        headers={
            "Authorization": f"Bearer {token}",
        },
    )

    assert response.status_code == 200, response.json()

    data = response.json()

    print("\nBULK RESPONSE:")
    print(data)

    assert data["total_rows"] == 2
    assert data["successful_rows"] == 2
    assert data["failed_rows"] == 0
    assert len(data["students"]) == 2

# Bulk create with Cohort
def test_bulk_create_students_assigns_to_cohort(client):
    token = get_super_admin_token(client)

    # Get the authenticated user's organization
    with Session(engine) as session:
        user = session.exec(
            select(UserModel).where(
                UserModel.email == USER_EMAIL
            )
        ).first()

        assert user is not None

        membership = session.exec(
            select(OrgMembership).where(
                OrgMembership.user_id == user.id
            )
        ).first()

        assert membership is not None

        user_id = user.id
        org_id = membership.org_id

        cohort = CohortModel(
            org_id=org_id,
            name="JSS3A",
            description="Test cohort",
            created_by=user_id,
        )

        session.add(cohort)
        session.commit()
        session.refresh(cohort)

        cohort_id = cohort.id

    excel_file = create_student_excel([
        {
            "firstname": "John",
            "lastname": "Doe",
            "email": "john.cohort@example.com",
            "institution_id": "848398349",
        },
        {
            "firstname": "Jane",
            "lastname": "Doe",
            "email": "jane.cohort@example.com",
            "institution_id": "4309434",
        },
    ])

    response = client.post(
        f"/api/v1/users/students/create/bulk?cohort_id={cohort_id}",
        files={
            "file": (
                "students.xlsx",
                excel_file.getvalue(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        headers={
            "Authorization": f"Bearer {token}",
        },
    )

    assert response.status_code == 200, response.json()

    data = response.json()

    assert data["total_rows"] == 2
    assert data["successful_rows"] == 2
    assert data["failed_rows"] == 0
    assert len(data["students"]) == 2

    # API returns string UUIDs
    student_ids = {
        student["id"]
        for student in data["students"]
    }

    # Verify cohort memberships
    with Session(engine) as session:
        members = session.exec(
            select(CohortMember).where(
                CohortMember.cohort_id == cohort_id
            )
        ).all()

        assert len(members) == 2

        # Convert DB UUIDs to strings for comparison
        assigned_student_ids = {
            str(member.student_id)
            for member in members
        }

        assert assigned_student_ids == student_ids

        for member in members:
            assert member.org_id == org_id
            assert member.cohort_id == cohort_id
            assert member.added_by == user_id
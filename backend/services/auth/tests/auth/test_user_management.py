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
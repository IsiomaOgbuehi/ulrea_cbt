"""Editable roles: built-in defaults, edits, custom roles, assignment and the token claim."""
from typing import Any

import jwt

from tests.auth.test_cohorts import (
    TEACHER_2_PAYLOAD, TEACHER_PAYLOAD, _create_and_activate_teacher, get_super_admin_token,
)

V1 = "/api/v1"
ADMIN_PAYLOAD = {**TEACHER_PAYLOAD, "email": "ada@cbtech.com", "phone": "+1000000077", "firstname": "Ada", "role": "admin"}
STAFF_PAYLOAD = {**TEACHER_PAYLOAD, "email": "sam@cbtech.com", "phone": "+1000000078", "firstname": "Sam", "role": "staff"}


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def claims(token: str) -> dict[str, Any]:
    return jwt.decode(token, options={"verify_signature": False})


def roles_by_key(client, token: str) -> dict[str, dict[str, Any]]:
    r = client.get(f"{V1}/roles", headers=auth(token))
    assert r.status_code == 200, r.json()
    return {role["key"]: role for role in r.json()}


def login(client, email: str, password: str = "newSecurePass123!") -> str:
    r = client.post(f"{V1}/auth/login", data={"username": email, "password": password})
    assert r.status_code == 200, r.json()
    return r.json()["access_token"]


# ----------------------------------------------------------- reading

def test_catalog_lists_every_permission(client) -> None:
    token = get_super_admin_token(client)
    r = client.get(f"{V1}/roles/catalog", headers=auth(token))
    assert r.status_code == 200
    keys = {p["key"] for p in r.json()}
    assert {"exams.view", "roles.manage", "exam_access.pay"} <= keys
    assert next(p for p in r.json() if p["key"] == "exam_access.pay")["candidate_only"] is True


def test_built_in_roles_with_member_counts(client) -> None:
    token = get_super_admin_token(client)
    _create_and_activate_teacher(client, token)
    roles = roles_by_key(client, token)
    assert list(roles)[:5] == ["super_admin", "admin", "teacher", "supervisor", "staff"]
    assert roles["super_admin"]["editable"] is False and roles["super_admin"]["member_count"] == 1
    assert roles["teacher"]["member_count"] == 1 and roles["teacher"]["is_customised"] is False
    assert roles["teacher"]["permissions"]["questions.manage"] == "assigned"
    assert roles["staff"]["permissions"] == {}


def test_tokens_carry_effective_permissions(client) -> None:
    token = get_super_admin_token(client)
    _, teacher_token = _create_and_activate_teacher(client, token)
    perms = claims(teacher_token)["perms"]
    assert perms["questions.manage"] == "assigned"
    assert perms["questions.view"] == "assigned"      # implied ones included
    assert "subjects.manage" not in perms
    assert set(claims(token)["perms"].values()) == {"all"}


# ----------------------------------------------------------- editing

def test_editing_a_built_in_role_applies_and_resets(client) -> None:
    token = get_super_admin_token(client)
    _, teacher_token = _create_and_activate_teacher(client, token)

    r = client.patch(
        f"{V1}/roles/teacher", json={"permissions": {"questions.view": "assigned"}}, headers=auth(token)
    )
    assert r.status_code == 200, r.json()
    assert r.json()["is_customised"] is True

    me = client.get(f"{V1}/roles/me", headers=auth(teacher_token)).json()
    assert "questions.manage" not in me["permissions"]
    # New tokens carry the edited permissions too.
    assert "questions.manage" not in claims(login(client, TEACHER_PAYLOAD["email"]))["perms"]

    r = client.post(f"{V1}/roles/teacher/reset", headers=auth(token))
    assert r.json()["is_customised"] is False
    me = client.get(f"{V1}/roles/me", headers=auth(teacher_token)).json()
    assert me["permissions"]["questions.manage"] == "assigned"


def test_custom_role_lifecycle(client) -> None:
    token = get_super_admin_token(client)
    teacher_id, teacher_token = _create_and_activate_teacher(client, token)

    r = client.post(
        f"{V1}/roles",
        json={"name": "HR Officer", "description": "People records",
              "permissions": {"staff.view": "all", "candidates.view": "all"}},
        headers=auth(token),
    )
    assert r.status_code == 201, r.json()
    assert r.json()["key"] == "hr-officer" and r.json()["base_role"] == "staff"

    r = client.put(f"{V1}/users/staff/{teacher_id}/role", json={"role": "hr-officer"}, headers=auth(token))
    assert r.status_code == 200, r.json()
    assert r.json()["role"] == "staff" and r.json()["role_name"] == "HR Officer"
    assert client.get(f"{V1}/users/staff", headers=auth(teacher_token)).status_code == 200

    staff = client.get(f"{V1}/users/staff", headers=auth(token)).json()["staff"]
    assert {s["role_name"] for s in staff} >= {"HR Officer"}

    # Can't delete a role people hold.
    assert client.delete(f"{V1}/roles/hr-officer", headers=auth(token)).status_code == 409
    client.put(f"{V1}/users/staff/{teacher_id}/role", json={"role": "teacher"}, headers=auth(token))
    assert client.delete(f"{V1}/roles/hr-officer", headers=auth(token)).status_code == 204


def test_create_staff_straight_into_a_custom_role(client) -> None:
    token = get_super_admin_token(client)
    client.post(f"{V1}/roles", json={"name": "Marker", "permissions": {"results.mark": "assigned"}}, headers=auth(token))
    r = client.post(f"{V1}/users/staff/create", json={**STAFF_PAYLOAD, "role_key": "marker"}, headers=auth(token))
    assert r.status_code == 200, r.json()
    assert roles_by_key(client, token)["marker"]["member_count"] == 1


# --------------------------------------------------------- guard rails

def test_invalid_permission_sets_are_rejected(client) -> None:
    token = get_super_admin_token(client)
    for perms in ({"made.up": "all"}, {"staff.view": "assigned"}, {"exam_access.pay": "all"}, {"exams.view": "everywhere"}):
        r = client.post(f"{V1}/roles", json={"name": "Bad", "permissions": perms}, headers=auth(token))
        assert r.status_code == 422, (perms, r.json())
    assert client.post(f"{V1}/roles", json={"name": "Teacher"}, headers=auth(token)).status_code == 409


def test_only_the_owner_manages_roles_and_admins(client) -> None:
    token = get_super_admin_token(client)
    _, admin_token = _create_and_activate_teacher(client, token, ADMIN_PAYLOAD)
    teacher_id, _ = _create_and_activate_teacher(client, token, TEACHER_2_PAYLOAD)

    # Admins can see roles and give non-admin roles, but not edit roles or make admins.
    assert client.get(f"{V1}/roles", headers=auth(admin_token)).status_code == 200
    assert client.patch(f"{V1}/roles/teacher", json={"permissions": {}}, headers=auth(admin_token)).status_code == 403
    assert client.put(f"{V1}/users/staff/{teacher_id}/role", json={"role": "supervisor"}, headers=auth(admin_token)).status_code == 200
    assert client.put(f"{V1}/users/staff/{teacher_id}/role", json={"role": "admin"}, headers=auth(admin_token)).status_code == 403


def test_owner_role_is_fixed(client) -> None:
    token = get_super_admin_token(client)
    owner_id = claims(token)["sub"]
    assert client.patch(f"{V1}/roles/super_admin", json={"permissions": {}}, headers=auth(token)).status_code == 400
    r = client.put(f"{V1}/users/staff/{owner_id}/role", json={"role": "staff"}, headers=auth(token))
    assert r.status_code in (400, 403)


def test_cannot_give_a_role_beyond_your_own(client) -> None:
    token = get_super_admin_token(client)
    # The owner lets admins manage roles…
    client.patch(
        f"{V1}/roles/admin",
        json={"permissions": {"roles.manage": "all", "staff.view": "all", "staff.manage": "all"}},
        headers=auth(token),
    )
    _, admin_token = _create_and_activate_teacher(client, token, ADMIN_PAYLOAD)
    # …but an admin still can't hand out what they don't have.
    r = client.post(
        f"{V1}/roles", json={"name": "Approver", "permissions": {"exams.approve": "all"}}, headers=auth(admin_token)
    )
    assert r.status_code == 403, r.json()
    r = client.post(f"{V1}/roles", json={"name": "Viewer", "permissions": {"staff.view": "all"}}, headers=auth(admin_token))
    assert r.status_code == 201, r.json()

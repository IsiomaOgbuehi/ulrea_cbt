# cbt_service/tests/test_permission_rules.py
"""
Route and record checks driven by permissions (not role names): the built-in
defaults, permissions carried in the token, and reach (all / assigned / own).
"""
from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlmodel import Session

from cbt_service.database.models.subject import SubjectAssignment

from .conftest import ORG_ID, create_exam, create_subject, engine, make_auth_header, make_token_payload

H = dict[str, str]
V1 = "/api/v1"


def headers_for(role: str, perms: dict[str, str] | None = None, user_id: UUID | None = None) -> tuple[H, UUID]:
    uid = user_id or uuid4()
    payload: dict[str, Any] = make_token_payload(uid, ORG_ID, role)
    if perms is not None:
        payload["perms"] = perms
    return make_auth_header(payload), uid


def assign(subject_id: str, user_id: UUID) -> None:
    with Session(engine) as session:
        session.add(SubjectAssignment(
            subject_id=UUID(subject_id), org_id=ORG_ID, assigned_to=user_id, assigned_by=uuid4(),
        ))
        session.commit()


def make_item(client: TestClient, headers: H, subject_id: str) -> int:
    """Try to add a question; returns the HTTP status code."""
    r = client.post(
        f"{V1}/subjects/{subject_id}/items",
        json={"question_text": "Q", "item_type": "short_answer", "correct_answers": ["x"]},
        headers=headers,
    )
    return r.status_code


# ------------------------------------------------------------ staff role

def test_staff_without_permissions_can_do_nothing(client: TestClient, admin_headers: H) -> None:
    staff, _ = headers_for("staff")
    subject = create_subject(client, admin_headers)["id"]
    assert client.get(f"{V1}/subjects", headers=staff).status_code == 403
    assert client.get(f"{V1}/subjects/{subject}/items", headers=staff).status_code == 403
    assert client.get(f"{V1}/exams", headers=staff).status_code == 403
    # Reading periods / settings is open to every staff member.
    assert client.get(f"{V1}/periods", headers=staff).status_code == 200


def test_permissions_in_token_apply_with_assigned_reach(client: TestClient, admin_headers: H) -> None:
    staff, staff_id = headers_for("staff", {"questions.manage": "assigned"})
    mine = create_subject(client, admin_headers, name="Mine")["id"]
    other = create_subject(client, admin_headers, name="Other")["id"]
    assign(mine, staff_id)

    assert make_item(client, staff, mine) == 200
    assert make_item(client, staff, other) == 403
    # questions.manage implies seeing subjects, but only assigned ones.
    names = {s["name"] for s in client.get(f"{V1}/subjects", headers=staff).json()["items"]}
    assert names == {"Mine"}
    # ...and nothing it doesn't imply.
    assert client.post(f"{V1}/subjects", json={"name": "New"}, headers=staff).status_code == 403


def test_token_permissions_replace_role_defaults(client: TestClient, admin_headers: H) -> None:
    # A teacher whose role was edited to remove question writing.
    teacher, teacher_id = headers_for("teacher", {"questions.view": "assigned"})
    subject = create_subject(client, admin_headers)["id"]
    assign(subject, teacher_id)
    assert client.get(f"{V1}/subjects/{subject}/items", headers=teacher).status_code == 200
    assert make_item(client, teacher, subject) == 403


def test_super_admin_is_never_restricted(client: TestClient) -> None:
    boss, _ = headers_for("super_admin", {})   # even an empty list in the token
    assert client.post(f"{V1}/subjects", json={"name": "Physics"}, headers=boss).status_code == 200


def test_students_never_get_staff_permissions(client: TestClient, admin_headers: H) -> None:
    student, _ = headers_for("student", {"subjects.manage": "all"})
    assert client.post(f"{V1}/subjects", json={"name": "Physics"}, headers=student).status_code == 403


# ---------------------------------------------------------------- exams

def test_exam_creator_reach(client: TestClient, admin_headers: H) -> None:
    """Create-only staff see and edit just the exams they made, not others'."""
    maker, _ = headers_for("staff", {"exams.create": "all"})
    subject = create_subject(client, admin_headers)["id"]
    mine = create_exam(client, maker, subject, title="Mine")
    create_exam(client, admin_headers, subject, title="Not mine")

    titles = [e["title"] for e in client.get(f"{V1}/exams", headers=maker).json()["items"]]
    assert titles == ["Mine"]
    assert client.patch(f"{V1}/exams/{mine['id']}", json={"title": "Mine 2"}, headers=maker).status_code == 200


def test_people_who_cannot_edit_exams_never_see_drafts(client: TestClient, admin_headers: H) -> None:
    viewer, _ = headers_for("staff", {"exams.view": "all"})
    subject = create_subject(client, admin_headers)["id"]
    draft = create_exam(client, admin_headers, subject)
    assert client.get(f"{V1}/exams", headers=viewer).json()["total"] == 0
    assert client.get(f"{V1}/exams/{draft['id']}", headers=viewer).status_code == 404


def test_supervisor_sees_but_cannot_review_violations(client: TestClient, admin_headers: H) -> None:
    supervisor, sup_id = headers_for("supervisor")
    subject = create_subject(client, admin_headers)["id"]
    assign(subject, sup_id)
    exam = create_exam(client, admin_headers, subject)
    client.patch(
        f"{V1}/exams/{exam['id']}/status-override", json={"status": "pending"},
        headers=headers_for("super_admin")[0],
    )
    assert client.get(f"{V1}/exams/{exam['id']}/violations", headers=supervisor).status_code == 200
    r = client.patch(f"{V1}/exams/{exam['id']}/violations/{uuid4()}/review", headers=supervisor)
    assert r.status_code == 403


def test_results_need_an_assigned_exam(client: TestClient, admin_headers: H) -> None:
    """Teachers can only export results for exams they made or whose subject they're assigned to."""
    teacher, _ = headers_for("teacher")
    subject = create_subject(client, admin_headers)["id"]
    exam = create_exam(client, admin_headers, subject)
    client.patch(
        f"{V1}/exams/{exam['id']}/status-override", json={"status": "approved"},
        headers=headers_for("super_admin")[0],
    )
    assert client.get(f"{V1}/attempts/{exam['id']}/result/export", headers=teacher).status_code == 403

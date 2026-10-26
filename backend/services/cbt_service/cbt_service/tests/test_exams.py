# exam_service/tests/test_exams.py
import pytest
from uuid import uuid4, UUID

from sqlmodel import Session, select
from cbt_service.database.models.exam import ExamAssignment
from cbt_service.clients.auth_client import AuthClient
from .conftest import engine, STUDENT_ID

from .attempt_conftest import start_attempt
from .conftest import create_exam, add_items, make_auth_header, seed_items, ORG_ID, TEST_SECRET


# ============================================================
# EXAM CRUD
# ============================================================

def test_teacher_can_create_exam(client, teacher_headers):
    exam = create_exam(client, teacher_headers)
    assert exam["status"] == "draft"
    assert exam["title"] == "Math Final Exam"
    assert exam["total_marks"] == 0.0


def test_teacher_only_sees_own_exams(client, teacher_headers, admin_headers) -> None:
    create_exam(client, teacher_headers, title="Teacher Exam")
    create_exam(client, admin_headers, title="Admin Exam")

    response = client.get("/api/v1/exams", headers=teacher_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["title"] == "Teacher Exam"


def test_admin_sees_all_exams(client, teacher_headers, admin_headers) -> None:
    create_exam(client, teacher_headers)
    create_exam(client, admin_headers)

    response = client.get("/api/v1/exams", headers=admin_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2
    

def test_list_exams_paginates_and_filters(client, teacher_headers) -> None:
    for title in ("Algebra Mid", "Biology Mid", "Algebra Final"):
        create_exam(client, teacher_headers, title=title)  # adjust to your helper's signature

    page = client.get("/api/v1/exams?page=1&page_size=2", headers=teacher_headers).json()
    assert page["total"] == 3 and len(page["items"]) == 2 and page["total_pages"] == 2

    hit = client.get("/api/v1/exams?search=algebra", headers=teacher_headers).json()
    assert hit["total"] == 2

    draft = client.get("/api/v1/exams?status=draft", headers=teacher_headers).json()
    assert draft["total"] == 3


def test_teacher_cannot_edit_other_teachers_exam(client, teacher_headers):
    from cbt_service.tests.conftest import make_auth_header, ORG_ID
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
    items = add_items(client, teacher_headers, exam["id"], seed_items(exam["subject_id"]))

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
    add_items(client, teacher_headers, exam["id"], seed_items(exam["subject_id"], 2))
    response = client.post(f"/api/v1/exams/{exam['id']}/submit", headers=teacher_headers)
    assert response.status_code == 200
    assert response.json()["status"] == "pending"

    # Admin approves
    response = client.post(
        f"/api/v1/exams/{exam['id']}/approval",
        json={"action": "approved"},
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    assert response.json()["approved_by"] is not None


def test_rejection_requires_reason(client, teacher_headers, admin_headers):
    exam = create_exam(client, teacher_headers)
    add_items(client, teacher_headers, exam["id"], seed_items(exam["subject_id"]))
    client.post(f"/api/v1/exams/{exam['id']}/submit", headers=teacher_headers)

    response = client.post(
        f"/api/v1/exams/{exam['id']}/approval",
        json={"action": "reject"},  # missing reason
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_rejected_exam_can_be_resubmitted(client, teacher_headers, admin_headers):
    exam = create_exam(client, teacher_headers)
    add_items(client, teacher_headers, exam["id"], seed_items(exam["subject_id"]))
    client.post(f"/api/v1/exams/{exam['id']}/submit", headers=teacher_headers)
    client.post(
        f"/api/v1/exams/{exam['id']}/approval",
        json={"action": "rejected", "rejection_reason": "Needs more questions"},
        headers=admin_headers,
    )

    # Teacher fixes it and resubmits
    client.patch(f"/api/v1/exams/{exam['id']}", json={"title": "Fixed Exam"}, headers=teacher_headers)
    response = client.post(f"/api/v1/exams/{exam['id']}/submit", headers=teacher_headers)
    assert response.status_code == 200
    assert response.json()["status"] == "pending"


def test_teacher_cannot_approve(client, teacher_headers):
    exam = create_exam(client, teacher_headers)
    add_items(client, teacher_headers, exam["id"], seed_items(exam["subject_id"]))
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
    item_id = seed_items(exam["subject_id"])[0]

    add_items(client, teacher_headers, exam["id"], [item_id])
    add_items(client, teacher_headers, exam["id"], [item_id])  # duplicate

    response = client.get(f"/api/v1/exams/{exam['id']}/items", headers=teacher_headers)
    assert len(response.json()) == 1


def _post_items(client, headers, exam_id, item_ids):
    return client.post(
        f"/api/v1/exams/{exam_id}/items",
        json={"item_ids": [str(i) for i in item_ids]},
        headers=headers,
    )


def _exam_item_ids(client, headers, exam_id) -> set[str]:
    return {i["item_id"] for i in client.get(f"/api/v1/exams/{exam_id}/items", headers=headers).json()}


def test_add_items_rejects_unknown_ids(client, teacher_headers):
    exam = create_exam(client, teacher_headers)
    real = seed_items(exam["subject_id"])[0]
    fake = uuid4()

    response = _post_items(client, teacher_headers, exam["id"], [real, fake])

    assert response.status_code == 400
    assert response.json()["detail"]["item_ids"] == [str(fake)]
    # All or nothing: the valid question isn't added either.
    assert _exam_item_ids(client, teacher_headers, exam["id"]) == set()


def test_add_items_rejects_questions_from_another_subject(client, teacher_headers):
    exam = create_exam(client, teacher_headers)
    other_subject_item = seed_items(uuid4())[0]

    response = _post_items(client, teacher_headers, exam["id"], [other_subject_item])
    assert response.status_code == 400


def test_add_items_rejects_questions_from_another_org(client, teacher_headers):
    exam = create_exam(client, teacher_headers)
    # Same subject id, but the question belongs to a different organisation.
    foreign = seed_items(exam["subject_id"], org_id=uuid4())[0]

    response = _post_items(client, teacher_headers, exam["id"], [foreign])
    assert response.status_code == 400


def test_add_items_rejects_archived_but_allows_draft(client, teacher_headers):
    exam = create_exam(client, teacher_headers)
    archived = seed_items(exam["subject_id"], status="archived")[0]
    draft = seed_items(exam["subject_id"], status="draft")[0]

    assert _post_items(client, teacher_headers, exam["id"], [archived]).status_code == 400
    assert _post_items(client, teacher_headers, exam["id"], [draft]).status_code == 200
    assert _exam_item_ids(client, teacher_headers, exam["id"]) == {str(draft)}


def test_re_adding_a_question_already_in_the_exam_is_a_no_op(client, teacher_headers):
    exam = create_exam(client, teacher_headers)
    item_id = seed_items(exam["subject_id"])[0]
    add_items(client, teacher_headers, exam["id"], [item_id])

    # Even if it was archived since, re-sending it doesn't fail: it's skipped.
    from cbt_service.database.models.item import ItemModel
    from cbt_service.database.models.enums.item_subject_enums import ItemStatus
    with Session(engine) as session:
        item = session.get(ItemModel, item_id)
        assert item is not None
        item.status = ItemStatus.ARCHIVED
        session.add(item)
        session.commit()

    response = _post_items(client, teacher_headers, exam["id"], [item_id])
    assert response.status_code == 200 and response.json() == []


def test_remove_item_updates_total_marks(client, teacher_headers):
    exam = create_exam(client, teacher_headers)
    items = add_items(client, teacher_headers, exam["id"], seed_items(exam["subject_id"], 2))

    exam_after = client.get(f"/api/v1/exams/{exam['id']}", headers=teacher_headers).json()
    assert exam_after["total_marks"] == 2.0

    client.delete(f"/api/v1/exams/{exam['id']}/items/{items[0]['id']}", headers=teacher_headers)

    exam_after = client.get(f"/api/v1/exams/{exam['id']}", headers=teacher_headers).json()
    assert exam_after["total_marks"] == 1.0  # ← was asserting 1.0, which is correct


# ============================================================
# STUDENT ASSIGNMENT
# ============================================================

def test_students_can_only_be_assigned_to_approved_exam(client, teacher_headers, admin_headers, all_students_valid: None) -> None:
    exam = create_exam(client, teacher_headers)

    response = client.post(
        f"/api/v1/exams/{exam['id']}/assign",
        json={"student_ids": [str(uuid4())]},
        headers=admin_headers,
    )
    assert response.status_code == 400


def create_approved_exam(client, teacher_headers, admin_headers) -> dict:
    exam = create_exam(client, teacher_headers)
    add_items(client, teacher_headers, exam["id"], seed_items(exam["subject_id"]))
    client.post(f"/api/v1/exams/{exam['id']}/submit", headers=teacher_headers)
    client.post(
        f"/api/v1/exams/{exam['id']}/approval",
        json={"action": "approved"},
        headers=admin_headers,
    )
    return exam


def test_assign_students_to_approved_exam(client, teacher_headers, admin_headers, all_students_valid: None) -> None:
    exam = create_exam(client, teacher_headers)
    add_items(client, teacher_headers, exam["id"], seed_items(exam["subject_id"]))
    client.post(f"/api/v1/exams/{exam['id']}/submit", headers=teacher_headers)
    client.post(f"/api/v1/exams/{exam['id']}/approval", json={"action": "approved"}, headers=admin_headers)

    students = [str(uuid4()), str(uuid4()), str(uuid4())]
    response = client.post(
        f"/api/v1/exams/{exam['id']}/assign",
        json={"student_ids": students},
        headers=teacher_headers,
    )
    assert response.status_code == 200
    assert len(response.json()) == 3



def test_assign_rejects_students_not_in_org(
    client, teacher_headers, admin_headers, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _none_valid(self: AuthClient, student_ids: list[UUID], org_id: UUID) -> list[UUID]:
        return []

    monkeypatch.setattr(AuthClient, "filter_student_ids", _none_valid)

    exam = create_approved_exam(client, teacher_headers, admin_headers)
    response = client.post(
        f"/api/v1/exams/{exam['id']}/assign",
        json={"student_ids": [str(uuid4())]},
        headers=teacher_headers,
    )
    assert response.status_code == 422


def test_assign_returns_502_when_auth_service_down(
    client, teacher_headers, admin_headers, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _boom(self: AuthClient, student_ids: list[UUID], org_id: UUID) -> list[UUID]:
        raise RuntimeError("down")

    monkeypatch.setattr(AuthClient, "filter_student_ids", _boom)

    exam = create_approved_exam(client, teacher_headers, admin_headers)
    response = client.post(
        f"/api/v1/exams/{exam['id']}/assign",
        json={"student_ids": [str(uuid4())]},
        headers=teacher_headers,
    )
    assert response.status_code == 502


# ============================================================
# AUDIT LOG
# ============================================================

def test_audit_log_records_lifecycle(client, teacher_headers, admin_headers):
    exam = create_exam(client, teacher_headers)
    add_items(client, teacher_headers, exam["id"], seed_items(exam["subject_id"]))
    client.post(f"/api/v1/exams/{exam['id']}/submit", headers=teacher_headers)
    client.post(f"/api/v1/exams/{exam['id']}/approval", json={"action": "approved"}, headers=admin_headers)

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



def test_super_admin_can_override_exam_status_to_draft(client, super_admin_headers, admin_headers):
    exam = create_exam(client, admin_headers, title="Locked Exam")
    # push it forward first so the override is a genuine backward move
    client.post(f"/api/v1/exams/{exam['id']}/submit", headers=admin_headers)

    response = client.patch(
        f"/api/v1/exams/{exam['id']}/status-override",
        json={"status": "draft", "reason": "Needs more items before going live"},
        headers=super_admin_headers,
    )
    assert response.status_code == 200, response.json()
    assert response.json()["status"] == "draft"


def test_teacher_cannot_override_exam_status(client, teacher_headers):
    exam = create_exam(client, teacher_headers, title="Teacher Exam")

    response = client.patch(
        f"/api/v1/exams/{exam['id']}/status-override",
        json={"status": "active"},
        headers=teacher_headers,
    )
    assert response.status_code == 403


def test_cannot_override_status_to_archived_directly(client, super_admin_headers, admin_headers):
    exam = create_exam(client, admin_headers, title="Not Archivable This Way")

    response = client.patch(
        f"/api/v1/exams/{exam['id']}/status-override",
        json={"status": "archived"},
        headers=super_admin_headers,
    )
    assert response.status_code == 400
    assert "delete endpoint" in response.json()["detail"].lower()


def test_cannot_change_status_of_archived_exam(
    client,
    super_admin_headers,
    admin_headers,
    student_headers,
):
    exam = create_exam(
        client,
        admin_headers,
        title="To Be Archived",
    )

    # Force the exam into ARCHIVED via the delete path
    # by first creating an attempt.
    from uuid import UUID, uuid4
    from sqlmodel import Session

    from cbt_service.database.models.exam import ExamAssignment
    from .conftest import engine, STUDENT_ID, ADMIN_ID

    assignment_id = uuid4()

    # API responses contain UUIDs as strings.
    # SQLModel expects UUID objects for UUID columns.
    exam_id = UUID(exam["id"])
    org_id = UUID(exam["org_id"])

    with Session(engine) as session:
        session.add(
            ExamAssignment(
                id=assignment_id,
                exam_id=exam_id,
                student_id=STUDENT_ID,
                org_id=org_id,
                assigned_by=ADMIN_ID,
            )
        )
        session.commit()

    # Create an attempt so deleting the exam archives it
    start_attempt(
        client,
        student_headers,
        exam_id=exam["id"],
        assignment_id=assignment_id,
    )

    response = client.delete(
        f"/api/v1/exams/{exam['id']}",
        headers=admin_headers,
    )

    assert response.status_code == 200, response.json()

    # Archived exams should be terminal.
    response = client.patch(
        f"/api/v1/exams/{exam['id']}/status-override",
        json={"status": "draft"},
        headers=super_admin_headers,
    )

    assert response.status_code == 400
    assert "terminal state" in response.json()["detail"].lower()




def test_admin_can_delete_exam_without_attempts(client, admin_headers):
    exam = create_exam(client, admin_headers, title="Deletable Exam")

    response = client.delete(f"/api/v1/exams/{exam['id']}", headers=admin_headers)
    assert response.status_code == 200
    assert response.json()["action"] == "deleted"

    followup = client.get(f"/api/v1/exams/{exam['id']}", headers=admin_headers)
    assert followup.status_code == 404


def test_teacher_cannot_delete_exam(client, teacher_headers):
    exam = create_exam(client, teacher_headers, title="Teacher's Exam")

    response = client.delete(f"/api/v1/exams/{exam['id']}", headers=teacher_headers)
    assert response.status_code == 403


def test_deleting_exam_with_attempts_archives_instead(client, admin_headers, student_headers):
    exam = create_exam(client, admin_headers, title="Exam With Attempts")

    from uuid import UUID, uuid4
    from sqlmodel import Session
    from cbt_service.database.models.exam import ExamAssignment
    from .conftest import engine, STUDENT_ID, ADMIN_ID

    exam_id = UUID(exam["id"])
    org_id = UUID(exam["org_id"])
    assignment_id = uuid4()

    with Session(engine) as session:
        session.add(ExamAssignment(
            id=assignment_id,
            exam_id=exam_id,
            student_id=STUDENT_ID,
            org_id=org_id,
            assigned_by=ADMIN_ID,
        ))
        session.commit()

    start_attempt(client, student_headers, exam_id=exam["id"], assignment_id=assignment_id)

    response = client.delete(f"/api/v1/exams/{exam['id']}", headers=admin_headers)
    assert response.status_code == 200, response.json()
    assert response.json()["action"] == "archived"

    followup = client.get(f"/api/v1/exams/{exam['id']}", headers=admin_headers)
    assert followup.status_code == 200
    assert followup.json()["status"] == "archived"


def test_delete_nonexistent_exam_returns_404(client, admin_headers):
    response = client.delete(f"/api/v1/exams/{uuid4()}", headers=admin_headers)
    assert response.status_code == 404



def test_assign_cohort_draft_exam_fails_before_auth_call(
    client, teacher_headers, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _boom(self: AuthClient, cohort_id: UUID, org_id: UUID) -> list[UUID]:
        raise AssertionError("auth service must not be called for a draft exam")

    monkeypatch.setattr(AuthClient, "get_cohort_student_ids", _boom)

    exam = create_exam(client, teacher_headers)
    response = client.post(
        f"/api/v1/exams/{exam['id']}/assign_cohort",
        json={"cohort_id": str(uuid4())},
        headers=teacher_headers,
    )
    assert response.status_code == 400


# def test_assign_forbidden_for_unassigned_teacher(
#     client, teacher_headers, admin_headers, other_teacher_headers
# ) -> None:
#     exam = create_approved_exam(client, teacher_headers, admin_headers)
#     response = client.post(
#         f"/api/v1/exams/{exam['id']}/assign",
#         json={"student_ids": [str(uuid4())]},
#         headers=other_teacher_headers,
#     )
#     assert response.status_code == 403

def test_audit_log_reads_old_upper_case_actions(client, teacher_headers, admin_headers) -> None:
    """Rows from when the column was a Postgres enum hold the NAME ("CREATED")."""
    from cbt_service.database.models.exam import ExamAuditLog

    exam = create_exam(client, teacher_headers)
    with Session(engine) as session:
        for legacy in ("CREATED", "ARCHIVED", "EXAM_STATUS_OVERRIDE", "SOMETHING_NEW"):
            session.add(ExamAuditLog(
                exam_id=UUID(exam["id"]), org_id=ORG_ID, actor_id=ORG_ID, action=legacy,
            ))
        session.commit()

    response = client.get(f"/api/v1/exams/{exam['id']}/audit", headers=admin_headers)
    assert response.status_code == 200, response.json()
    actions = [entry["action"] for entry in response.json()]
    assert actions.count("created") == 2          # the real one + the legacy one
    assert {"exam_archived", "exam_status_override", "something_new"} <= set(actions)



def test_audit_log_keeps_details(client) -> None:
    """_log used to pass details as `metadata`, which the model ignored."""
    from cbt_service.database.models.exam import ExamAuditLog
    from cbt_service.services.exam_service import _log

    exam_id = uuid4()
    with Session(engine) as session:
        _log(session, exam_id, ORG_ID, ORG_ID, "deleted", {"exam_title": "Maths"})
        session.commit()
        row = session.exec(select(ExamAuditLog).where(ExamAuditLog.exam_id == exam_id)).one()
    assert row.action == "deleted" and row.extra_data == {"exam_title": "Maths"}

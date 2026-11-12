"""
External Exams practice: subscribed students practise an exam body's exams.
Practice never mixes with real exams.
"""
from typing import Any
from uuid import UUID

import pytest
from sqlmodel import Session
from starlette.testclient import TestClient

from cbt_service.database.models.attempt import AttemptModel
from cbt_service.database.models.exam import ExamModel
from .conftest import ORG_ID, engine
from .test_exam_settings import _content, _exam, _save, _start

H = dict[str, str]
V1 = "/api/v1"


@pytest.fixture
def access(monkeypatch: pytest.MonkeyPatch) -> dict[str, bool]:
    state = {"public": True, "subscribed": True}

    async def fake(self: Any, student_id: UUID, body_id: UUID) -> tuple[bool, bool]:
        return state["public"], state["subscribed"]

    monkeypatch.setattr("cbt_service.clients.auth_client.AuthClient.exam_access", fake)
    return state


def _listed(**settings: Any) -> dict[str, Any]:
    exam = _exam(**settings)
    with Session(engine) as s:
        e = s.get(ExamModel, exam["exam_id"])
        assert e is not None
        e.external_listed = True
        s.add(e)
        s.commit()
    return exam


def _practise(client: TestClient, h: H, exam: dict[str, Any]) -> Any:
    return client.post(f"{V1}/external/exams/{exam['exam_id']}/start", headers=h)


def test_practice_needs_a_subscription_and_a_listed_exam(client: TestClient, student_headers: H, access: dict[str, bool]) -> None:
    exam = _exam()
    assert _practise(client, student_headers, exam).status_code == 404          # not ticked
    listed = _listed()
    access["subscribed"] = False
    r = _practise(client, student_headers, listed)
    assert r.status_code == 403 and "Subscribe" in r.json()["detail"]
    access["subscribed"] = True
    r = _practise(client, student_headers, listed)
    assert r.status_code == 200 and r.json()["practice"] is True


def test_retake_replaces_answers_and_keeps_progress(client: TestClient, student_headers: H, access: dict[str, bool]) -> None:
    exam = _listed(n_mcq=4)
    first = _practise(client, student_headers, exam).json()
    items = _content(client, student_headers, first["id"])
    for i in items[:4]:
        _save(client, student_headers, first["id"], i, ["A"])
    done = client.post(f"{V1}/attempts/{first['id']}/submit", headers=student_headers).json()
    assert done["show_result"] is True and done["percentage"] is not None
    best = done["percentage"]

    cat = client.get(f"{V1}/external/bodies/{ORG_ID}/exams", headers=student_headers).json()
    row = next(e for e in cat["exams"] if e["exam_id"] == str(exam["exam_id"]))
    assert row["times_taken"] == 1 and row["best_percentage"] == best and row["latest_attempt_id"] == first["id"]

    again = _practise(client, student_headers, exam).json()
    assert again["id"] == first["id"] and again["status"] == "started"          # the same row, fresh
    assert client.get(f"{V1}/attempts/{again['id']}/responses", headers=student_headers).json() == []
    client.post(f"{V1}/attempts/{again['id']}/submit", headers=student_headers)
    row = next(e for e in client.get(f"{V1}/external/bodies/{ORG_ID}/exams", headers=student_headers).json()["exams"] if e["exam_id"] == str(exam["exam_id"]))
    assert row["times_taken"] == 2 and row["last_percentage"] == 0.0 and row["best_percentage"] == best


def test_practice_and_a_real_exam_on_the_same_exam_stay_apart(
    client: TestClient, student_headers: H, admin_headers: H, access: dict[str, bool],
) -> None:
    exam = _listed(n_mcq=2, max_attempts=1)
    real = _start(client, student_headers, exam)                     # assigned for real
    practice = _practise(client, student_headers, exam).json()
    assert practice["id"] != real["id"]
    client.post(f"{V1}/attempts/{practice['id']}/submit", headers=student_headers)
    # Practising again never touches the real attempt...
    _practise(client, student_headers, exam)
    with Session(engine) as s:
        r = s.get(AttemptModel, UUID(real["id"]))
        assert r is not None and r.status == "started" and r.practice is False
    # ...the real exam can still be resumed, and practice isn't in My exams or results.
    resumed = client.post(f"{V1}/attempts", json={"exam_id": str(exam["exam_id"]), "assignment_id": str(exam["assignment_id"])}, headers=student_headers)
    assert resumed.json()["id"] == real["id"]
    mine = [r for r in client.get(f"{V1}/exams/my-assignments", headers=student_headers).json() if r["exam_id"] == str(exam["exam_id"])]
    assert len(mine) == 1 and mine[0]["in_progress_attempt_id"] == real["id"]
    assignments = client.get(f"{V1}/exams/{exam['exam_id']}/assignments", headers=admin_headers).json()
    rows = assignments["items"] if isinstance(assignments, dict) else assignments
    assert len(rows) == 1


def test_exam_body_sees_only_anonymous_totals(client: TestClient, student_headers: H, admin_headers: H, access: dict[str, bool]) -> None:
    exam = _listed(n_mcq=2)
    a = _practise(client, student_headers, exam).json()
    client.post(f"{V1}/attempts/{a['id']}/submit", headers=student_headers)
    stats = client.get(f"{V1}/exams/{exam['exam_id']}/practice-stats", headers=admin_headers).json()
    assert stats == {"students": 1, "attempts": 1, "average_percentage": 0.0}


def test_staff_tick_show_in_external_exams(client: TestClient, admin_headers: H, student_headers: H, access: dict[str, bool]) -> None:
    exam = _exam()
    r = client.patch(f"{V1}/exams/{exam['exam_id']}/external", json={"listed": True}, headers=admin_headers)
    assert r.status_code == 200 and r.json() == {"listed": True}
    assert _practise(client, student_headers, exam).status_code == 200
    assert client.patch(f"{V1}/exams/{exam['exam_id']}/external", json={"listed": True}, headers=student_headers).status_code == 403

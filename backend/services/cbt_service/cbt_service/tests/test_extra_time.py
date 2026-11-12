"""
Extra time after a disruption (a power cut, say): answers are kept, a
running attempt gets a later deadline, and one that ran out of time reopens
with the time counted from when the candidate comes back.
"""
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlmodel import Session
from starlette.testclient import TestClient

from cbt_service.database.models.attempt import AttemptModel
from .conftest import engine
from .test_exam_settings import _age, _content, _exam, _save, _start

H = dict[str, str]
V1 = "/api/v1"
URL = f"{V1}/attempts/extra-time"


def _give(client: TestClient, h: H, ids: list[str], minutes: int, reason: str = "Power cut in hall B") -> Any:
    return client.post(URL, json={"attempt_ids": ids, "minutes": minutes, "reason": reason}, headers=h)


def _read(client: TestClient, h: H, attempt_id: str) -> dict[str, Any]:
    return client.get(f"{V1}/attempts/{attempt_id}", headers=h).json()


def test_running_attempt_gets_a_later_deadline(client: TestClient, student_headers: H, admin_headers: H) -> None:
    exam = _exam(duration_minutes=10, extra_time_seconds=0)
    attempt = _start(client, student_headers, exam)
    _content(client, student_headers, attempt["id"])
    _age(attempt["id"], 8, 0)                      # 2 minutes left
    r = _give(client, admin_headers, [attempt["id"]], 15)
    assert r.status_code == 200 and (r.json()["running"], r.json()["reopening"]) == (1, 0)
    left = _read(client, student_headers, attempt["id"])["remaining_seconds"]
    assert 16 * 60 <= left <= 17 * 60
    _age(attempt["id"], 20, 0)                     # past the old deadline, inside the new one
    assert _read(client, student_headers, attempt["id"])["status"] == "started"


def test_attempt_that_ran_out_reopens_with_answers_and_time_from_return(
    client: TestClient, student_headers: H, admin_headers: H,
) -> None:
    exam = _exam(duration_minutes=10, extra_time_seconds=0)
    attempt = _start(client, student_headers, exam)
    q1, q2 = _content(client, student_headers, attempt["id"])[:2]
    _save(client, student_headers, attempt["id"], q1, ["A"])
    _age(attempt["id"], 10, 5)                     # the power went; time ran out
    assert _save(client, student_headers, attempt["id"], q2, ["A"]).status_code == 202   # held as late
    assert _read(client, student_headers, attempt["id"])["status"] == "submitted"
    _age(attempt["id"], 60, 0)                     # they come back much later

    r = _give(client, admin_headers, [attempt["id"]], 20)
    assert (r.json()["running"], r.json()["reopening"]) == (0, 1)
    # Waiting for them: the clock is stopped at 20 minutes.
    read = _read(client, student_headers, attempt["id"])
    assert read["status"] == "started" and read["clock_started"] is False and read["remaining_seconds"] == 20 * 60

    from cbt_service.services.attempt_service import AttemptService
    with Session(engine) as session:
        row = session.get(AttemptModel, UUID(attempt["id"]))
        assert row is not None and AttemptService.summaries(session, [row])[0].awaiting_marking == 0
    # Resuming: same attempt, both answers there (the late one put back), 20 minutes from now.
    again = client.post(f"{V1}/attempts", json={"exam_id": str(exam["exam_id"]), "assignment_id": str(exam["assignment_id"])}, headers=student_headers)
    assert again.status_code == 200 and again.json()["id"] == attempt["id"]
    content = client.get(f"{V1}/attempts/{attempt['id']}/exam", headers=student_headers).json()
    assert 20 * 60 - 3 <= content["remaining_seconds"] <= 20 * 60
    saved = {r["item_id"]: r["answer"] for r in client.get(f"{V1}/attempts/{attempt['id']}/responses", headers=student_headers).json()}
    assert saved == {q1["id"]: ["A"], q2["id"]: ["A"]}

    # Submitting scores it again with everything.
    done = client.post(f"{V1}/attempts/{attempt['id']}/submit", headers=student_headers).json()
    assert done["status"] in ("submitted", "scored") and done["raw_score"] == 2.0
    # Staff see the 20 minutes they gave, not the hour the student was away.
    with Session(engine) as session:
        row = session.get(AttemptModel, UUID(attempt["id"]))
        assert row is not None and row.extra_given_seconds == 20 * 60 and row.extra_seconds > 60 * 60


def test_reopened_attempt_closes_again_when_the_new_time_runs_out(
    client: TestClient, student_headers: H, admin_headers: H,
) -> None:
    exam = _exam(duration_minutes=10, extra_time_seconds=0)
    attempt = _start(client, student_headers, exam)
    _content(client, student_headers, attempt["id"])
    _age(attempt["id"], 30, 0)
    assert _read(client, student_headers, attempt["id"])["status"] == "submitted"
    _give(client, admin_headers, [attempt["id"]], 5)
    _content(client, student_headers, attempt["id"])          # back now: 5 minutes from here
    with Session(engine) as session:
        row = session.get(AttemptModel, UUID(attempt["id"]))
        assert row is not None and row.resume_seconds is None
        row.clock_started_at = (row.clock_started_at.replace(tzinfo=timezone.utc) if row.clock_started_at.tzinfo is None else row.clock_started_at) - timedelta(minutes=6)
        session.add(row)
        session.commit()
    assert _read(client, student_headers, attempt["id"])["status"] == "submitted"


def test_left_alone_when_submitted_by_the_candidate(client: TestClient, student_headers: H, admin_headers: H) -> None:
    exam = _exam(duration_minutes=10, extra_time_seconds=0)
    attempt = _start(client, student_headers, exam)
    _content(client, student_headers, attempt["id"])
    client.post(f"{V1}/attempts/{attempt['id']}/submit", headers=student_headers)
    r = _give(client, admin_headers, [attempt["id"], "00000000-0000-0000-0000-000000000000"], 10)
    body = r.json()
    assert (body["running"], body["reopening"]) == (0, 0)
    assert [x["detail"] for x in body["skipped"]] == ["Submitted by the candidate before time ran out.", "Attempt not found."]


def test_reopened_attempt_resumes_after_the_exam_window_closed(
    client: TestClient, student_headers: H, admin_headers: H,
) -> None:
    now = datetime.now(timezone.utc)
    exam = _exam(duration_minutes=10, extra_time_seconds=0, start_time=now - timedelta(hours=2), end_time=now + timedelta(hours=1))
    attempt = _start(client, student_headers, exam)
    _content(client, student_headers, attempt["id"])
    _age(attempt["id"], 30, 0)
    _read(client, student_headers, attempt["id"])
    _give(client, admin_headers, [attempt["id"]], 10)
    from cbt_service.database.models.exam import ExamModel
    with Session(engine) as session:
        e = session.get(ExamModel, exam["exam_id"])
        assert e is not None
        e.end_time = now - timedelta(minutes=1)     # the window has closed since
        session.add(e)
        session.commit()
    mine = next(r for r in client.get(f"{V1}/exams/my-assignments", headers=student_headers).json() if r["exam_id"] == str(exam["exam_id"]))
    assert mine["can_attempt"] is True and mine["in_progress_attempt_id"] == attempt["id"]
    again = client.post(f"{V1}/attempts", json={"exam_id": str(exam["exam_id"]), "assignment_id": str(exam["assignment_id"])}, headers=student_headers)
    assert again.status_code == 200 and again.json()["id"] == attempt["id"]


def test_needs_the_permission_and_a_reason(
    client: TestClient, student_headers: H, teacher_headers: H, admin_headers: H,
) -> None:
    exam = _exam(duration_minutes=10, extra_time_seconds=0)
    attempt = _start(client, student_headers, exam)
    assert _give(client, teacher_headers, [attempt["id"]], 10).status_code == 403
    assert _give(client, admin_headers, [attempt["id"]], 10, reason="").status_code == 422
    assert _give(client, admin_headers, [attempt["id"]], 0).status_code == 422


def test_results_show_extra_time_and_last_activity(client: TestClient, student_headers: H, admin_headers: H) -> None:
    exam = _exam(duration_minutes=10, extra_time_seconds=0)
    attempt = _start(client, student_headers, exam)
    q1 = _content(client, student_headers, attempt["id"])[0]
    _save(client, student_headers, attempt["id"], q1, ["A"])
    _give(client, admin_headers, [attempt["id"]], 7)
    from cbt_service.services.attempt_service import AttemptService
    with Session(engine) as session:
        row = session.get(AttemptModel, UUID(attempt["id"]))
        assert row is not None
        summary = AttemptService.summaries(session, [row])[0]
    assert summary.extra_seconds == 7 * 60 and summary.last_active_at is not None and summary.waiting_to_resume is False


# ------------------------------------------------------------- whole exam / group

def _second_student_attempt(client: TestClient, exam: dict[str, Any], cohort_id: UUID | None = None) -> str:
    """Another student on the same exam (optionally in a group), with an attempt under way."""
    from uuid import uuid4
    from cbt_service.database.models.enums.exam_enum import AssignmentStatus
    from cbt_service.database.models.exam import ExamAssignment
    from .conftest import ORG_ID, TEACHER_ID
    student_id = uuid4()
    with Session(engine) as session:
        assignment = ExamAssignment(
            exam_id=exam["exam_id"], student_id=student_id, org_id=ORG_ID, assigned_by=TEACHER_ID,
            status=AssignmentStatus.STARTED, cohort_id=cohort_id,
        )
        session.add(assignment)
        session.flush()
        attempt = AttemptModel(
            exam_id=exam["exam_id"], student_id=student_id, org_id=ORG_ID, assignment_id=assignment.id,
            clock_started_at=datetime.now(timezone.utc),
        )
        session.add(attempt)
        session.commit()
        return str(attempt.id)


def _scope(client: TestClient, h: H, path: str, **body: Any) -> Any:
    return client.post(f"{URL}{path}", json=body, headers=h)


def test_whole_exam_reaches_everyone_writing_and_recently_out_of_time(
    client: TestClient, student_headers: H, admin_headers: H,
) -> None:
    exam = _exam(duration_minutes=10, extra_time_seconds=0)
    mine = _start(client, student_headers, exam)
    _content(client, student_headers, mine["id"])
    _age(mine["id"], 10, 30)                       # ran out 30 s ago, not yet closed
    other = _second_student_attempt(client, exam)  # still writing

    preview = _scope(client, admin_headers, "/preview", exam_id=str(exam["exam_id"])).json()
    assert (preview["running"], preview["reopening"], preview["skipped"]) == (1, 1, [])

    done = _scope(
        client, admin_headers, "", exam_id=str(exam["exam_id"]), minutes=15, reason="Generator failed",
    ).json()
    assert (done["running"], done["reopening"]) == (1, 1)
    with Session(engine) as session:
        a = session.get(AttemptModel, UUID(mine["id"]))
        b = session.get(AttemptModel, UUID(other))
        assert a is not None and b is not None
        assert a.status == "started" and a.resume_seconds == 15 * 60
        assert b.extra_seconds == 15 * 60 and b.resume_seconds is None
    # One activity entry for the whole action.
    log = client.get(f"{V1}/exams/{exam['exam_id']}/audit", headers=admin_headers).json()
    entries = [e for e in (log if isinstance(log, list) else log["items"]) if e["action"] == "attempt_extra_time"]
    assert len(entries) == 1


def test_whole_exam_leaves_out_attempts_that_ran_out_long_ago(
    client: TestClient, student_headers: H, admin_headers: H,
) -> None:
    exam = _exam(duration_minutes=10, extra_time_seconds=0)
    mine = _start(client, student_headers, exam)
    _content(client, student_headers, mine["id"])
    _age(mine["id"], 200, 0)
    _read(client, student_headers, mine["id"])      # closed now, at its deadline 190 minutes ago
    body = _scope(client, admin_headers, "/preview", exam_id=str(exam["exam_id"]), ran_out_within_minutes=60).json()
    assert (body["running"], body["reopening"]) == (0, 0)
    body = _scope(client, admin_headers, "/preview", exam_id=str(exam["exam_id"]), ran_out_within_minutes=240).json()
    assert (body["running"], body["reopening"]) == (0, 1)


def test_one_group_only(client: TestClient, student_headers: H, admin_headers: H) -> None:
    from uuid import uuid4
    exam = _exam(duration_minutes=10, extra_time_seconds=0)
    group = uuid4()
    in_group = _second_student_attempt(client, exam, cohort_id=group)
    _second_student_attempt(client, exam, cohort_id=uuid4())
    mine = _start(client, student_headers, exam)    # assigned individually
    _content(client, student_headers, mine["id"])

    body = _scope(client, admin_headers, "", exam_id=str(exam["exam_id"]), cohort_id=str(group), minutes=5, reason="Lab 2 power").json()
    assert body["running"] == 1
    individual = _scope(client, admin_headers, "/preview", exam_id=str(exam["exam_id"]), cohort_id="individual").json()
    assert individual["running"] == 1
    with Session(engine) as session:
        row = session.get(AttemptModel, UUID(in_group))
        assert row is not None and row.extra_seconds == 300


def test_scope_needs_one_way_of_choosing(client: TestClient, admin_headers: H) -> None:
    assert _scope(client, admin_headers, "/preview").status_code == 422
    assert _scope(client, admin_headers, "/preview", attempt_ids=[], cohort_id="individual").status_code == 422
    from uuid import uuid4
    assert _scope(client, admin_headers, "/preview", exam_id=str(uuid4())).status_code == 404


def test_my_exams_shows_an_attempt_whose_time_ran_out_as_finished(
    client: TestClient, student_headers: H,
) -> None:
    exam = _exam(duration_minutes=10, extra_time_seconds=0)
    attempt = _start(client, student_headers, exam)
    _content(client, student_headers, attempt["id"])
    _age(attempt["id"], 30, 0)          # nothing has closed it yet
    row = next(r for r in client.get(f"{V1}/exams/my-assignments", headers=student_headers).json() if r["exam_id"] == str(exam["exam_id"]))
    assert row["in_progress_attempt_id"] is None and row["latest_attempt_id"] == attempt["id"]
    assert row["status"] == "submitted"


def test_new_exams_blocked_when_the_plan_ended_but_running_ones_carry_on(
    client: TestClient, student_headers: H, monkeypatch: Any,
) -> None:
    exam = _exam(duration_minutes=10, extra_time_seconds=0, max_attempts=2)
    attempt = _start(client, student_headers, exam)          # started while the plan ran
    _content(client, student_headers, attempt["id"])

    async def ended(self: Any, org_id: Any) -> tuple[bool, str]:
        return False, "Your organisation's plan has ended, so new exams can't be started."

    monkeypatch.setattr("cbt_service.clients.auth_client.AuthClient.exam_starts_allowed", ended)
    # Resuming the exam under way still works...
    again = client.post(f"{V1}/attempts", json={"exam_id": str(exam["exam_id"]), "assignment_id": str(exam["assignment_id"])}, headers=student_headers)
    assert again.status_code == 200 and again.json()["id"] == attempt["id"]
    # ...but a new one (a retake) is refused, with the reason.
    client.post(f"{V1}/attempts/{attempt['id']}/submit", headers=student_headers)
    retake = client.post(f"{V1}/attempts", json={"exam_id": str(exam["exam_id"]), "assignment_id": str(exam["assignment_id"])}, headers=student_headers)
    assert retake.status_code == 403 and "plan has ended" in retake.json()["detail"]

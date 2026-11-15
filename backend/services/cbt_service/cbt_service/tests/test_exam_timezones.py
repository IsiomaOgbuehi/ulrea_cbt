# cbt_service/tests/test_exam_timezones.py
"""Exam times are UTC however a client sends them: setting, rescheduling and
taking an exam never mix times with and without a zone."""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from cbt_service.schemas.attempt_schemas import ResetAttemptRequest
from cbt_service.schemas.exam_schemas import AssignStudentsRequest, ExamUpdate
from cbt_service.schemas.utc import as_utc
from cbt_service.schemas.violation_schemas import ViolationCreate
from .conftest import create_exam, create_subject

H = dict[str, str]
V1 = "/api/v1"


def _at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_incoming_times_are_made_utc() -> None:
    assert as_utc(datetime(2026, 11, 16, 9, 0)) == datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc)
    lagos = timezone(timedelta(hours=1))
    assert as_utc(datetime(2026, 11, 16, 10, 0, tzinfo=lagos)) == datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc)
    utc = timezone.utc
    reset = ResetAttemptRequest.model_validate({"reason": "x", "new_scheduled_at": "2026-11-16T09:00"})
    assert reset.new_scheduled_at == datetime(2026, 11, 16, 9, tzinfo=utc)
    assign = AssignStudentsRequest.model_validate({"student_ids": [], "scheduled_at": "2026-11-16T10:00+01:00"})
    assert assign.scheduled_at == datetime(2026, 11, 16, 9, tzinfo=utc)
    update = ExamUpdate.model_validate({"end_time": "2026-11-16T09:00"})
    assert update.end_time is not None and update.end_time.tzinfo == utc
    report = ViolationCreate.model_validate({"violation_type": "tab_switch", "occurred_at": "2026-11-16T09:00"})
    assert report.occurred_at.tzinfo == utc


def test_setting_an_exam_with_and_without_zones(client: TestClient, admin_headers: H) -> None:
    subject = create_subject(client, admin_headers)["id"]
    # Same moment three ways: no zone (UTC), Z, and Lagos time.
    a = create_exam(client, admin_headers, subject, title="A", start_time="2026-12-01T08:00:00", end_time="2026-12-01T10:00:00")
    b = create_exam(client, admin_headers, subject, title="B", start_time="2026-12-01T08:00:00Z", end_time="2026-12-01T10:00:00Z")
    c = create_exam(client, admin_headers, subject, title="C", start_time="2026-12-01T09:00:00+01:00", end_time="2026-12-01T11:00:00+01:00")
    starts = {as_utc(_at(e["start_time"])) for e in (a, b, c)}
    assert starts == {datetime(2026, 12, 1, 8, 0, tzinfo=timezone.utc)}

    # Editing with a time without a zone, against stored ones, never errors; a bad window is refused.
    r = client.patch(f"{V1}/exams/{c['id']}", json={"end_time": "2026-12-01T12:00:00"}, headers=admin_headers)
    assert r.status_code == 200, r.json()
    bad = client.patch(f"{V1}/exams/{c['id']}", json={"end_time": "2026-12-01T07:00:00"}, headers=admin_headers)
    assert bad.status_code in (400, 422), bad.json()


def test_a_window_open_now_is_live_whatever_the_zone(client: TestClient, admin_headers: H, super_admin_headers: H) -> None:
    subject = create_subject(client, admin_headers)["id"]
    now = datetime.now(timezone.utc)
    lagos = timezone(timedelta(hours=1))
    naive = create_exam(
        client, admin_headers, subject, title="No zone",
        start_time=(now - timedelta(hours=1)).replace(tzinfo=None).isoformat(),
        end_time=(now + timedelta(hours=1)).replace(tzinfo=None).isoformat(),
    )
    local = create_exam(
        client, admin_headers, subject, title="Lagos",
        start_time=(now - timedelta(minutes=30)).astimezone(lagos).isoformat(),
        end_time=(now + timedelta(minutes=30)).astimezone(lagos).isoformat(),
    )
    for e in (naive, local):
        r = client.patch(f"{V1}/exams/{e['id']}/status-override", json={"status": "approved"}, headers=super_admin_headers)
        assert r.status_code == 200, r.json()
    live = {x["title"] for x in client.get(f"{V1}/exams", params={"phase": "live"}, headers=admin_headers).json()["items"]}
    assert {"No zone", "Lagos"} <= live

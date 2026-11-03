"""
A student's "today" list: only exams set for the day in their time zone,
plus anything they have open or worked on that day.
"""
from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlmodel import Session
from starlette.testclient import TestClient

from cbt_service.database.models.attempt import AttemptModel
from cbt_service.database.models.enums.attempt_enum import AttemptStatus
from cbt_service.database.models.enums.exam_enum import AssignmentStatus, ExamStatus
from cbt_service.database.models.exam import ExamAssignment, ExamModel
from cbt_service.database.models.subject import SubjectModel
from cbt_service.services.exam_service import ExamService
from .conftest import ORG_ID, STUDENT_ID, TEACHER_ID, engine

H = dict[str, str]
URL = "/api/v1/exams/my-assignments"
DAY = date(2026, 3, 10)


def _at(d: int, h: int = 9) -> datetime:
    """10 March 2026 plus d days, at hour h UTC."""
    return datetime(2026, 3, 10, h, tzinfo=timezone.utc) + timedelta(days=d)


def _exam(
    title: str,
    start: datetime | None,
    end: datetime | None,
    *,
    scheduled_at: datetime | None = None,
    attempt: tuple[AttemptStatus, datetime] | None = None,
) -> UUID:
    exam_id, subject_id = uuid4(), uuid4()
    with Session(engine) as session:
        session.add(SubjectModel(id=subject_id, org_id=ORG_ID, name=f"S {title}", created_by=TEACHER_ID))
        session.add(ExamModel(
            id=exam_id, org_id=ORG_ID, subject_id=subject_id, created_by=TEACHER_ID, title=title,
            status=ExamStatus.ACTIVE, pass_mark=50, total_marks=10, start_time=start, end_time=end,
        ))
        assignment = ExamAssignment(
            exam_id=exam_id, student_id=STUDENT_ID, org_id=ORG_ID, assigned_by=TEACHER_ID,
            status=AssignmentStatus.ASSIGNED, scheduled_at=scheduled_at,
        )
        session.add(assignment)
        session.flush()
        if attempt is not None:
            status, when = attempt
            session.add(AttemptModel(
                exam_id=exam_id, assignment_id=assignment.id, student_id=STUDENT_ID, org_id=ORG_ID,
                status=status, started_at=when,
                submitted_at=when + timedelta(minutes=30) if status == AttemptStatus.SUBMITTED else None,
            ))
        session.commit()
    return exam_id


def _titles(client: TestClient, h: H, **params: str) -> set[str]:
    r = client.get(URL, params=params, headers=h)
    assert r.status_code == 200, r.json()
    return {row["exam_title"] for row in r.json()}


def test_only_exams_set_for_the_day(client: TestClient, student_headers: H) -> None:
    _exam("Today", _at(0, 9), _at(0, 11))
    _exam("Yesterday", _at(-1, 9), _at(-1, 11))
    _exam("Tomorrow", _at(1, 9), _at(1, 11))
    _exam("All week", _at(-3), _at(3))
    _exam("Opens today, no end", _at(0, 8), None)
    _exam("Moved to today", _at(-2), _at(0, 23), scheduled_at=_at(0, 10))
    _exam("Undated, not taken", None, None)
    _exam("Undated, done before", None, None, attempt=(AttemptStatus.SUBMITTED, _at(-5)))
    _exam("Undated, done today", None, None, attempt=(AttemptStatus.SUBMITTED, _at(0, 7)))
    _exam("Past, still in progress", _at(-2, 9), _at(-2, 11), attempt=(AttemptStatus.STARTED, _at(-2, 10)))

    assert _titles(client, student_headers, day=DAY.isoformat(), tz="UTC") == {
        "Today", "All week", "Opens today, no end", "Moved to today",
        "Undated, not taken", "Undated, done today", "Past, still in progress",
    }
    # Without a day, nothing is filtered.
    assert len(_titles(client, student_headers)) == 10


def test_day_follows_the_students_time_zone(client: TestClient, student_headers: H) -> None:
    # 23:30 UTC on 9 March is already 10 March in Lagos (UTC+1).
    _exam("Late night", _at(-1, 23) + timedelta(minutes=30), _at(-1, 23) + timedelta(minutes=50))
    assert _titles(client, student_headers, day=DAY.isoformat(), tz="Africa/Lagos") == {"Late night"}
    assert _titles(client, student_headers, day=DAY.isoformat(), tz="UTC") == set()


def test_unknown_time_zone_is_refused(client: TestClient, student_headers: H) -> None:
    r = client.get(URL, params={"day": DAY.isoformat(), "tz": "Mars/Base"}, headers=student_headers)
    assert r.status_code == 400


def test_day_bounds() -> None:
    start, end = ExamService.day_bounds(DAY, "Africa/Lagos")
    assert start == datetime(2026, 3, 9, 23, tzinfo=timezone.utc)
    assert end == datetime(2026, 3, 10, 23, tzinfo=timezone.utc)


PAST = "/api/v1/exams/my-assignments/past"


def _past(client: TestClient, h: H, **params: str) -> dict[str, Any]:
    r = client.get(PAST, params={"day": DAY.isoformat(), "tz": "UTC", **params}, headers=h)
    assert r.status_code == 200, r.json()
    return r.json()


def _past_titles(client: TestClient, h: H, **params: str) -> list[str]:
    items: list[dict[str, Any]] = _past(client, h, **params)["items"]
    return [str(row["exam_title"]) for row in items]


def test_past_exams_are_the_ones_not_on_today(client: TestClient, student_headers: H) -> None:
    _exam("Today", _at(0, 9), _at(0, 11))
    _exam("Yesterday", _at(-1, 9), _at(-1, 11))
    _exam("Last week", _at(-7, 9), _at(-7, 11))
    _exam("Tomorrow", _at(1, 9), _at(1, 11))
    _exam("All week, done Monday", _at(-3), _at(3), attempt=(AttemptStatus.SUBMITTED, _at(-1)))
    _exam("All week, not done", _at(-3), _at(3))
    _exam("Undated, done before", None, None, attempt=(AttemptStatus.SUBMITTED, _at(-5)))
    _exam("Undated, not taken", None, None)
    _exam("Past, still in progress", _at(-2, 9), _at(-2, 11), attempt=(AttemptStatus.STARTED, _at(-2, 10)))

    today = _titles(client, student_headers, day=DAY.isoformat(), tz="UTC")
    past = _past_titles(client, student_headers)
    assert today == {"Today", "All week, not done", "Undated, not taken", "Past, still in progress"}
    # Newest first: by start, else by when the student sat it.
    assert past == ["Yesterday", "All week, done Monday", "Undated, done before", "Last week"]
    assert not today & set(past)


def test_past_exams_page_and_date_filter(client: TestClient, student_headers: H) -> None:
    for d in range(1, 6):
        _exam(f"Day -{d}", _at(-d, 9), _at(-d, 11))

    first = _past(client, student_headers, page="1", page_size="2")
    assert (first["total"], first["total_pages"]) == (5, 3)
    assert _past_titles(client, student_headers, page="3", page_size="2") == ["Day -5"]

    between = _past_titles(
        client, student_headers,
        date_from=(DAY - timedelta(days=4)).isoformat(), date_to=(DAY - timedelta(days=2)).isoformat(),
    )
    assert between == ["Day -2", "Day -3", "Day -4"]
    assert _past_titles(client, student_headers, date_from=(DAY - timedelta(days=1)).isoformat()) == ["Day -1"]

    r = client.get(PAST, params={
        "day": DAY.isoformat(), "date_from": DAY.isoformat(), "date_to": (DAY - timedelta(days=3)).isoformat(),
    }, headers=student_headers)
    assert r.status_code == 400

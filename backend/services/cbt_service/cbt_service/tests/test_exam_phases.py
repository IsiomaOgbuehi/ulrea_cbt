# cbt_service/tests/test_exam_phases.py
"""Exams list grouped by phase (upcoming / live / past / drafts) and the per-exam counts."""
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi.testclient import TestClient

from .conftest import add_items, create_exam, create_subject, seed_items

H = dict[str, str]
V1 = "/api/v1"


def _iso(delta_hours: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=delta_hours)).isoformat()


def _set_status(client: TestClient, headers: H, exam_id: str, status: str) -> None:
    r = client.patch(f"{V1}/exams/{exam_id}/status-override", json={"status": status}, headers=headers)
    assert r.status_code == 200, r.json()


def _titles(client: TestClient, headers: H, **params: Any) -> list[str]:
    r = client.get(f"{V1}/exams", params=params, headers=headers)
    assert r.status_code == 200, r.json()
    return [e["title"] for e in r.json()["items"]]


def _setup(client: TestClient, admin: H, superadmin: H) -> str:
    subject = create_subject(client, admin)["id"]
    create_exam(client, admin, subject, title="Draft one")
    rejected = create_exam(client, admin, subject, title="Sent back")
    _set_status(client, superadmin, rejected["id"], "rejected")
    pending = create_exam(client, admin, subject, title="Awaiting approval")
    _set_status(client, superadmin, pending["id"], "pending")

    soon = create_exam(client, admin, subject, title="Next week", start_time=_iso(24 * 7), end_time=_iso(24 * 7 + 2))
    _set_status(client, superadmin, soon["id"], "approved")
    sooner = create_exam(client, admin, subject, title="Tomorrow", start_time=_iso(24), end_time=_iso(26))
    _set_status(client, superadmin, sooner["id"], "approved")

    running = create_exam(client, admin, subject, title="Running", start_time=_iso(-1), end_time=_iso(1))
    _set_status(client, superadmin, running["id"], "approved")
    open_ended = create_exam(client, admin, subject, title="Always open")
    _set_status(client, superadmin, open_ended["id"], "active")

    ended = create_exam(client, admin, subject, title="Ended yesterday", start_time=_iso(-26), end_time=_iso(-24))
    _set_status(client, superadmin, ended["id"], "approved")
    closed = create_exam(client, admin, subject, title="Closed")
    _set_status(client, superadmin, closed["id"], "closed")
    return subject


def test_list_by_phase(client: TestClient, admin_headers: H, super_admin_headers: H) -> None:
    _setup(client, admin_headers, super_admin_headers)

    assert set(_titles(client, admin_headers, phase="drafts")) == {"Draft one", "Sent back"}
    # Soonest first; pending approval (no start) after dated ones.
    assert _titles(client, admin_headers, phase="upcoming") == ["Tomorrow", "Next week", "Awaiting approval"]
    assert set(_titles(client, admin_headers, phase="live")) == {"Running", "Always open"}
    assert set(_titles(client, admin_headers, phase="past")) == {"Ended yesterday", "Closed"}
    assert len(_titles(client, admin_headers)) == 9


def test_phase_counts_follow_filters(client: TestClient, admin_headers: H, super_admin_headers: H) -> None:
    _setup(client, admin_headers, super_admin_headers)
    r = client.get(f"{V1}/exams/summary", headers=admin_headers)
    assert r.status_code == 200, r.json()
    assert r.json() == {"upcoming": 3, "live": 2, "past": 2, "drafts": 2, "all": 9}

    r = client.get(f"{V1}/exams/summary", params={"search": "open"}, headers=admin_headers)
    assert r.json() == {"upcoming": 0, "live": 1, "past": 0, "drafts": 0, "all": 1}


def test_list_includes_question_and_candidate_counts(client: TestClient, admin_headers: H) -> None:
    subject = create_subject(client, admin_headers)["id"]
    exam = create_exam(client, admin_headers, subject)
    add_items(client, admin_headers, exam["id"], seed_items(subject, count=3))

    row = client.get(f"{V1}/exams", headers=admin_headers).json()["items"][0]
    assert (row["item_count"], row["assigned_count"], row["submitted_count"]) == (3, 0, 0)

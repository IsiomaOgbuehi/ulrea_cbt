# cbt_service/tests/test_periods.py
"""Organisation-wide periods (session > term...) on items and exams, plus org settings."""
import io
from typing import Any

import openpyxl
from fastapi.testclient import TestClient

from .conftest import create_exam, create_subject

H = dict[str, str]
V1 = "/api/v1"


# ------------------------------------------------------------------ helpers

def make_period(
    client: TestClient, headers: H, name: str, parent_id: str | None = None, **extra: Any
) -> dict[str, Any]:
    r = client.post(f"{V1}/periods", json={"name": name, "parent_id": parent_id, **extra}, headers=headers)
    assert r.status_code == 201, r.json()
    return r.json()


def make_item(
    client: TestClient, headers: H, subject_id: str, period_id: str | None = None, text: str = "2 + 2?"
) -> dict[str, Any]:
    payload = {
        "question_text": text,
        "item_type": "mcq_single",
        "options": [{"key": "A", "text": "3"}, {"key": "B", "text": "4"}],
        "correct_answers": ["B"],
        "period_id": period_id,
    }
    r = client.post(f"{V1}/subjects/{subject_id}/items", json=payload, headers=headers)
    assert r.status_code == 200, r.json()
    return r.json()


def item_ids(client: TestClient, headers: H, subject_id: str, **params: Any) -> set[str]:
    r = client.get(f"{V1}/subjects/{subject_id}/items", params={"page_size": 100, **params}, headers=headers)
    assert r.status_code == 200, r.json()
    return {i["id"] for i in r.json()["items"]}


def school_year(client: TestClient, headers: H) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """2025/2026 with First Term and Second Term."""
    session = make_period(client, headers, "2025/2026", starts_on="2025-09-08", ends_on="2026-07-24")
    first = make_period(client, headers, "First Term", session["id"], position=0)
    second = make_period(client, headers, "Second Term", session["id"], position=1)
    return session, first, second


# ----------------------------------------------------------------- periods

def test_create_nested_periods_and_list(client: TestClient, admin_headers: H) -> None:
    session, first, second = school_year(client, admin_headers)
    assert session["parent_id"] is None and session["starts_on"] == "2025-09-08"
    assert first["parent_id"] == session["id"]

    periods = client.get(f"{V1}/periods", headers=admin_headers).json()
    assert {p["name"] for p in periods} == {"2025/2026", "First Term", "Second Term"}


def test_only_two_levels(client: TestClient, admin_headers: H) -> None:
    _, first, _ = school_year(client, admin_headers)
    r = client.post(f"{V1}/periods", json={"name": "Week 1", "parent_id": first["id"]}, headers=admin_headers)
    assert r.status_code == 400


def test_duplicate_sibling_names_rejected_case_insensitively(client: TestClient, admin_headers: H) -> None:
    session, _, _ = school_year(client, admin_headers)
    r = client.post(f"{V1}/periods", json={"name": "first term", "parent_id": session["id"]}, headers=admin_headers)
    assert r.status_code == 409
    # Same term name under another session is fine.
    other = make_period(client, admin_headers, "2026/2027")
    make_period(client, admin_headers, "First Term", other["id"])


def test_end_date_before_start_rejected(client: TestClient, admin_headers: H) -> None:
    r = client.post(
        f"{V1}/periods", json={"name": "2026", "starts_on": "2026-12-31", "ends_on": "2026-01-01"},
        headers=admin_headers,
    )
    assert r.status_code == 422


def test_only_admins_manage_periods_but_teachers_can_read(
    client: TestClient, admin_headers: H, teacher_headers: H, student_headers: H
) -> None:
    make_period(client, admin_headers, "2026")
    assert client.post(f"{V1}/periods", json={"name": "Q1"}, headers=teacher_headers).status_code == 403
    assert client.get(f"{V1}/periods", headers=teacher_headers).status_code == 200
    assert client.get(f"{V1}/periods", headers=student_headers).status_code == 403
    assert client.patch(f"{V1}/org-settings", json={}, headers=teacher_headers).status_code == 403


def test_archiving_a_session_hides_its_terms_and_blocks_new_tags(client: TestClient, admin_headers: H) -> None:
    session, first, _ = school_year(client, admin_headers)
    r = client.patch(f"{V1}/periods/{session['id']}", json={"status": "archived"}, headers=admin_headers)
    assert r.status_code == 200 and r.json()["status"] == "archived"

    assert client.get(f"{V1}/periods", headers=admin_headers).json() == []
    everything = client.get(f"{V1}/periods", params={"include_archived": True}, headers=admin_headers).json()
    assert len(everything) == 3

    subject = create_subject(client, admin_headers)["id"]
    r = client.post(
        f"{V1}/subjects/{subject}/items",
        json={"question_text": "Q", "item_type": "short_answer", "correct_answers": ["x"], "period_id": first["id"]},
        headers=admin_headers,
    )
    assert r.status_code == 400

    # Restoring the term brings its session back too.
    client.patch(f"{V1}/periods/{first['id']}", json={"status": "active"}, headers=admin_headers)
    names = {p["name"] for p in client.get(f"{V1}/periods", headers=admin_headers).json()}
    assert {"2025/2026", "First Term"} <= names


def test_delete_unused_period_but_not_used_one(client: TestClient, admin_headers: H) -> None:
    session, first, second = school_year(client, admin_headers)
    subject = create_subject(client, admin_headers)["id"]
    make_item(client, admin_headers, subject, period_id=first["id"])

    assert client.delete(f"{V1}/periods/{second['id']}", headers=admin_headers).status_code == 204
    assert client.delete(f"{V1}/periods/{first['id']}", headers=admin_headers).status_code == 409
    assert client.delete(f"{V1}/periods/{session['id']}", headers=admin_headers).status_code == 409

    spare = make_period(client, admin_headers, "2030")
    make_period(client, admin_headers, "Q1", spare["id"])
    assert client.delete(f"{V1}/periods/{spare['id']}", headers=admin_headers).status_code == 204
    assert "2030" not in {p["name"] for p in client.get(f"{V1}/periods", headers=admin_headers).json()}


# ------------------------------------------------------------ org settings

def test_settings_default_then_save_labels_and_current(client: TestClient, admin_headers: H) -> None:
    r = client.get(f"{V1}/org-settings", headers=admin_headers).json()
    assert r["is_default"] is True and r["period_parent_label"] == "Session" and r["current_period_id"] is None

    _, first, _ = school_year(client, admin_headers)
    r = client.patch(
        f"{V1}/org-settings",
        json={"period_parent_label": " Year ", "period_child_label": "Review cycle", "current_period_id": first["id"]},
        headers=admin_headers,
    )
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["is_default"] is False
    assert (body["period_parent_label"], body["period_child_label"]) == ("Year", "Review cycle")
    assert body["current_period_id"] == first["id"]

    periods = client.get(f"{V1}/periods", headers=admin_headers).json()
    assert [p["name"] for p in periods if p["is_current"]] == ["First Term"]

    # Clearing the current period.
    r = client.patch(f"{V1}/org-settings", json={"current_period_id": None}, headers=admin_headers)
    assert r.json()["current_period_id"] is None


def test_archiving_the_current_period_clears_it(client: TestClient, admin_headers: H) -> None:
    session, first, _ = school_year(client, admin_headers)
    client.patch(f"{V1}/org-settings", json={"current_period_id": first["id"]}, headers=admin_headers)
    client.patch(f"{V1}/periods/{session['id']}", json={"status": "archived"}, headers=admin_headers)
    assert client.get(f"{V1}/org-settings", headers=admin_headers).json()["current_period_id"] is None


def test_current_period_must_be_active(client: TestClient, admin_headers: H) -> None:
    old = make_period(client, admin_headers, "2019")
    client.patch(f"{V1}/periods/{old['id']}", json={"status": "archived"}, headers=admin_headers)
    r = client.patch(f"{V1}/org-settings", json={"current_period_id": old["id"]}, headers=admin_headers)
    assert r.status_code == 400


# -------------------------------------------------------------------- items

def test_items_tagged_and_filtered_by_period(client: TestClient, admin_headers: H) -> None:
    session, first, second = school_year(client, admin_headers)
    subject = create_subject(client, admin_headers)["id"]
    a = make_item(client, admin_headers, subject, period_id=first["id"])
    b = make_item(client, admin_headers, subject, period_id=second["id"])
    c = make_item(client, admin_headers, subject, period_id=session["id"])
    d = make_item(client, admin_headers, subject)
    assert a["period_id"] == first["id"]

    assert item_ids(client, admin_headers, subject, period_id=first["id"]) == {a["id"]}
    # A session includes its terms.
    assert item_ids(client, admin_headers, subject, period_id=session["id"]) == {a["id"], b["id"], c["id"]}
    assert item_ids(client, admin_headers, subject, no_period=True) == {d["id"]}

    counts = {p["name"]: p["item_count"] for p in client.get(f"{V1}/periods", headers=admin_headers).json()}
    assert counts == {"2025/2026": 1, "First Term": 1, "Second Term": 1}


def test_item_update_sets_and_clears_period(client: TestClient, admin_headers: H) -> None:
    _, first, second = school_year(client, admin_headers)
    subject = create_subject(client, admin_headers)["id"]
    item = make_item(client, admin_headers, subject, period_id=first["id"])

    r = client.patch(f"{V1}/subjects/{subject}/items/{item['id']}", json={"period_id": second["id"]}, headers=admin_headers)
    assert r.status_code == 200 and r.json()["period_id"] == second["id"]
    r = client.patch(f"{V1}/subjects/{subject}/items/{item['id']}", json={"period_id": None}, headers=admin_headers)
    assert r.json()["period_id"] is None


def test_period_from_another_org_rejected(client: TestClient, admin_headers: H, super_admin_headers: H) -> None:
    from uuid import uuid4
    subject = create_subject(client, admin_headers)["id"]
    r = client.post(
        f"{V1}/subjects/{subject}/items",
        json={"question_text": "Q", "item_type": "short_answer", "correct_answers": ["x"], "period_id": str(uuid4())},
        headers=admin_headers,
    )
    assert r.status_code == 400


def test_bulk_upload_into_a_period(client: TestClient, admin_headers: H) -> None:
    _, first, _ = school_year(client, admin_headers)
    subject = create_subject(client, admin_headers)["id"]

    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.append(["question_text", "item_type", "option_a", "option_b", "correct_answers"])
    ws.append(["Capital of Nigeria?", "mcq_single", "Lagos", "Abuja", "B"])
    ws.append(["Sky is blue", "true_false", "True", "False", "True"])
    buf = io.BytesIO()
    wb.save(buf)

    r = client.post(
        f"{V1}/subjects/{subject}/items/bulk",
        files={"file": ("q.xlsx", buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"period_id": first["id"]},
        headers=admin_headers,
    )
    assert r.status_code == 200, r.json()
    assert len(item_ids(client, admin_headers, subject, period_id=first["id"])) == 2


# -------------------------------------------------------------------- exams

def test_exam_period_create_update_and_list_filter(client: TestClient, admin_headers: H) -> None:
    session, first, second = school_year(client, admin_headers)
    subject = create_subject(client, admin_headers)["id"]
    e1 = create_exam(client, admin_headers, subject, title="First term test", period_id=first["id"])
    e2 = create_exam(client, admin_headers, subject, title="Plain exam")
    assert e1["period_id"] == first["id"] and e2["period_id"] is None

    r = client.patch(f"{V1}/exams/{e2['id']}", json={"period_id": second["id"]}, headers=admin_headers)
    assert r.status_code == 200 and r.json()["period_id"] == second["id"]

    def listed(**params: Any) -> set[str]:
        res = client.get(f"{V1}/exams", params=params, headers=admin_headers)
        assert res.status_code == 200, res.json()
        return {e["title"] for e in res.json()["items"]}

    assert listed(period_id=first["id"]) == {"First term test"}
    assert listed(period_id=session["id"]) == {"First term test", "Plain exam"}
    assert listed() == {"First term test", "Plain exam"}


def test_add_questions_to_exam_by_period(client: TestClient, admin_headers: H) -> None:
    session, first, second = school_year(client, admin_headers)
    subject = create_subject(client, admin_headers)["id"]
    in_first = {make_item(client, admin_headers, subject, period_id=first["id"], text=f"F{n}")["id"] for n in range(3)}
    in_second = {make_item(client, admin_headers, subject, period_id=second["id"], text=f"S{n}")["id"] for n in range(2)}
    make_item(client, admin_headers, subject)

    exam = create_exam(client, admin_headers, subject)
    r = client.post(f"{V1}/exams/{exam['id']}/items/from-bank", json={"period_id": first["id"]}, headers=admin_headers)
    assert r.status_code == 200, r.json()
    assert set(r.json()["item_ids"]) == in_first

    exam2 = create_exam(client, admin_headers, subject, title="Whole session draw")
    r = client.post(
        f"{V1}/exams/{exam2['id']}/items/from-bank", json={"period_id": session["id"], "count": 4},
        headers=admin_headers,
    )
    body = r.json()
    assert body["matched"] == 5 and body["added"] == 4
    assert set(body["item_ids"]) <= in_first | in_second


def test_exam_rejects_archived_period(client: TestClient, admin_headers: H) -> None:
    old = make_period(client, admin_headers, "2019")
    client.patch(f"{V1}/periods/{old['id']}", json={"status": "archived"}, headers=admin_headers)
    subject = create_subject(client, admin_headers)["id"]
    r = client.post(
        f"{V1}/exams",
        json={"title": "T", "subject_id": subject, "duration_minutes": 30, "period_id": old["id"]},
        headers=admin_headers,
    )
    assert r.status_code == 400

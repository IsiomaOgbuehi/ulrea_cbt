"""Students' institution IDs: optional; when left out, Juwazi generates one
(JW-XXXXXXXX) that no student in any organisation has."""
import re
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from auth.database.schema.membership.membership_db import OrgMembership
from auth.database.schema.user.enums import UserRole
from auth.services.user import institution_ids
from .test_user_management import STUDENT_PAYLOAD, create_student_excel, get_super_admin_token
from ..conftest import engine

GENERATED = re.compile(r"^JW-[0-9A-HJKMNP-TV-Z]{8}$")
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _membership(student_id: str) -> OrgMembership:
    with Session(engine) as s:
        m = s.exec(select(OrgMembership).where(OrgMembership.user_id == UUID(student_id))).first()
        assert m is not None
        return m


def test_student_without_an_id_gets_a_generated_one(client: TestClient) -> None:
    token = get_super_admin_token(client)
    payload = {k: v for k, v in STUDENT_PAYLOAD.items() if k != "institution_id"}
    r = client.post("/api/v1/users/students/create", json=payload, headers=_auth(token))
    assert r.status_code == 200, r.json()
    body = r.json()
    assert GENERATED.match(body["institution_id"]) and body["institution_id_generated"] is True
    m = _membership(body["id"])
    assert m.institution_id == body["institution_id"] and m.institution_id_generated is True

    # Blank is the same as left out.
    r2 = client.post("/api/v1/users/students/create", json={**payload, "phone": "+1000000099", "institution_id": "  "}, headers=_auth(token))
    assert r2.status_code == 200 and GENERATED.match(r2.json()["institution_id"])
    assert r2.json()["institution_id"] != body["institution_id"]


def test_own_ids_are_kept_and_checked(client: TestClient) -> None:
    token = get_super_admin_token(client)
    r = client.post("/api/v1/users/students/create", json=STUDENT_PAYLOAD, headers=_auth(token))
    assert r.status_code == 200 and r.json()["institution_id"] == STUDENT_PAYLOAD["institution_id"]
    assert r.json()["institution_id_generated"] is False
    # The same ID again in this organisation (any case) is refused...
    dup = {**STUDENT_PAYLOAD, "phone": "+1000000098", "institution_id": STUDENT_PAYLOAD["institution_id"].lower()}
    r2 = client.post("/api/v1/users/students/create", json=dup, headers=_auth(token))
    assert r2.status_code == 400 and "already exists" in r2.json()["detail"]
    # ...and so is the shape Juwazi keeps for its own.
    taken = {**STUDENT_PAYLOAD, "phone": "+1000000097", "institution_id": "jw-7k3m9q2x"}
    r3 = client.post("/api/v1/users/students/create", json=taken, headers=_auth(token))
    assert r3.status_code == 400 and "Juwazi creates" in r3.json()["detail"]


def test_generated_ids_never_repeat_anywhere(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    other_org, user = uuid4(), uuid4()
    with Session(engine) as s:
        s.add(OrgMembership(user_id=user, org_id=other_org, role=UserRole.STUDENT,
                            institution_id="JW-AAAAAAAA", institution_id_generated=True))
        s.commit()
    picks = iter("A" * 8 + "B" * 8)            # first try is taken in another organisation
    monkeypatch.setattr(institution_ids.secrets, "choice", lambda _: next(picks))
    with Session(engine) as s:
        assert institution_ids.generate(s) == "JW-BBBBBBBB"
    # And the database refuses a second student with the same generated ID.
    with Session(engine) as s:
        s.add(OrgMembership(user_id=uuid4(), org_id=uuid4(), role=UserRole.STUDENT,
                            institution_id="JW-AAAAAAAA", institution_id_generated=True))
        with pytest.raises(IntegrityError):
            s.commit()


def test_upload_check_then_generate_for_blank_rows(client: TestClient) -> None:
    token = get_super_admin_token(client)
    rows = [
        {"firstname": "Ada", "lastname": "Obi", "institution_id": "REG/1"},
        {"firstname": "Bayo", "lastname": "Ade"},
        {"firstname": "Chi", "lastname": "Eze", "institution_id": ""},
    ]
    files = {"file": ("students.xlsx", create_student_excel(rows).getvalue(), XLSX)}
    check = client.post("/api/v1/users/students/create/bulk/check", files=files, headers=_auth(token))
    assert check.status_code == 200, check.json()
    assert check.json() == {"total_rows": 3, "missing_institution_id_rows": [3, 4]}

    files = {"file": ("students.xlsx", create_student_excel(rows).getvalue(), XLSX)}
    r = client.post("/api/v1/users/students/create/bulk", files=files, headers=_auth(token))
    assert r.status_code == 200, r.json()
    data = r.json()
    assert data["successful_rows"] == 3 and data["failed_rows"] == 0
    ids = {s["firstname"]: (s["institution_id"], s["institution_id_generated"]) for s in data["students"]}
    assert ids["Ada"] == ("REG/1", False)
    assert GENERATED.match(ids["Bayo"][0]) and ids["Bayo"][1] is True
    assert GENERATED.match(ids["Chi"][0]) and ids["Bayo"][0] != ids["Chi"][0]


def test_editing_a_students_id_saves_it(client: TestClient) -> None:
    token = get_super_admin_token(client)
    created = client.post("/api/v1/users/students/create", json=STUDENT_PAYLOAD, headers=_auth(token)).json()
    r = client.patch(f"/api/v1/users/{created['id']}", json={"institution_id": "STU/2024/777"}, headers=_auth(token))
    assert r.status_code == 200, r.json()
    m = _membership(created["id"])
    assert m.institution_id == "STU/2024/777" and m.institution_id_generated is False

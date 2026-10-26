"""One person in several organisations: invite an existing account, list, switch, refresh, sign in again."""
from typing import Any

import jwt

from tests.conftest import SIGNUP_PAYLOAD, USER_EMAIL, do_full_signup, do_request_otp, do_signup, do_verify_otp

V1 = "/api/v1"
PASSWORD = SIGNUP_PAYLOAD["user"]["password"]
SECOND_OWNER = "owner2@second.edu"
SECOND_ORG_PAYLOAD: dict[str, Any] = {
    "organization": {**SIGNUP_PAYLOAD["organization"], "name": "Second School", "slug": "second-school",
                     "email": "office@second.edu", "phone": "+2348000000002"},
    "user": {**SIGNUP_PAYLOAD["user"], "firstname": "Bola", "email": SECOND_OWNER, "phone": "+1848000002"},
}
INVITE = {"firstname": "John", "lastname": "Doe", "email": USER_EMAIL, "phone": "+1848234593", "role": "teacher"}


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def claims(token: str) -> dict[str, Any]:
    return jwt.decode(token, options={"verify_signature": False})


def login(client, email: str = USER_EMAIL) -> dict[str, Any]:
    r = client.post(f"{V1}/auth/login", data={"username": email, "password": PASSWORD})
    assert r.status_code == 200, r.json()
    return r.json()


def two_orgs(client) -> tuple[str, str, str]:
    """John owns org A and is invited to org B as a teacher. Returns (john_token, org_a, org_b)."""
    john = do_full_signup(client)["token"]["access_token"]
    do_signup(client, SECOND_ORG_PAYLOAD)
    otp = do_request_otp(client, SECOND_OWNER)
    second = do_verify_otp(client, SECOND_OWNER, otp)["token"]["access_token"]
    r = client.post(f"{V1}/users/staff/create", json=INVITE, headers=auth(second))
    assert r.status_code == 200, r.json()
    assert r.json()["is_existing_user"] is True
    return john, claims(john)["org_id"], claims(second)["org_id"]


def test_existing_account_is_added_straight_away(client) -> None:
    john, org_a, org_b = two_orgs(client)
    r = client.get(f"{V1}/auth/organizations", headers=auth(john))
    assert r.status_code == 200, r.json()
    orgs = {o["org_id"]: o for o in r.json()}
    assert set(orgs) == {org_a, org_b}
    assert orgs[org_a]["is_current"] and orgs[org_a]["role_name"] == "Owner"
    assert not orgs[org_b]["is_current"] and orgs[org_b]["role_name"] == "Teacher"
    assert orgs[org_b]["name"] == "Second School"


def test_switching_issues_tokens_for_the_other_organisation(client) -> None:
    john, org_a, org_b = two_orgs(client)
    session = login(client)
    assert claims(session["access_token"])["org_id"] == org_a

    r = client.post(f"{V1}/auth/switch-organization",
                    json={"org_id": org_b, "refresh_token": session["refresh_token"]},
                    headers=auth(session["access_token"]))
    assert r.status_code == 200, r.json()
    switched = r.json()
    assert switched["organization"]["id"] == org_b
    access = claims(switched["access_token"])
    assert access["org_id"] == org_b and access["role"] == "teacher"
    assert access["perms"]["questions.manage"] == "assigned"
    assert claims(switched["refresh_token"])["org_id"] == org_b

    me = client.get(f"{V1}/roles/me", headers=auth(switched["access_token"])).json()
    assert me["role_name"] == "Teacher"
    # The old tokens no longer work.
    assert client.get(f"{V1}/roles/me", headers=auth(session["access_token"])).status_code == 401
    old = client.post(f"{V1}/auth/token/refresh", json={"refresh_token": session["refresh_token"]})
    assert old.status_code == 401

    # Refreshing stays in the organisation they switched to.
    r = client.post(f"{V1}/auth/token/refresh", json={"refresh_token": switched["refresh_token"]})
    assert r.status_code == 200, r.json()
    assert claims(r.json()["access_token"])["org_id"] == org_b

    # Signing in again lands where they left off.
    again = login(client)
    assert again["organization"]["id"] == org_b
    assert claims(again["access_token"])["org_id"] == org_b


def test_cannot_switch_into_an_organisation_you_are_not_in(client) -> None:
    john, org_a, org_b = two_orgs(client)
    second = login(client, SECOND_OWNER)["access_token"]
    r = client.post(f"{V1}/auth/switch-organization", json={"org_id": org_a}, headers=auth(second))
    assert r.status_code == 403


def test_inviting_an_existing_account_twice_is_rejected(client) -> None:
    two_orgs(client)
    second = login(client, SECOND_OWNER)["access_token"]
    r = client.post(f"{V1}/users/staff/create", json=INVITE, headers=auth(second))
    assert r.status_code == 409

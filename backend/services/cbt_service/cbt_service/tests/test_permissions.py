# cbt_service/tests/test_permissions.py
"""The permission catalog and the built-in roles' defaults."""
from cbt_service.core.permissions import (
    CATALOG, DEFAULT_ROLE_GRANTS, P, decode, encode, expand, grants_for_role,
)


def test_every_permission_is_described() -> None:
    assert set(CATALOG) == set(P)


def test_super_admin_has_everything_at_all() -> None:
    grants = grants_for_role("super_admin")
    assert set(grants) == set(P) and set(grants.values()) == {"all"}


def test_students_only_get_candidate_permissions_and_unknown_roles_nothing() -> None:
    assert set(grants_for_role("student")) == {P.EXAM_ACCESS_VIEW, P.EXAM_ACCESS_PAY}
    assert grants_for_role("staff") == {}
    assert grants_for_role("something_else") == {}


def test_scoped_reach_only_on_scoped_permissions() -> None:
    for role, grants in DEFAULT_ROLE_GRANTS.items():
        for perm, reach in grants.items():
            if reach != "all":
                assert CATALOG[perm].scoped, (role, perm)


def test_implied_permissions_take_the_widest_reach() -> None:
    grants = expand({P.QUESTIONS_MANAGE: "assigned", P.SUBJECTS_MANAGE: "all"})
    assert grants[P.QUESTIONS_VIEW] == "assigned"
    assert grants[P.SUBJECTS_VIEW] == "all"


def test_token_round_trip_drops_unknown_values() -> None:
    grants = grants_for_role("teacher")
    assert decode(encode(grants)) == grants
    assert decode({"exams.view": "all", "made.up": "all", "exams.edit": "everywhere"}) == {P.EXAMS_VIEW: "all"}
    assert decode(None) is None


def test_creating_exams_only_implies_seeing_and_editing_your_own() -> None:
    grants = expand({P.EXAMS_CREATE: "all"})
    assert grants[P.EXAMS_VIEW] == "own" and grants[P.EXAMS_EDIT] == "own"
    # A teacher's explicit reach wins over the narrower implied one.
    assert grants_for_role("teacher")[P.EXAMS_VIEW] == "assigned"

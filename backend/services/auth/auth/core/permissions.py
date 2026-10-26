"""
Permissions: what staff can do, and how far it reaches.

The catalog below is fixed by the app (each permission must be enforced in
code). Roles are bundles of permissions. Until organisations can edit roles,
every role gets DEFAULT_ROLE_GRANTS, which reproduce today's hard-coded rules
exactly, so switching a route from require_roles(...) to
require_permission(...) changes nothing.

Reach says which records a grant covers:
- "all":      every record in the organisation
- "assigned": records in subjects (or cohorts) the person is assigned to,
              plus exams they created
- "own":      only exams the person created

Students are not part of this: they take exams and keep their own checks.
Super admins always hold every permission at "all" and can't be edited.

This file is duplicated in the cbt service (cbt_service/core/permissions.py);
keep the two identical.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Literal

Reach = Literal["all", "assigned", "own"]
REACH_ORDER: dict[Reach, int] = {"own": 0, "assigned": 1, "all": 2}


class P(str, Enum):
    """Permission keys. Stored in roles and tokens, so never rename a value."""
    # Subjects & questions
    SUBJECTS_VIEW = "subjects.view"
    SUBJECTS_MANAGE = "subjects.manage"
    SUBJECTS_ASSIGN_STAFF = "subjects.assign_staff"
    QUESTIONS_VIEW = "questions.view"
    QUESTIONS_MANAGE = "questions.manage"
    BANKS_TOPICS_MANAGE = "banks_topics.manage"
    # Exams
    EXAMS_VIEW = "exams.view"
    EXAMS_CREATE = "exams.create"
    EXAMS_EDIT = "exams.edit"
    EXAMS_SUBMIT = "exams.submit"
    EXAMS_APPROVE = "exams.approve"
    EXAMS_ASSIGN = "exams.assign"
    EXAMS_DELETE = "exams.delete"
    EXAMS_OVERRIDE_STATUS = "exams.override_status"
    EXAMS_AUDIT_LOG = "exams.audit_log"
    # Results & monitoring
    RESULTS_VIEW = "results.view"
    RESULTS_EXPORT = "results.export"
    RESULTS_MARK = "results.mark"
    VIOLATIONS_VIEW = "violations.view"
    VIOLATIONS_REVIEW = "violations.review"
    ATTEMPTS_RESET = "attempts.reset"
    # People
    CANDIDATES_VIEW = "candidates.view"
    CANDIDATES_MANAGE = "candidates.manage"
    COHORTS_VIEW = "cohorts.view"
    COHORTS_MANAGE = "cohorts.manage"
    STAFF_VIEW = "staff.view"
    STAFF_MANAGE = "staff.manage"
    ROLES_MANAGE = "roles.manage"
    # Organisation
    SETTINGS_MANAGE = "settings.manage"
    BILLING_VIEW = "billing.view"
    BILLING_PAY_EXAM_ACCESS = "billing.pay_exam_access"
    BILLING_MANAGE = "billing.manage"
    # Candidates (what students can do for themselves)
    EXAM_ACCESS_VIEW = "exam_access.view"
    EXAM_ACCESS_PAY = "exam_access.pay"


@dataclass(frozen=True)
class PermissionInfo:
    group: str
    label: str
    description: str
    scoped: bool = False                 # supports "assigned"/"own" reach
    # Granting this also grants these: at the same reach, or at a fixed reach
    # when given as (permission, reach), e.g. creating exams lets you see your own.
    implies: tuple["P | tuple[P, Reach]", ...] = ()


CATALOG: dict[P, PermissionInfo] = {
    P.SUBJECTS_VIEW: PermissionInfo("Subjects & questions", "View subjects", "See subjects and their details.", scoped=True),
    P.SUBJECTS_MANAGE: PermissionInfo("Subjects & questions", "Manage subjects", "Create, rename and archive subjects.", implies=(P.SUBJECTS_VIEW,)),
    P.SUBJECTS_ASSIGN_STAFF: PermissionInfo("Subjects & questions", "Assign staff to subjects", "Choose which staff work on each subject.", implies=(P.SUBJECTS_VIEW,)),
    P.QUESTIONS_VIEW: PermissionInfo("Subjects & questions", "View questions", "Browse questions, banks and topics.", scoped=True, implies=(P.SUBJECTS_VIEW,)),
    P.QUESTIONS_MANAGE: PermissionInfo("Subjects & questions", "Write questions", "Add, edit, archive and bulk-upload questions.", scoped=True, implies=(P.QUESTIONS_VIEW,)),
    P.BANKS_TOPICS_MANAGE: PermissionInfo("Subjects & questions", "Manage banks & topics", "Create and organise a subject's banks and topics.", scoped=True, implies=(P.QUESTIONS_VIEW,)),

    P.EXAMS_VIEW: PermissionInfo("Exams", "View exams", "See exams and their questions. Drafts are only visible to people who can edit exams.", scoped=True),
    P.EXAMS_CREATE: PermissionInfo("Exams", "Create exams", "Start new exams.", implies=((P.EXAMS_VIEW, "own"), (P.EXAMS_EDIT, "own"))),
    P.EXAMS_EDIT: PermissionInfo("Exams", "Edit exams", "Change details, sections and questions while an exam is a draft or sent back.", scoped=True, implies=(P.EXAMS_VIEW,)),
    P.EXAMS_SUBMIT: PermissionInfo("Exams", "Submit for approval", "Send a finished draft for approval.", scoped=True, implies=(P.EXAMS_VIEW,)),
    P.EXAMS_APPROVE: PermissionInfo("Exams", "Approve exams", "Approve or send back submitted exams.", scoped=True, implies=(P.EXAMS_VIEW,)),
    P.EXAMS_ASSIGN: PermissionInfo("Exams", "Assign candidates", "Assign candidates or groups to an exam and see who is assigned.", scoped=True, implies=(P.EXAMS_VIEW,)),
    P.EXAMS_DELETE: PermissionInfo("Exams", "Delete exams", "Delete exams.", implies=(P.EXAMS_VIEW,)),
    P.EXAMS_OVERRIDE_STATUS: PermissionInfo("Exams", "Override exam status", "Force an exam into any status, skipping the approval flow.", implies=(P.EXAMS_VIEW,)),
    P.EXAMS_AUDIT_LOG: PermissionInfo("Exams", "View exam activity", "See the activity log of an exam.", implies=(P.EXAMS_VIEW,)),

    P.RESULTS_VIEW: PermissionInfo("Results", "View results", "See attempts, scores and answers.", scoped=True),
    P.RESULTS_EXPORT: PermissionInfo("Results", "Export results", "Download results to Excel.", scoped=True, implies=(P.RESULTS_VIEW,)),
    P.RESULTS_MARK: PermissionInfo("Results", "Mark answers", "Mark answers that need a person to check them.", scoped=True, implies=(P.RESULTS_VIEW,)),
    P.VIOLATIONS_VIEW: PermissionInfo("Results", "View violations", "See proctoring violations (tab switches, etc.).", scoped=True),
    P.VIOLATIONS_REVIEW: PermissionInfo("Results", "Review violations", "Mark proctoring violations as reviewed.", scoped=True, implies=(P.VIOLATIONS_VIEW,)),
    P.ATTEMPTS_RESET: PermissionInfo("Results", "Reset attempts", "Let a candidate start an exam again.", implies=(P.RESULTS_VIEW,)),

    P.CANDIDATES_VIEW: PermissionInfo("People", "View candidates", "See the list of candidates."),
    P.CANDIDATES_MANAGE: PermissionInfo("People", "Manage candidates", "Add, edit, move, export candidates and reset their sign-in.", implies=(P.CANDIDATES_VIEW,)),
    P.COHORTS_VIEW: PermissionInfo("People", "View groups", "See groups (cohorts), their members and staff.", scoped=True),
    P.COHORTS_MANAGE: PermissionInfo("People", "Manage groups", "Create, edit, archive and graduate groups; manage members and staff.", implies=(P.COHORTS_VIEW,)),
    P.STAFF_VIEW: PermissionInfo("People", "View staff", "See the list of staff."),
    P.STAFF_MANAGE: PermissionInfo("People", "Manage staff", "Invite, edit and remove staff.", implies=(P.STAFF_VIEW,)),
    P.ROLES_MANAGE: PermissionInfo("People", "Manage roles", "Create roles and choose their permissions.", implies=(P.STAFF_VIEW,)),

    P.SETTINGS_MANAGE: PermissionInfo("Organisation", "Manage settings", "Change organisation settings, periods and labels."),
    P.BILLING_VIEW: PermissionInfo("Organisation", "View subscription", "See the plan and usage limits."),
    P.BILLING_PAY_EXAM_ACCESS: PermissionInfo("Organisation", "Pay for exam access", "Pay for candidates' access to exam bodies.", implies=(P.BILLING_VIEW,)),
    P.BILLING_MANAGE: PermissionInfo("Organisation", "Manage subscription", "Start a trial or change the plan.", implies=(P.BILLING_VIEW,)),

    P.EXAM_ACCESS_VIEW: PermissionInfo("Candidates", "View exam-body subscriptions", "See subscriptions for exams from external bodies (WAEC, GCE...)."),
    P.EXAM_ACCESS_PAY: PermissionInfo("Candidates", "Pay for exam-body subscriptions", "Pay for access to exams from external bodies.", implies=(P.EXAM_ACCESS_VIEW,)),
}

Grants = dict[P, Reach]

ALL: Grants = {p: "all" for p in P}

# Built-in roles, reproducing what each role can do today.
DEFAULT_ROLE_GRANTS: dict[str, Grants] = {
    "super_admin": ALL,
    "admin": {
        p: "all" for p in P
        if p not in (P.BILLING_MANAGE, P.ROLES_MANAGE, P.EXAM_ACCESS_VIEW, P.EXAM_ACCESS_PAY)
    },
    "teacher": {
        P.SUBJECTS_VIEW: "assigned",
        P.QUESTIONS_VIEW: "assigned",
        P.QUESTIONS_MANAGE: "assigned",
        P.BANKS_TOPICS_MANAGE: "assigned",
        P.EXAMS_VIEW: "assigned",
        P.EXAMS_CREATE: "all",
        P.EXAMS_EDIT: "assigned",
        P.EXAMS_SUBMIT: "own",
        P.EXAMS_ASSIGN: "assigned",
        P.RESULTS_VIEW: "assigned",
        P.RESULTS_EXPORT: "assigned",
        P.RESULTS_MARK: "assigned",
        P.VIOLATIONS_REVIEW: "assigned",
        P.COHORTS_VIEW: "assigned",
    },
    "supervisor": {
        P.EXAMS_VIEW: "assigned",
        P.EXAMS_APPROVE: "assigned",
        P.VIOLATIONS_VIEW: "assigned",
        P.COHORTS_VIEW: "assigned",
    },
    # Staff start with nothing: an admin gives them a role with the permissions
    # they need, and "assigned" reach limits it to their subjects / groups.
    "staff": {},
    # Candidates only act for themselves.
    "student": {
        P.EXAM_ACCESS_VIEW: "all",
        P.EXAM_ACCESS_PAY: "all",
    },
}

# Permissions only candidates use; they never appear in staff roles by default.
CANDIDATE_PERMISSIONS: frozenset[P] = frozenset({P.EXAM_ACCESS_VIEW, P.EXAM_ACCESS_PAY})


def expand(grants: Grants) -> Grants:
    """Add implied permissions, each at the widest reach anything implies for it."""
    out: Grants = dict(grants)
    changed = True
    while changed:
        changed = False
        for perm, reach in list(out.items()):
            for entry in CATALOG[perm].implies:
                implied, implied_reach = entry if isinstance(entry, tuple) else (entry, reach)
                current = out.get(implied)
                if current is None or REACH_ORDER[implied_reach] > REACH_ORDER[current]:
                    out[implied] = implied_reach
                    changed = True
    return out


def grants_for_role(role: str) -> Grants:
    return expand(DEFAULT_ROLE_GRANTS.get(role, {}))


def reach_of(grants: Grants, perm: P) -> Reach | None:
    """The reach a person has for a permission, or None if they don't have it."""
    return grants.get(perm)


def encode(grants: Grants) -> dict[str, str]:
    """For the token: {"exams.view": "assigned", ...}."""
    return {p.value: r for p, r in grants.items()}


def decode(raw: object) -> Grants | None:
    """From the token; unknown keys or reaches are dropped. None if absent/invalid."""
    if not isinstance(raw, dict):
        return None
    valid: set[str] = {p.value for p in P}
    out: Grants = {}
    for key, reach in raw.items():
        if key in valid and reach in REACH_ORDER:
            out[P(key)] = reach
    return out

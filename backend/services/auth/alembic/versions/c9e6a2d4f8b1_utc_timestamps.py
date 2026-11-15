"""Date-time columns keep their time zone (timestamptz)

These were "timestamp without time zone". The app writes UTC times; Postgres
turned each into the connection's time zone (the server default) before
dropping the zone. So each stored value is read back in that same time zone
here, which gives the exact moment whatever the server's zone is (UTC, or
Africa/Lagos on a local Mac).

Revision ID: c9e6a2d4f8b1
Revises: b8d5f1c3e7a9
Create Date: 2026-11-15
"""
from typing import Sequence, Union

from alembic import op

revision: str = "c9e6a2d4f8b1"
down_revision: Union[str, Sequence[str], None] = "b8d5f1c3e7a9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUMNS: dict[str, list[str]] = {
    "organizations": ["created_at", "updated_at"],
    "organization_settings": ["updated_at"],
    "users": ["external_exams_seen_at", "created_at", "updated_at"],
    "org_memberships": ["archived_at", "created_at", "updated_at"],
    "cohorts": ["start_date", "end_date", "graduated_at", "created_at", "updated_at"],
    "cohort_members": ["created_at"],
    "teacher_cohort_assignments": ["assigned_at"],
    "exam_body_subscriptions": ["starts_at", "expires_at", "trial_ends_at", "created_at", "updated_at"],
    "bulk_org_subscriptions": ["created_at", "updated_at"],
    "platform_plans": ["created_at", "updated_at"],
    "org_platform_subscriptions": [
        "trial_ends_at", "current_period_start", "current_period_end", "cancelled_at", "created_at", "updated_at",
    ],
}


def _alter(to: str) -> None:
    for table, columns in COLUMNS.items():
        changes = ", ".join(
            f'ALTER COLUMN "{c}" TYPE {to} USING "{c}" AT TIME ZONE current_setting(\'TimeZone\')'
            for c in columns
        )
        op.execute(f'ALTER TABLE "{table}" {changes}')


def upgrade() -> None:
    _alter("TIMESTAMP WITH TIME ZONE")


def downgrade() -> None:
    _alter("TIMESTAMP WITHOUT TIME ZONE")

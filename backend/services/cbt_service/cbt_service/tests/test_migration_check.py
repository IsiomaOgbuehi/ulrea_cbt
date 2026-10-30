"""The startup check says when the database is behind the code's migrations."""
import logging

import pytest
from sqlalchemy import create_engine, text

from cbt_service.core.migration_check import check_migrations, pending_migrations


def test_database_behind_is_reported(caplog: pytest.LogCaptureFixture) -> None:
    engine = create_engine("sqlite://")
    with caplog.at_level(logging.ERROR):
        assert check_migrations(engine) is False
    assert "DATABASE NEEDS UPDATING" in caplog.text and "alembic upgrade head" in caplog.text


def test_database_up_to_date_passes() -> None:
    engine = create_engine("sqlite://")
    _, latest = pending_migrations(engine)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        for rev in latest:
            conn.execute(text("INSERT INTO alembic_version VALUES (:v)"), {"v": rev})
    assert check_migrations(engine) is True


def test_unreachable_database_only_warns() -> None:
    engine = create_engine("postgresql://u:p@127.0.0.1:1/db")
    assert check_migrations(engine) is False

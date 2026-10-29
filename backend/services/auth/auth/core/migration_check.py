"""
Startup check: is the database up to date with the code's migrations?

When a release adds a column, screens fail with "column ... does not exist"
until `alembic upgrade head` runs. This says so plainly in the log at startup
instead. It only reads the database; it never migrates.
"""
import logging
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.engine import Engine

logger: logging.Logger = logging.getLogger(__name__)

SERVICE_DIR: Path = Path(__file__).resolve().parents[2]   # where alembic.ini lives


def pending_migrations(engine: Engine) -> tuple[set[str], set[str]]:
    """(revisions the database is at, latest revisions in the code)."""
    script = ScriptDirectory.from_config(Config(str(SERVICE_DIR / "alembic.ini")))
    latest: set[str] = set(script.get_heads())
    with engine.connect() as conn:
        current: set[str] = set(MigrationContext.configure(conn).get_current_heads())
    return current, latest


def check_migrations(engine: Engine) -> bool:
    """Logs an error if the database is behind. Returns True when up to date."""
    try:
        current, latest = pending_migrations(engine)
    except Exception as exc:   # database down, alembic files missing, ...
        logger.warning("Couldn't check database migrations: %s", exc)
        return False
    if current == latest:
        logger.info("Database is up to date (migration %s).", ", ".join(sorted(latest)))
        return True
    logger.error(
        "DATABASE NEEDS UPDATING: it is at %s, the code expects %s. Screens will fail with "
        "'column does not exist' errors until you run `uv run alembic upgrade head` in %s "
        "and restart this service.",
        ", ".join(sorted(current)) or "no migrations", ", ".join(sorted(latest)), SERVICE_DIR,
    )
    return False

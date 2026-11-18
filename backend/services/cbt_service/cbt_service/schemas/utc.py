"""Times coming in from clients: always handled in UTC."""
from datetime import datetime, timezone
from typing import Annotated

from pydantic import AfterValidator


def as_utc(value: datetime) -> datetime:
    """A time without a zone is taken as UTC (as the web app sends and reads
    them); one with a zone is converted to UTC. Comparisons with stored times
    then never mix the two, and Postgres never reads it in its own zone."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


UTCDateTime = Annotated[datetime, AfterValidator(as_utc)]

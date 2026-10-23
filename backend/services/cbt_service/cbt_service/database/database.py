from typing import Annotated

import anyio
from fastapi import Depends
from sqlmodel import Session
from .database_interface import IDatabase
from .postgres_database import PostgresDatabase

# class DatabaseFactory:
#     def __init__(self, db: IDatabase):
#         self.database = db

#     def create_factory(self) -> IDatabase:
#         return self.database

class DatabaseFactory:
    def __init__(self, db: IDatabase) -> None:
        self.database: IDatabase = db

    def create_factory(self) -> IDatabase:
        return self.database


db = DatabaseFactory(PostgresDatabase())

database = db.create_factory()
database_engine = database.engine()

SessionDep = Annotated[Session, Depends(database.get_session)]


async def release_connection(session: Session) -> None:
    """
    Hand the session's DB connection back to the pool.

    Call this in async endpoints after the DB work is done and before awaiting
    something slow that doesn't need the DB (e.g. an auth-service HTTP call),
    so the connection isn't held idle while we wait.

    - Objects already loaded stay readable (models have no lazy relationships,
      and close() doesn't expire attributes).
    - The session is still usable afterwards; it checks out a fresh connection
      on the next query.
    - Uncommitted changes are discarded, so only call this after commit().
    - Runs with its own limiter (like FastAPI does for dependency teardown) so
      releasing a connection never waits behind threads that are themselves
      waiting for a connection.
    """
    await anyio.to_thread.run_sync(session.close, limiter=anyio.CapacityLimiter(1))

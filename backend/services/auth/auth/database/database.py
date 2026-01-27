from typing import Annotated

from fastapi import Depends
from sqlmodel import Session
from auth.database.database_interface import IDatabase
from auth.database.postgres_database import PostgresDatabase

class DatabaseFactory:
    def __init__(self, db: IDatabase):
        self.database = db

    def create_factory(self) -> IDatabase:
        return self.database


db = DatabaseFactory(PostgresDatabase())

database = db.create_factory()
database_engine = database.engine()

SessionDep = Annotated[Session, Depends(database.get_session)]
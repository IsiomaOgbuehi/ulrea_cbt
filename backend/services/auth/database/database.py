from typing import Annotated

from fastapi import Depends
from sqlmodel import Session
from database.database_interface import IDatabase
from database.postgres_database import PostgresDatabase

class DatabaseFactory:
    def __init__(self, db: IDatabase):
        self.database = db

    def create_factory(self) -> IDatabase:
        return self.database


db = DatabaseFactory(PostgresDatabase())
database = db.create_factory()

SessionDep = Annotated[Session, Depends(database.get_session)]
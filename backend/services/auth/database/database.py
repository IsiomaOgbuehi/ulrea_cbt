from typing import Annotated

from fastapi import Depends
from sqlmodel import Field, SQLModel, Session
from database.database_interface import IDatabase
from database.postgres_database import PostgresDatabase

# class Hero(SQLModel, table=True):
#     id: int | None = Field(default=None, primary_key=True)
#     name: str = Field(index=True)
#     age: int | None = Field(default=None, index=True)
#     secret_name: str

class DatabaseFactory:
    def __init__(self, db: IDatabase):
        self.database = db

    def create_factory(self) -> IDatabase:
        return self.database


db = DatabaseFactory(PostgresDatabase())

database = db.create_factory()
database_engine = database.engine()

SessionDep = Annotated[Session, Depends(database.get_session)]
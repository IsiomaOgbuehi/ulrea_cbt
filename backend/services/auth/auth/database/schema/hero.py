from pydantic import ConfigDict
from sqlmodel import SQLModel, Field

class HeroBase(SQLModel):
    name: str = Field(index=True)
    age: int | None = Field(default=None, index=True)
    secret_name: str

class HeroModel(HeroBase, table=True):
    __tablename__ = "hero"
    id: int | None = Field(default=None, primary_key=True)


# API Fields
class Hero(HeroBase):
    model_config = ConfigDict(extra="forbid")
    pass

class HeroRead(HeroBase):
    id: int
from pydantic import BaseModel

class User(BaseModel):
    # id: int
    username: str
    email: str | None = None
    firstname: str | None = None
    lastname: str | None = None
    phone: str | None = None
    password: str | None = None
    disabled: bool | None = None

class UserInDB(User):
    hashed_password: str
from datetime import datetime, timezone
from uuid import UUID, uuid4
from pydantic import EmailStr
from sqlmodel import SQLModel, Field
from .enums import MembershipStatus, UserRole, VerificationMethod

class UserBase(SQLModel):
    firstname: str = Field(index=True)
    lastname: str
    othername: str | None = ''
    email: EmailStr | None = Field(default=None, index=True, unique=True, nullable=True)  # optional for students
    phone: str | None = None
    password: str | None = None # None for students initially
    verified: bool = Field(default=False)
    verification_method: VerificationMethod | None = Field(default=None)   # VerificationMethod
    is_first_login: bool = Field(default=True)  # triggers forced setup flow
    
    # Student-specific
    access_code: str | None = Field(default=None, index=True, unique=True) # 6-char alphanumeric
    favorite_question: str | None = None
    favorite_answer_hash: str | None = None                   # hashed like password




class UserModel(UserBase, table=True):
    """Global identity — one record per person across all orgs."""
    __tablename__ = 'users'
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    created_at: datetime | None = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        index=True,
        nullable=False,
    )
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), nullable=False,)
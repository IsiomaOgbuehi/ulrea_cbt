"""
Students' institution IDs (registration / admission numbers).

A school's own IDs are unique within that school. When a student is added
without one, Juwazi generates "JW-" + 8 characters (Crockford base32: no I, L,
O or U, so they're easy to read out). Generated IDs are unique across every
organisation: checked here, and guaranteed by a unique database index over
generated ones. Schools can't use that shape themselves, so their IDs never
clash with a generated one.
"""
import re
import secrets

from fastapi import HTTPException
from sqlmodel import Session, func, select

from auth.database.schema.membership.membership_db import OrgMembership

GENERATED_PREFIX: str = "JW-"
_ALPHABET: str = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_LENGTH: int = 8
_GENERATED_SHAPE = re.compile(r"^JW-[0-9A-Z]{8}$", re.IGNORECASE)
MAX_LENGTH: int = 64


def looks_generated(value: str) -> bool:
    return bool(_GENERATED_SHAPE.match(value.strip()))


def clean(value: str | None) -> str | None:
    """A school's own ID, tidied; None when blank. Refuses the generated shape."""
    text = (value or "").strip()
    if not text:
        return None
    if len(text) > MAX_LENGTH:
        raise HTTPException(status_code=400, detail=f"Institution IDs can be at most {MAX_LENGTH} characters.")
    if looks_generated(text):
        raise HTTPException(
            status_code=400,
            detail=(
                f"IDs like '{GENERATED_PREFIX}XXXXXXXX' are kept for the ones Juwazi creates. "
                "Use your own ID, or leave it blank and Juwazi will create one."
            ),
        )
    return text


def generate(session: Session) -> str:
    """A new ID no student in any organisation has."""
    for _ in range(25):
        candidate = GENERATED_PREFIX + "".join(secrets.choice(_ALPHABET) for _ in range(_LENGTH))
        taken = session.exec(
            select(OrgMembership.id).where(func.upper(OrgMembership.institution_id) == candidate)
        ).first()
        if taken is None:
            return candidate
    raise HTTPException(status_code=500, detail="Couldn't create an institution ID. Please try again.")

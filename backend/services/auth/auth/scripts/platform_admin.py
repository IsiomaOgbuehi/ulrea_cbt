"""
Make someone a Juwazi platform admin (or remove it). Run on the server:

    uv run python -m auth.scripts.platform_admin grant person@juwazi.com
    uv run python -m auth.scripts.platform_admin revoke person@juwazi.com
    uv run python -m auth.scripts.platform_admin list

There's deliberately no API for this.
"""
import sys

from sqlmodel import Session, col, select

from auth.database.database import database_engine
from auth.database.schema.user.user_db import UserModel


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("grant", "revoke", "list") or (argv[0] != "list" and len(argv) != 2):
        print(__doc__)
        return 2
    with Session(database_engine) as session:
        if argv[0] == "list":
            for u in session.exec(select(UserModel).where(col(UserModel.is_platform_admin).is_(True))).all():
                print(f"{u.email}  {u.firstname} {u.lastname}")
            return 0
        email = argv[1].strip().lower()
        user = session.exec(select(UserModel).where(UserModel.email == email)).first()
        if user is None:
            print(f"No account with email {email}.")
            return 1
        user.is_platform_admin = argv[0] == "grant"
        session.add(user)
        session.commit()
        print(f"{email} is {'now' if user.is_platform_admin else 'no longer'} a platform admin.")
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

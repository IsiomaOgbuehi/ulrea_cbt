"""Paging for list endpoints: one count query plus one page query."""
from typing import Any

from sqlalchemy import func
from sqlmodel import Session, select
from sqlmodel.sql.expression import SelectOfScalar, Select

DEFAULT_PAGE_SIZE: int = 20
MAX_PAGE_SIZE: int = 200


def page_of(
    session: Session, stmt: "SelectOfScalar[Any] | Select[Any]", page: int, page_size: int,
) -> tuple[list[Any], int]:
    """(rows on this page, total rows). stmt carries its own ordering."""
    total: int = int(session.exec(select(func.count()).select_from(stmt.order_by(None).subquery())).one())
    rows = session.exec(stmt.offset((page - 1) * page_size).limit(page_size)).all()
    return list(rows), total


def total_pages(total: int, page_size: int) -> int:
    return max(1, -(-total // page_size))

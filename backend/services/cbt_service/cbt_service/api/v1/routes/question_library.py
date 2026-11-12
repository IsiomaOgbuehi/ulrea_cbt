"""Question library: organisations browse the question banks exam bodies share
and copy questions (or whole banks) into their own subjects, optionally
straight into an exam. Free on a trial or a paid plan."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from starlette.concurrency import run_in_threadpool

from cbt_service.clients.auth_client import AuthClient
from cbt_service.core.permissions import P
from cbt_service.core.redis.redis_client import redis_client
from cbt_service.database.database import SessionDep, release_connection
from cbt_service.dependencies import require_permission
from cbt_service.schemas.current_user import CurrentUser
from cbt_service.schemas.question_library_schemas import (
    LibraryCopyRequest, LibraryCopyResult, LibraryItemPage, LibraryRead, PoolSubjectRead,
)
from cbt_service.services.paging import MAX_PAGE_SIZE
from cbt_service.services.question_pool_service import QuestionPoolService

router = APIRouter(prefix="/question-library", tags=["question library"])


async def _require_plan(org_id: UUID) -> None:
    allowed, reason = await AuthClient(redis_client).question_library_allowed(org_id)
    if not allowed:
        raise HTTPException(status_code=403, detail=reason or "Your plan doesn't include questions from exam bodies.")


async def _require_body(body_id: UUID, org_id: UUID) -> None:
    bodies = await AuthClient(redis_client).exam_bodies()
    if body_id not in bodies or body_id == org_id:
        raise HTTPException(status_code=404, detail="Exam body not found.")


@router.get("", response_model=LibraryRead)
async def library(
    session: SessionDep, current_user: CurrentUser = Depends(require_permission(P.QUESTIONS_VIEW)),
) -> LibraryRead:
    """Exam bodies sharing questions, and whether this organisation's plan includes them."""
    await release_connection(session)
    client = AuthClient(redis_client)
    allowed, reason = await client.question_library_allowed(current_user.org_id)
    bodies = await client.exam_bodies()
    rows = await run_in_threadpool(QuestionPoolService.library_bodies, session, bodies, current_user.org_id)
    return LibraryRead(allowed=allowed, reason=reason, bodies=[b for b in rows if b.question_count > 0])


@router.get("/bodies/{body_id}/subjects", response_model=list[PoolSubjectRead])
async def body_subjects(
    body_id: UUID, session: SessionDep, current_user: CurrentUser = Depends(require_permission(P.QUESTIONS_VIEW)),
) -> list[PoolSubjectRead]:
    await release_connection(session)
    await _require_plan(current_user.org_id)
    await _require_body(body_id, current_user.org_id)
    return await run_in_threadpool(lambda: QuestionPoolService.subjects(session, body_id, library=True))


@router.get("/bodies/{body_id}/items", response_model=LibraryItemPage)
async def body_items(
    body_id: UUID,
    session: SessionDep,
    subject_id: UUID | None = None,
    topic_id: UUID | None = None,
    bank_id: UUID | None = None,
    q: str | None = Query(default=None, max_length=200),
    into_subject_id: UUID | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
    current_user: CurrentUser = Depends(require_permission(P.QUESTIONS_VIEW)),
) -> LibraryItemPage:
    """A page of an exam body's shared questions, with answers (for picking).
    `into_subject_id` marks the ones already copied into that subject."""
    await release_connection(session)
    await _require_plan(current_user.org_id)
    await _require_body(body_id, current_user.org_id)
    return await run_in_threadpool(
        lambda: QuestionPoolService.library_items(
            session, body_id, subject_id=subject_id, topic_id=topic_id, bank_id=bank_id, search=q, page=page,
            page_size=page_size, into_subject_id=into_subject_id, org_id=current_user.org_id,
        )
    )


@router.post("/copy", response_model=LibraryCopyResult)
async def copy_questions(
    payload: LibraryCopyRequest,
    session: SessionDep,
    current_user: CurrentUser = Depends(require_permission(P.QUESTIONS_MANAGE)),
) -> LibraryCopyResult:
    """Copy chosen questions, or a whole shared bank, into one of the
    organisation's subjects (skipping ones already copied there); with
    exam_id, into the exam's subject and straight into the exam."""
    await release_connection(session)
    await _require_plan(current_user.org_id)
    await _require_body(payload.exam_body_org_id, current_user.org_id)
    return await run_in_threadpool(QuestionPoolService.copy, session, payload, current_user)

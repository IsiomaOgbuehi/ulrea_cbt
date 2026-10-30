from uuid import UUID
from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.concurrency import run_in_threadpool

from cbt_service.database.database import SessionDep
from cbt_service.services.item.subject_service import SubjectService
from cbt_service.core.settings import settings
from cbt_service.database.models.exam import ExamAssignment
from sqlmodel import Session, func, select

router = APIRouter(prefix="/internal", tags=["internal"])


def verify_internal_secret(x_internal_secret: str = Header(...)):
    if x_internal_secret != settings.INTERNAL_SECRET:
        raise HTTPException(status_code=403, detail="Invalid internal service credentials.")


@router.get(
    "/subjects/{subject_id}/assigned-user-ids",
    response_model=list[UUID],
    dependencies=[Depends(verify_internal_secret)],
)
async def get_assigned_user_ids(
    subject_id: UUID,
    org_id: UUID,
    session: SessionDep,
) -> list[UUID]:
    user_ids: list[UUID] = await run_in_threadpool(SubjectService.get_assigned_user_ids, session=session, subject_id=subject_id, org_id=org_id)
    return user_ids


def _cohort_assignment_count(session: Session, cohort_id: UUID, org_id: UUID) -> int:
    return int(session.exec(
        select(func.count()).select_from(ExamAssignment).where(
            ExamAssignment.cohort_id == cohort_id, ExamAssignment.org_id == org_id,
        )
    ).one())


@router.get(
    "/cohorts/{cohort_id}/exam-assignment-count",
    response_model=int,
    dependencies=[Depends(verify_internal_secret)],
)
async def cohort_exam_assignment_count(cohort_id: UUID, org_id: UUID, session: SessionDep) -> int:
    """How many exam assignments came through this cohort (0 = it was never given an exam)."""
    return await run_in_threadpool(_cohort_assignment_count, session, cohort_id, org_id)

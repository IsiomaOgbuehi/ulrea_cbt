"""External Exams: subscribers practising exam bodies' exams."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from cbt_service.clients.auth_client import AuthClient
from cbt_service.core.permissions import P
from cbt_service.core.redis.redis_client import redis_client
from cbt_service.database.database import SessionDep, release_connection
from cbt_service.dependencies import StudentOnly, require_permission
from cbt_service.schemas.attempt_schemas import AttemptRead
from cbt_service.schemas.current_user import CurrentUser
from cbt_service.schemas.practice_schemas import PracticeCatalogue, PracticeStatsRead
from cbt_service.schemas.question_library_schemas import (
    PoolSubjectRead, TopicTestRead, TopicTestRequest, TopicTestResult, TopicTestScoreRequest,
)
from cbt_service.services.question_pool_service import QuestionPoolService
from cbt_service.services.attempt_service import AttemptService
from cbt_service.services.practice_service import PracticeService
from cbt_service.services.exam_service import ExamService, _log

router = APIRouter(tags=["external exams"])


@router.get("/external/bodies/{body_id}/exams", response_model=PracticeCatalogue)
async def body_exams(body_id: UUID, session: SessionDep, current_user: CurrentUser = StudentOnly) -> PracticeCatalogue:
    """An exam body's practice exams, with the student's progress. Listed for
    everyone; taking them needs a subscription."""
    public, subscribed = await AuthClient(redis_client).exam_access(current_user.id, body_id)
    if not public:
        raise HTTPException(status_code=404, detail="Exam body not found.")
    exams = await run_in_threadpool(PracticeService.catalogue, session, body_id, current_user.id)
    return PracticeCatalogue(subscribed=subscribed, expires_at=None, exams=exams)


@router.post("/external/exams/{exam_id}/start", response_model=AttemptRead)
async def start_practice(exam_id: UUID, session: SessionDep, current_user: CurrentUser = StudentOnly) -> AttemptRead:
    """Start (or resume) practising an exam. A retake replaces the previous practice answers."""
    exam = await run_in_threadpool(PracticeService.listed_exam, session, exam_id)
    await release_connection(session)            # don't hold a connection while asking auth
    public, subscribed = await AuthClient(redis_client).exam_access(current_user.id, exam.org_id)
    if not public:
        raise HTTPException(status_code=404, detail="Exam not found.")
    if not subscribed:
        raise HTTPException(status_code=403, detail="Subscribe to this exam body to practise its exams.")
    attempt = await run_in_threadpool(PracticeService.start, session, exam_id, current_user.id)
    return await run_in_threadpool(AttemptService._to_attempt_read, session, attempt, True)


async def _require_subscribed(body_id: UUID, student_id: UUID) -> None:
    public, subscribed = await AuthClient(redis_client).exam_access(student_id, body_id)
    if not public:
        raise HTTPException(status_code=404, detail="Exam body not found.")
    if not subscribed:
        raise HTTPException(status_code=403, detail="Subscribe to this exam body to practise its questions.")


@router.get("/external/bodies/{body_id}/topics", response_model=list[PoolSubjectRead])
async def body_topics(body_id: UUID, session: SessionDep, current_user: CurrentUser = StudentOnly) -> list[PoolSubjectRead]:
    """Subjects and topics a subscriber can build a topic test from (questions marked automatically)."""
    await release_connection(session)
    await _require_subscribed(body_id, current_user.id)
    return await run_in_threadpool(QuestionPoolService.subjects, session, body_id, True)


@router.post("/external/bodies/{body_id}/topic-test", response_model=TopicTestRead)
async def topic_test(
    body_id: UUID, payload: TopicTestRequest, session: SessionDep, current_user: CurrentUser = StudentOnly,
) -> TopicTestRead:
    """A new topic test: random questions from the chosen topics, without answers.
    Nothing is stored; the student's device keeps it until it is marked."""
    await release_connection(session)
    await _require_subscribed(body_id, current_user.id)
    return await run_in_threadpool(QuestionPoolService.draw, session, body_id, payload)


@router.post("/external/bodies/{body_id}/topic-test/score", response_model=TopicTestResult)
async def score_topic_test(
    body_id: UUID, payload: TopicTestScoreRequest, session: SessionDep, current_user: CurrentUser = StudentOnly,
) -> TopicTestResult:
    """Mark a topic test: the score, the right answers and explanations. Nothing is stored."""
    await release_connection(session)
    await _require_subscribed(body_id, current_user.id)
    return await run_in_threadpool(QuestionPoolService.score, session, body_id, payload)


@router.get("/exams/{exam_id}/practice-stats", response_model=PracticeStatsRead)
def practice_stats(
    exam_id: UUID, session: SessionDep, current_user: CurrentUser = Depends(require_permission(P.RESULTS_VIEW)),
) -> PracticeStatsRead:
    """For the exam body: how much its exam is practised (anonymous totals)."""
    return PracticeService.stats(session, exam_id, current_user)


class ExternalListingUpdate(BaseModel):
    listed: bool


@router.patch("/exams/{exam_id}/external", response_model=ExternalListingUpdate)
def set_external_listing(
    exam_id: UUID, payload: ExternalListingUpdate, session: SessionDep,
    current_user: CurrentUser = Depends(require_permission(P.EXAMS_EDIT)),
) -> ExternalListingUpdate:
    """Offer an exam (or stop offering it) in students' External Exams. Any
    status: the catalogue only shows approved or live exams of exam bodies."""
    exam = ExamService._get_exam(session, exam_id, current_user.org_id)
    ExamService._assert_can_view(session, exam, current_user, P.EXAMS_EDIT)
    exam.external_listed = payload.listed
    session.add(exam)
    _log(session, exam.id, exam.org_id, current_user.id, "external_listing", {"listed": payload.listed})
    session.commit()
    return ExternalListingUpdate(listed=exam.external_listed)

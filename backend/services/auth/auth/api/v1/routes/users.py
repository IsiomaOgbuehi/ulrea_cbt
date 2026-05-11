import asyncio
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import select

from auth.database.schema.user.enums import UserRole
from auth.database.schema.user.user_db import UserModel
from auth.dependencies.auth_dependencies import get_current_user
from auth.database.database import SessionDep
from auth.api_models.user_api_models import CreateStaffUser, CreateStudent, StaffActivationPayload, StaffCreatedResponse, StaffFirstLoginSetup, StudentCreatedResponse, StudentFirstLoginSetup, StudentLoginRequest, StudentLoginResponse, StudentLoginUserResponse, UserRead, StudentAccessCodeRequest
from auth.services.user.user_management_service import UserManagementService
from auth.utility.email.email_service import EmailService
from auth.api.v1.routes.auth import IS_DEV
from auth.utility.password.password_harsher import PasswordHasher
from auth.utility.jwt.jwt import create_access_token, create_refresh_token
from auth.api.v1.auth_routes import AuthRoutes
from auth.utility.jwt.token_activation import create_staff_activation_token, verify_staff_activation_token
from auth.core.settings import settings
from auth.api_models.login_response import StudentFirstLoginResponse, StaffActivateResponse, StaffFirstLoginResponse


router = APIRouter()

router = APIRouter(
    prefix="/users",
    tags=["users"],
    responses={401: {"message": "Unauthorized"}}
)


def require_roles(*roles: UserRole):
    """Dependency that enforces role-based access."""
    def _check(current_user: UserModel = Depends(get_current_user)):
        if current_user.role not in roles:
            raise HTTPException(status_code=403, detail="Insufficient permissions.")
        return current_user
    return _check


''' CREATE STAFF USER 👤 '''
@router.post(AuthRoutes.CREATE_STAFF.value, response_model=StaffCreatedResponse)
async def create_staff_user(
    payload: CreateStaffUser,
    session: SessionDep,
    creator: UserModel = Depends(require_roles(UserRole.SUPER_ADMIN, UserRole.ADMIN)),
):
    user, temp_password = UserManagementService.create_staff(
        session=session,
        creator=creator,
        payload=payload,
        org_id=creator.org_id,
    )

    try:
        activation_token = create_staff_activation_token(str(user.id))
        activation_link = (
            f"{settings.FRONTEND_URL}"
            f"/activate-staff-account"
            f"?token={activation_token}"
        )
        print(f"Activation Link: {activation_link}")
        await asyncio.wait_for(
            # EmailService.send_staff_welcome_email(user.email, user.firstname, temp_password),
            EmailService.send_staff_activation_email(
                email=user.email,
                firstname=user.firstname,
                activation_link=activation_link,
            ),
            timeout=10.0
        )
    except (asyncio.TimeoutError, Exception):
        logging.exception("Welcome email failed for %s", user.email)
        # Don't block staff creation if email fails — account is already created
        # Log it and move on, admin can resend manually

    return StaffCreatedResponse(
        **UserRead.model_validate(user, from_attributes=True).model_dump(),
        temporary_password=temp_password if IS_DEV else "sent via email",
    )


''' CREATE STUDENT 🎓 '''
@router.post(AuthRoutes.CREATE_STUDENT.value, response_model=StudentCreatedResponse)
async def create_student(
    payload: CreateStudent,
    session: SessionDep,
    creator: UserModel = Depends(require_roles(UserRole.SUPER_ADMIN, UserRole.ADMIN)),
):
    user, access_code = UserManagementService.create_student(
        session=session,
        creator=creator,
        payload=payload,
        org_id=creator.org_id,
    )

    if user.email:
        await EmailService.send_student_access_code_email(
            email=user.email,
            firstname=user.firstname,
            access_code=access_code,
        )

    return StudentCreatedResponse(
        **UserRead.model_validate(user, from_attributes=True).model_dump(exclude={'access_code'}),
        access_code=access_code,  # caller shares this with student
    )


''' STAFF FIRST LOGIN SETUP 🔑 '''
@router.post(AuthRoutes.INIT_STAFF.value, deprecated=True)
async def staff_first_login_setup(
    payload: StaffFirstLoginSetup,
    session: SessionDep,
):
    user = session.exec(
        select(UserModel).where(UserModel.email == payload.email)
    ).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    if user.role == UserRole.STUDENT.value:
        raise HTTPException(status_code=400, detail="Invalid user type.")

    if not user.is_first_login:
        raise HTTPException(status_code=400, detail="Setup already completed.")

    if not user.password:
        raise HTTPException(status_code=400, detail="Temporary password not set.")

    if not PasswordHasher.verify(payload.current_password, user.password):
        raise HTTPException(status_code=400, detail="Current password is incorrect.")

    if payload.new_password != payload.confirm_new_password:
        raise HTTPException(status_code=422, detail="Passwords do not match.")

    user.password = PasswordHasher.create(payload.new_password)
    user.is_first_login = False
    user.verified = True

    session.add(user)
    session.commit()
    session.refresh(user)

    token = create_access_token(user.id, user.org_id, user.role)
    refresh = create_refresh_token(user.id)

    return StaffFirstLoginResponse(
        detail="Password updated. Account is now fully active.",
        access_token=token.access_token,
        refresh_token=refresh
    )

''' STAFF ACTIVATE 🔑 '''
@router.post(AuthRoutes.STAFF_ACTIVATE.value)
async def activate_staff_account(
    payload: StaffActivationPayload,
    session: SessionDep,
):
    if payload.password != payload.confirm_password:
        raise HTTPException(status_code=400, detail="Passwords do not match.")

    user_id = verify_staff_activation_token(payload.token)

    if not user_id:
        raise HTTPException(status_code=400, detail="Invalid or expired token.")

    user = session.get(UserModel, UUID(user_id))

    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    if not user.is_first_login:
        raise HTTPException(status_code=400, detail="Account already activated.")

    user.password = PasswordHasher.create(payload.password)
    user.is_first_login = False
    user.verified = True

    session.add(user)
    session.commit()
    session.refresh(user)

    access_token = create_access_token(user.id, user.org_id, user.role)
    refresh_token = create_refresh_token(user.id)

    return StaffActivateResponse(
        detail="Account activated successfully.",
        access_token=access_token.access_token,
        refresh_token=refresh_token
    )


''' STUDENT FIRST LOGIN SETUP 🎓 '''
@router.post(AuthRoutes.INIT_STUDENT.value)
async def student_first_login_setup(
    payload: StudentFirstLoginSetup,
    session: SessionDep,
):
    user = session.exec(
        select(UserModel).where(UserModel.access_code == payload.access_code)
    ).first()

    if not user or user.role != UserRole.STUDENT:
        raise HTTPException(status_code=404, detail="Invalid access code.")

    if not user.is_first_login:
        raise HTTPException(status_code=400, detail="Setup already completed.")

    user.favorite_question = payload.favorite_question
    user.favorite_answer_hash = PasswordHasher.create(payload.favorite_answer)
    user.is_first_login = False
    user.verified = True
    session.add(user)
    session.commit()
    session.refresh(user)

    # Issue tokens immediately after setup
    token = create_access_token(user.id, user.org_id, user.role)
    refresh = create_refresh_token(user.id)

    return StudentFirstLoginResponse(
        access_token=token.access_token,
        refresh_token=refresh,
        detail="Setup complete."
    )


''' STUDENT LOGIN VALIDATE CODE AND SHOW QUESTION 🔐 '''
@router.post(AuthRoutes.STUDENT_LOGIN_QUESTION.value)
async def get_student_security_question(
    payload: StudentAccessCodeRequest,
    session: SessionDep,
):
    user = session.exec(
        select(UserModel).where(
            UserModel.access_code == payload.access_code
        )
    ).first()

    if not user or user.role != UserRole.STUDENT:
        raise HTTPException(
            status_code=404,
            detail="Invalid access code."
        )

    if user.is_first_login:
        raise HTTPException(
            status_code=403,
            detail="Please complete first-time setup first."
        )

    if not user.favorite_question:
        raise HTTPException(
            status_code=400,
            detail="Security question not configured."
        )

    return {
        "favorite_question": user.favorite_question
    }


''' STUDENT LOGIN 🔐 '''
@router.post(AuthRoutes.STUDENT_LOGIN.value)
async def student_login(
    payload: StudentLoginRequest,
    session: SessionDep,
):
    user = session.exec(
        select(UserModel).where(UserModel.access_code == payload.access_code)
    ).first()

    if not user or user.role != UserRole.STUDENT:
        raise HTTPException(status_code=401, detail="Invalid access code or answer.")

    if user.is_first_login:
        raise HTTPException(
            status_code=403,
            detail="Please complete first-time setup before logging in."
        )

    if not user.favorite_answer_hash or not PasswordHasher.verify(
        payload.favorite_answer, user.favorite_answer_hash
    ):
        raise HTTPException(status_code=401, detail="Invalid access code or answer.")

    token = create_access_token(user.id, user.org_id, user.role)
    refresh = create_refresh_token(user.id)

    return StudentLoginResponse(
        access_token=token.access_token,
        refresh_token=refresh,
        user=StudentLoginUserResponse.model_validate(user, from_attributes=True),
    )
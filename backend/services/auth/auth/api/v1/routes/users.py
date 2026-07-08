import asyncio
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from sqlmodel import select

from auth.database.schema.user.enums import MembershipStatus, UserRole, VerificationMethod
from auth.database.schema.user.user_db import UserModel
from auth.dependencies.auth_dependencies import get_user_context
from auth.database.database import SessionDep
from auth.api_models.user_api_models import BulkStudentResult, CreateStaffUser, CreateStudent, MoveCohortRequest, PaginatedStaffResponse, PaginatedStudentResponse, StaffActivationPayload, StaffCreatedResponse, StaffFirstLoginSetup, StudentCreatedResponse, StudentFirstLoginSetup, StudentListItem, StudentLoginRequest, StudentLoginResponse, StudentLoginUserResponse, UpdateStudentRequest, UserRead, StudentAccessCodeRequest, UserReadResponse
from auth.services.user.user_management_service import UserManagementService
from auth.utility.email.email_service import EmailService
from auth.api.v1.routes.auth import IS_DEV
from auth.utility.password.password_harsher import PasswordHasher
from auth.utility.jwt.jwt import create_access_token, create_refresh_token
from auth.api.v1.auth_routes import AuthRoutes
from auth.utility.jwt.token_activation import create_staff_activation_token, verify_staff_activation_token
from auth.core.settings import settings
from auth.api_models.login_response import StudentFirstLoginResponse, StaffActivateResponse, StaffFirstLoginResponse
from auth.services.student_bulk_upload_service import generate_student_template, parse_student_excel
from auth.services.membership_service import MembershipService
from auth.database.schema.cohort.cohort_api_models import AddMembersRequest
from auth.services.cohort_service import CohortService
from auth.database.schema.organization.organization_db import OrganizationModel
from auth.services.platform_subscription_service import PlatformSubscriptionService
from auth.database.schema.membership.membership_db import OrgMembership
from auth.services.user.user_context import UserContext
from auth.api_models.user_api_models import ResendActivationRequest
from auth.api_models.user_api_models import (
    ForgotPasswordRequest, ResetPasswordRequest,
    StudentForgotPasswordRequest, StudentResetPasswordRequest,
)
from auth.utility.jwt.token_activation import (
    create_password_reset_token,
    verify_password_reset_token,
)


router = APIRouter()

router = APIRouter(
    prefix="/users",
    tags=["users"],
    responses={401: {"message": "Unauthorized"}}
)


# ============================================================
# HELPERS
# ============================================================


def require_roles(*roles: UserRole):
    def _check(
        ctx: UserContext = Depends(get_user_context),
    ):
        if ctx.membership.role not in roles:
            raise HTTPException(
                status_code=403,
                detail="Insufficient permissions."
            )

        return ctx

    return _check

AdminOrAbove = require_roles(UserRole.SUPER_ADMIN, UserRole.ADMIN)



def _get_active_membership(session: SessionDep, user_id: UUID) -> OrgMembership:
    """
    Fetch the user's active OrgMembership.
    Raises 403 if not found — used after activation/setup to issue tokens.
    """
    membership = session.exec(
        select(OrgMembership).where(
            OrgMembership.user_id == user_id,
            OrgMembership.status == MembershipStatus.ACTIVE,
        )
    ).first()

    if not membership:
        raise HTTPException(
            status_code=403,
            detail="No active organization membership found.",
        )
    return membership


''' CREATE STAFF USER 👤 '''
@router.post(AuthRoutes.CREATE_STAFF.value, response_model=StaffCreatedResponse)
async def create_staff_user(
    payload: CreateStaffUser,
    session: SessionDep,
    ctx: UserContext = Depends(require_roles(UserRole.SUPER_ADMIN, UserRole.ADMIN)),
):
    PlatformSubscriptionService.assert_can_add_staff(session, ctx.membership.org_id)
    
    user, temp_password, is_existing = UserManagementService.create_staff(
        session=session,
        ctx=ctx,
        payload=payload,
        org_id=ctx.membership.org_id,
    )

    org = session.exec(
        select(OrganizationModel).where(OrganizationModel.id == ctx.membership.org_id)
    ).first()

    if is_existing:
        # User already has an account — just notify them
        try:
            await asyncio.wait_for(
                EmailService.send_added_to_org_email(
                    email=user.email,
                    firstname=user.firstname,
                    org_name=org.name if org else "an organization",
                    role=payload.role,
                ),
                timeout=10.0,
            )
        except Exception:
            logging.warning("Failed to send org-added notification to %s", user.email)

        return StaffCreatedResponse(
            **UserReadResponse.model_validate(user, from_attributes=True).model_dump(),
            temporary_password="",  # signal to frontend
            is_existing_user=True,
        )

    try:
        activation_token = create_staff_activation_token(str(user.id))
        activation_link = (
            f"{settings.FRONTEND_URL}"
            f"/activate-staff-account"
            f"?token={activation_token}"
        )
        
        await asyncio.wait_for(
            # EmailService.send_staff_welcome_email(user.email, user.firstname, temp_password),
            EmailService.send_staff_activation_email(
                email=user.email,
                firstname=user.firstname,
                activation_link=activation_link,
            ),
            timeout=10.0
        )
        if IS_DEV:
            print(f'ACTIVATION TOKEN LINK: {activation_link}')
    except (asyncio.TimeoutError, Exception):
        logging.exception("Activation email failed for %s", user.email)
        # Don't block staff creation if email fails — account is already created
        # Log it and move on, admin can resend manually

    return StaffCreatedResponse(
        **UserReadResponse.model_validate(user, from_attributes=True).model_dump(),
        org_id=org.id,
        role=payload.role,
        temporary_password=temp_password if IS_DEV else "sent via email",
        is_existing_user=False,
    )


''' CREATE STUDENT 🎓 '''
@router.post(AuthRoutes.CREATE_STUDENT.value, response_model=StudentCreatedResponse)
async def create_student(
    payload: CreateStudent,
    session: SessionDep,
    ctx: UserContext = Depends(
        require_roles(
            UserRole.SUPER_ADMIN,
            UserRole.ADMIN,
        )
    ),
):
    # ← ADD THIS: check plan limits before creating
    PlatformSubscriptionService.assert_can_add_student(session, ctx.membership.org_id)
    
    # if not payload.cohort_id:
    #     raise HTTPException(status_code=422, detail="Cohort Id is required to create student")    HANDLE FOR ADMIN/SUPER ADMIN CREATTION
    
    user, access_code = UserManagementService.create_student(
        session=session,
        ctx=ctx,
        payload=payload,
        org_id=ctx.membership.org_id,
    )

    # Auto-assign to cohort if provided
    if payload.cohort_id:
        try:
            CohortService.add_members(
                session=session,
                cohort_id=payload.cohort_id,
                payload=AddMembersRequest(student_ids=[user.id]),
                actor=ctx.user,
            )
        except HTTPException as e:
            logging.warning("Could not assign student to cohort: %s", e.detail)

    if user.email:
        await EmailService.send_student_access_code_email(
            email=user.email,
            firstname=user.firstname,
            access_code=access_code,
        )
    
    memebership = MembershipService.get_pending_membership(session=session, user_id=user.id)

    return StudentCreatedResponse(
        **UserRead.model_validate(user, from_attributes=True).model_dump(exclude={'access_code'}),
        org_id=ctx.membership.org_id,
        role=memebership.role if memebership else None,
        access_code=access_code,  # caller shares this with student
    )


''' BULK STUDENT TEMPLATE ⬇️ '''
@router.get(AuthRoutes.STUDENT_BULK_TEMPLATE.value)
async def download_student_template(
    creator: UserModel = Depends(require_roles(UserRole.SUPER_ADMIN, UserRole.ADMIN)),
):
    """Download Excel template for bulk student creation."""
    file_bytes = generate_student_template()
    return Response(
        content=file_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=students_template.xlsx"},
    )


''' BULK CREATE STUDENTS 📤 '''
@router.post(AuthRoutes.CREATE_STUDENTS_BULK.value, response_model=BulkStudentResult)
async def create_students_bulk(
    session: SessionDep,
    ctx: UserContext = Depends(require_roles(UserRole.SUPER_ADMIN, UserRole.ADMIN)),
    file: UploadFile = File(...),
):
    """
    Upload an Excel file to create multiple students at once.
    Download the template from GET /users/students/bulk/template first.
    Partial success supported — valid rows are created even if some fail.
    """
    if not file.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="Only .xlsx and .xls files are accepted.")

    file_bytes = await file.read()
    rows = parse_student_excel(file_bytes, file.filename)

    result = UserManagementService.create_students_bulk(
        session=session,
        ctx=ctx,
        rows=rows,
        org_id=ctx.membership.org_id,
    )

    # Send access code emails for students who have email addresses
    for student in result.students:
        student_user = session.exec(
            select(UserModel).where(UserModel.id == student.id)
        ).first()
        if student_user and student_user.email:
            try:
                await EmailService.send_student_access_code_email(
                    email=student_user.email,
                    firstname=student_user.firstname,
                    access_code=student.access_code,
                )
            except Exception:
                logging.warning("Failed to send access code email to %s", student_user.email)

    return result


''' STAFF FIRST LOGIN SETUP 🔑 '''
@router.post(AuthRoutes.INIT_STAFF.value, deprecated=True)
async def staff_first_login_setup(
    payload: StaffFirstLoginSetup,
    session: SessionDep,
):
    user = session.exec(
        select(UserModel).where(UserModel.email == payload.email)
    ).first()

    if not user:                                # ← early return
        raise HTTPException(status_code=404, detail="User Not Found.")

    membership = session.exec(
        select(OrgMembership).where(
            OrgMembership.user_id == user.id
        )
    ).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    if membership.role == UserRole.STUDENT.value:
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

    token = create_access_token(user.id, membership.org_id, membership.role)
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
    try:
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
        user.verification_method = VerificationMethod.EMAIL_OTP

        session.add(user)
        session.flush()

        # Activate the pending membership created when admin created this user
        membership = MembershipService.get_pending_membership(session=session, user_id=user.id)

        if membership:
            membership.status = MembershipStatus.ACTIVE
            membership.verification_method = VerificationMethod.EMAIL_OTP
            session.add(membership)
        else:
            # Fallback — should not happen but guard anyway
            logging.warning("No pending membership found for user %s", user.id)

        session.commit()
        session.refresh(user)

        # Get org from now-active membership
        active_membership = session.exec(
            select(OrgMembership).where(
                OrgMembership.user_id == user.id,
                OrgMembership.status == MembershipStatus.ACTIVE,
            )
        ).first()

        org_id = active_membership.org_id if active_membership else user.id  # fallback
        role = active_membership.role if active_membership else None

        access_token = create_access_token(user.id, org_id, role)
        refresh_token = create_refresh_token(user.id)

        return StaffActivateResponse(
            detail="Account activated successfully.",
            access_token=access_token.access_token,
            refresh_token=refresh_token
        )
    except Exception as e:
        logging.warning("Error: %s", str(e))
        session.rollback()
        raise HTTPException(
            status_code=409, 
            detail={"message": "Error", "error": str(e)}
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

    if not user:                                # ← early return
        raise HTTPException(status_code=404, detail="User Not Found.")

    membership = session.exec(
        select(OrgMembership).where(
            OrgMembership.user_id == user.id,
            OrgMembership.status == MembershipStatus.PENDING,
            # OrgMembership.role == UserRole.STUDENT
        )
    ).first()

    if not user or membership.role != UserRole.STUDENT:
        raise HTTPException(status_code=404, detail="Invalid access code.")

    if not user.is_first_login:
        raise HTTPException(status_code=400, detail="Setup already completed.")

    user.favorite_question = payload.favorite_question
    user.favorite_answer_hash = PasswordHasher.create(payload.favorite_answer)
    user.is_first_login = False
    user.verified = True
    session.add(user)
    session.flush()                                 # flush so user.id is available

    # ← ADD THIS: auto-add to org on student setup completion
    MembershipService.auto_add_on_verification(
        session=session,
        user=user,
        org_id=membership.org_id,
        role=UserRole.STUDENT,
        created_by=user.id,
        verification_method=VerificationMethod.ACCESS_CODE,
        institution_id=membership.institution_id,
    )

    session.commit()
    session.refresh(user)

    # Issue tokens immediately after setup
    token = create_access_token(user.id, membership.org_id, membership.role)
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

    if not user:                                # ← early return
        raise HTTPException(status_code=404, detail="Invalid access code.")

    membership = session.exec(
        select(OrgMembership).where(
            OrgMembership.user_id == user.id,
            OrgMembership.status == MembershipStatus.ACTIVE,
        )
    ).first()

    if not membership or membership.role != UserRole.STUDENT:
        raise HTTPException(
            status_code=403,
            detail="Please complete first-time setup first."
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

    membership = session.exec(
        select(OrgMembership).where(
            OrgMembership.user_id == user.id,
            OrgMembership.status == MembershipStatus.ACTIVE,
        )
    ).first()

    if not membership:
        raise HTTPException(status_code=401, detail='No active membership found. Initialize your account first before login')

    if not user or membership.role != UserRole.STUDENT:
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

    
    token = create_access_token(user.id, membership.org_id, membership.role)
    refresh = create_refresh_token(user.id)

        
    user_data = StudentLoginUserResponse.model_validate(user, from_attributes=True)
    user_data.role = membership.role if membership else None
    user_data.org_id = membership.org_id if membership else None
    user_data.institution_id = membership.institution_id if membership.institution_id else None

    return StudentLoginResponse(
        access_token=token.access_token,
        refresh_token=refresh,
        user=user_data
    )





''' RESEND STAFF ACTIVATION 🔁 '''
@router.post(AuthRoutes.STAFF_ACTIVATE_RESEND.value)
async def resend_staff_activation(
    payload: ResendActivationRequest,
    session: SessionDep,
):
    """
    Two callers:
    - Staff member themselves: hits this after seeing RESEND_ACTIVATION on login
    - Admin: calls on behalf of a stuck staff member
    Both paths just need the email — no auth required since user isn't activated yet.
    """
    user = session.exec(
        select(UserModel).where(UserModel.email == payload.email.lower().strip())
    ).first()

    # Always return 200 — don't leak whether email exists
    if not user:
        return {"detail": "If that email exists, a new activation link has been sent."}

    # Only resend for unverified, non-student staff with a pending membership
    pending_membership = session.exec(
        select(OrgMembership).where(
            OrgMembership.user_id == user.id,
            OrgMembership.status == MembershipStatus.PENDING,
            OrgMembership.role != UserRole.STUDENT,
        )
    ).first()

    if not pending_membership:
        return {"detail": "If that email exists, a new activation link has been sent."}

    if not user.is_first_login:
        # Already activated — no need to resend
        return {"detail": "If that email exists, a new activation link has been sent."}

    # Issue a fresh 24hr token
    activation_token = create_staff_activation_token(str(user.id))
    activation_link = (
        f"{settings.FRONTEND_URL}"
        f"/activate-staff-account"
        f"?token={activation_token}"
    )

    try:
        await asyncio.wait_for(
            EmailService.send_staff_activation_email(
                email=user.email,
                firstname=user.firstname,
                activation_link=activation_link,
            ),
            timeout=10.0,
        )
        logging.info("Resent activation link to %s", user.email)
        if IS_DEV:
            print(f"RESENT ACTIVATION LINK: {activation_link}")
    except Exception:
        logging.exception("Failed to resend activation email to %s", user.email)
        # Still return 200 — don't expose email delivery failures

    return {"detail": "If that email exists, a new activation link has been sent."}



''' ADMIN RESEND STAFF ACTIVATION 🔁 '''
@router.post("/staff/{user_id}/activate/resend")
async def admin_resend_staff_activation(
    user_id: UUID,
    session: SessionDep,
    ctx: UserContext = Depends(require_roles(UserRole.SUPER_ADMIN, UserRole.ADMIN)),
):
    """Admin resends activation to a specific staff member by user_id."""
    user = session.get(UserModel, user_id)

    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    # Confirm they belong to the admin's org and are still pending
    pending_membership = session.exec(
        select(OrgMembership).where(
            OrgMembership.user_id == user_id,
            OrgMembership.org_id == ctx.membership.org_id,
            OrgMembership.status == MembershipStatus.PENDING,
            OrgMembership.role != UserRole.STUDENT,
        )
    ).first()

    if not pending_membership:
        raise HTTPException(
            status_code=400,
            detail="No pending activation found for this user in your organization.",
        )

    if not user.is_first_login:
        raise HTTPException(status_code=400, detail="Account is already activated.")

    activation_token = create_staff_activation_token(str(user.id))
    activation_link = (
        f"{settings.FRONTEND_URL}"
        f"/activate-staff-account"
        f"?token={activation_token}"
    )

    try:
        await asyncio.wait_for(
            EmailService.send_staff_activation_email(
                email=user.email,
                firstname=user.firstname,
                activation_link=activation_link,
            ),
            timeout=10.0,
        )
        if IS_DEV:
            print(f"ADMIN RESENT ACTIVATION LINK: {activation_link}")
    except Exception:
        logging.exception("Failed to resend activation to %s", user.email)
        raise HTTPException(status_code=502, detail="Failed to send activation email.")

    return {
        "detail": f"Activation link resent to {EmailService.mask_email(user.email)}."
    }


''' FORGOT PASSWORD 📧 '''
@router.post(AuthRoutes.FORGOT_PASSWORD.value)
async def forgot_password(
    payload: ForgotPasswordRequest,
    session: SessionDep,
):
    user = session.exec(
        select(UserModel).where(
            UserModel.email == payload.email.lower().strip()
        )
    ).first()

    # Silent return for missing accounts or students (no password field)
    if not user or not user.password:
        return {"detail": "If that email exists, a reset link has been sent."}

    # Unverified staff — their password exists but account was never activated
    # Send activation link instead of reset link since they haven't set a real password yet
    if not user.verified:
        pending_membership = session.exec(
            select(OrgMembership).where(
                OrgMembership.user_id == user.id,
                OrgMembership.status == MembershipStatus.PENDING,
                OrgMembership.role != UserRole.STUDENT,
            )
        ).first()

        if pending_membership:
            activation_token = create_staff_activation_token(str(user.id))
            activation_link = (
                f"{settings.FRONTEND_URL}"
                f"/activate-staff-account"
                f"?token={activation_token}"
            )
            try:
                await asyncio.wait_for(
                    EmailService.send_staff_activation_email(
                        email=user.email,
                        firstname=user.firstname,
                        activation_link=activation_link,
                    ),
                    timeout=10.0,
                )
                if IS_DEV:
                    print(f"RESENT ACTIVATION LINK: {activation_link}")
            except Exception:
                logging.exception("Failed to resend activation to %s", user.email)

        return {"detail": "A reset link has been sent to the provided email."}

    # Verified user — issue password reset link
    reset_token = create_password_reset_token(str(user.id))
    reset_link = (
        f"{settings.FRONTEND_URL}"
        f"/reset-password"
        f"?token={reset_token}"
    )

    try:
        await asyncio.wait_for(
            EmailService.send_password_reset_email(
                email=user.email,
                firstname=user.firstname,
                reset_link=reset_link,
            ),
            timeout=10.0,
        )
        if IS_DEV:
            print(f"RESET LINK: {reset_link}")
    except Exception:
        logging.exception("Password reset email failed for %s", user.email)

    return {"detail": "If that email exists, a reset link has been sent."}



''' RESET PASSWORD 🔑 '''
@router.post(AuthRoutes.RESET_PASSWORD.value)
async def reset_password(
    payload: ResetPasswordRequest,
    session: SessionDep,
):
    if payload.new_password != payload.confirm_password:
        raise HTTPException(status_code=400, detail="Passwords do not match.")

    try:
        user_id = verify_password_reset_token(payload.token)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid or expired reset link.")

    user = session.get(UserModel, UUID(user_id))

    if not user or not user.password:
        raise HTTPException(status_code=404, detail="User not found.")

    user.password = PasswordHasher.create(payload.new_password)
    session.add(user)
    session.commit()

    return {"detail": "Password reset successfully. You can now log in."}



''' STUDENT FORGOT PASSWORD — STEP 1: VERIFY ACCESS CODE 🎓 '''
@router.post(AuthRoutes.STUDENT_VERIFY_FORGOT_PASSWORD.value)
async def student_forgot_password_verify(
    payload: StudentForgotPasswordRequest,
    session: SessionDep,
):
    """Returns the security question for the student to answer in step 2."""
    user = session.exec(
        select(UserModel).where(UserModel.access_code == payload.access_code)
    ).first()

    membership = None
    if user:
        membership = session.exec(
            select(OrgMembership).where(
                OrgMembership.user_id == user.id,
                OrgMembership.status == MembershipStatus.ACTIVE,
                OrgMembership.role == UserRole.STUDENT,
            )
        ).first()

    if not user or not membership:
        raise HTTPException(status_code=404, detail="Invalid access code.")

    if user.is_first_login:
        raise HTTPException(
            status_code=403,
            detail="Please complete first-time setup first.",
        )

    if not user.favorite_question or not user.favorite_answer_hash:
        raise HTTPException(
            status_code=400,
            detail="Security question not configured. Contact your administrator.",
        )

    return {"favorite_question": user.favorite_question}



''' STUDENT FORGOT PASSWORD — STEP 2: RESET SECURITY Q&A 🎓 '''
@router.post(AuthRoutes.STUDENT_RESET_PASSWORD_QA.value)
async def student_reset_password(
    payload: StudentResetPasswordRequest,
    session: SessionDep,
):
    """
    Verifies current security answer, then replaces Q&A with new one.
    Issues tokens on success so the student is logged in immediately.
    """
    user = session.exec(
        select(UserModel).where(UserModel.access_code == payload.access_code)
    ).first()

    membership = None
    if user:
        membership = session.exec(
            select(OrgMembership).where(
                OrgMembership.user_id == user.id,
                OrgMembership.status == MembershipStatus.ACTIVE,
                OrgMembership.role == UserRole.STUDENT,
            )
        ).first()

    # Same generic error for both bad access code and wrong answer
    # — don't reveal which one failed
    if not user or not membership:
        raise HTTPException(status_code=401, detail="Invalid access code or answer.")

    if not user.favorite_answer_hash or not PasswordHasher.verify(
        payload.favorite_answer, user.favorite_answer_hash
    ):
        raise HTTPException(status_code=401, detail="Invalid access code or answer.")

    user.favorite_question = payload.new_favorite_question
    user.favorite_answer_hash = PasswordHasher.create(payload.new_favorite_answer)
    session.add(user)
    session.commit()
    session.refresh(user)

    token = create_access_token(user.id, membership.org_id, membership.role)
    refresh = create_refresh_token(user.id)

    return StudentFirstLoginResponse(
        access_token=token.access_token,
        refresh_token=refresh,
        detail="Security question reset successfully.",
    )




''' LIST STAFF 👥 '''
@router.get("/staff", response_model=PaginatedStaffResponse)
async def list_staff(
    session: SessionDep,
    ctx: UserContext = Depends(require_roles(UserRole.SUPER_ADMIN, UserRole.ADMIN)),
    cohort_id: UUID | None = Query(default=None),
    subject_id: UUID | None = Query(default=None),
    name: str | None = Query(default=None, description="Search by staff first/last name"),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
):
    rows, total = UserManagementService.list_staff(
        session=session, org_id=ctx.membership.org_id,
        cohort_id=cohort_id, 
        # subject_id=subject_id, 
        name=name,
        page=page, per_page=per_page,
    )
    return PaginatedStaffResponse(
        total=total, page=page, per_page=per_page,
        staff=UserManagementService.to_read_list(session, rows, ctx.membership.org_id),
    )


''' LIST STUDENTS 🎓 '''
@router.get("/students", response_model=PaginatedStudentResponse)
async def list_students(
    session: SessionDep,
    ctx: UserContext = Depends(require_roles(UserRole.SUPER_ADMIN, UserRole.ADMIN)),
    status: MembershipStatus | None = Query(default=None),
    cohort_id: UUID | None = Query(default=None),
    name: str | None = Query(default=None, description="Search by student first/last name"),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
):
    rows, total = UserManagementService.list_students(
        session=session, org_id=ctx.membership.org_id,
        status=status, cohort_id=cohort_id, name=name, 
        page=page, per_page=per_page,
    )
    return PaginatedStudentResponse(
        total=total, page=page, per_page=per_page,
        students=[
            StudentListItem(
                id=user.id, firstname=user.firstname, lastname=user.lastname,
                email=user.email, access_code=user.access_code,
                status=membership.status, created_at=membership.created_at,
            )
            for user, membership in rows
        ],
    )


''' UPDATE STUDENT ✏️ '''
@router.patch("/{student_id}", response_model=UserRead)
async def update_student(
    student_id: UUID,
    payload: UpdateStudentRequest,
    session: SessionDep,
    ctx: UserContext = Depends(AdminOrAbove),
):
    user = UserManagementService.update_student(
        session=session, student_id=student_id, payload=payload, org_id=ctx.membership.org_id,
    )
    return UserRead.model_validate(user, from_attributes=True)


''' MOVE STUDENT TO NEW COHORT 🔀 '''
@router.put("/{student_id}/cohort")
async def move_student_cohort(
    student_id: UUID,
    payload: MoveCohortRequest,
    session: SessionDep,
    ctx: UserContext = Depends(AdminOrAbove),
):
    return CohortService.change_student_cohort(
        session=session, student_id=student_id, new_cohort_id=payload.cohort_id,
        actor=ctx.user, org_id=ctx.membership.org_id,
    )
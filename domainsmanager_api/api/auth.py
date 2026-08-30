from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from domainsmanager_api.dependencies import (
    AuthContextDependency,
    AuthServiceDependency,
    CurrentUserDependency,
    ResourcesDependency,
    RuntimeSettingsDependency,
    get_session,
)
from domainsmanager_api.email_verification import begin as begin_email_verification
from domainsmanager_api.email_verification import confirm as confirm_email_verification
from domainsmanager_api.email_verification import (
    setting_values,
    validate_allowlist,
    validate_site_url,
)
from domainsmanager_api.notifier import send_verification_email
from domainsmanager_api.schemas.auth import (
    AuthResultResponse,
    ChangePasswordRequest,
    EmailVerificationConfirmRequest,
    EmailVerificationResponse,
    RegisterRequest,
    TokenPairResponse,
    UpdateCurrentUserRequest,
    UserResponse,
    UserSettings,
    UserSettingsPatch,
)
from domainsmanager_application.auth import UserRecord
from domainsmanager_application.security import (
    InvalidPasswordError,
    InvalidUsernameError,
)
from domainsmanager_application.services import (
    AccountBannedError,
    AuthenticationError,
    AuthenticationResult,
    InvalidTokenError,
    PasswordMismatchError,
    PasswordReusedError,
    RegistrationDisabledError,
    TokenPair,
    UsernameTakenError,
)
from domainsmanager_persistence.models import AppUser

router = APIRouter(prefix="/auth", tags=["Authentication"])


def no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


def user_response(user: UserRecord) -> UserResponse:
    return UserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        pending_email=user.pending_email,
        email_verified_at=user.email_verified_at,
        role=user.role,
        status="banned" if user.banned_at is not None or not user.is_active else "active",
        last_login_at=user.last_login_at,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


def token_response(tokens: TokenPair) -> TokenPairResponse:
    return TokenPairResponse(
        access_token=tokens.access_token,
        token_type="bearer",
        expires_in=tokens.expires_in,
    )


def auth_response(result: AuthenticationResult) -> AuthResultResponse:
    return AuthResultResponse(
        user=user_response(result.user),
        tokens=token_response(result.tokens),
    )


def set_refresh_cookie(response: Response, token: str, request: Request) -> None:
    settings = request.app.state.settings
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=token,
        max_age=settings.refresh_token_ttl_seconds,
        httponly=True,
        secure=settings.refresh_cookie_secure,
        samesite=settings.refresh_cookie_samesite,
        path=f"{settings.api_prefix}/auth",
    )


def clear_refresh_cookie(response: Response, request: Request) -> None:
    settings = request.app.state.settings
    response.delete_cookie(
        key=settings.refresh_cookie_name,
        httponly=True,
        secure=settings.refresh_cookie_secure,
        samesite=settings.refresh_cookie_samesite,
        path=f"{settings.api_prefix}/auth",
    )


def raise_auth_error(error: Exception) -> None:
    if isinstance(error, RegistrationDisabledError):
        status_code = 403
    elif isinstance(error, (UsernameTakenError, PasswordReusedError)):
        status_code = 409
    elif isinstance(error, AccountBannedError):
        status_code = 403
    elif isinstance(error, (AuthenticationError, InvalidTokenError)):
        status_code = 401
    elif isinstance(
        error, (PasswordMismatchError, InvalidUsernameError, InvalidPasswordError)
    ):
        status_code = 422
    else:
        raise error
    headers = {"WWW-Authenticate": "Bearer"} if status_code == 401 else None
    raise HTTPException(
        status_code=status_code,
        detail={
            "code": getattr(error, "code", "validation_error"),
            "message": str(error),
        },
        headers=headers,
    ) from error


@router.post(
    "/register",
    response_model=AuthResultResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="registerUser",
)
async def register(
    body: RegisterRequest,
    request: Request,
    response: Response,
    auth: AuthServiceDependency,
    context: AuthContextDependency,
) -> AuthResultResponse:
    try:
        # Email is persisted as pending until the message link is confirmed.
        # This is deliberately performed after account/session creation.
        result = await auth.register(
            body.username,
            body.password,
            str(body.email) if body.email is not None else None,
            context,
        )
    except Exception as error:
        raise_auth_error(error)
    session_factory = request.app.state.resources.sessions
    async with session_factory() as session:
        config = await setting_values(session, request.app.state.settings)
        if bool(config["email_verification_enabled"]) and body.email is None:
            raise HTTPException(status_code=422, detail={"code": "email_verification_required", "message": "email is required when verification is enabled"})
        if bool(config["email_verification_enabled"]) and body.email is not None:
            validate_allowlist(str(body.email), str(config["email_domain_allowlist"]))
            try:
                link = await begin_email_verification(session, user_id=result.user.id, email=str(body.email), site_url=validate_site_url(str(config["site_url"])))
                await send_verification_email(str(body.email), link, request.app.state.settings, session_factory)
            except ValueError as error:
                raise HTTPException(status_code=422, detail={"code": "email_verification_configuration_error", "message": str(error)}) from error
            result = AuthenticationResult(user=await auth.get_user(result.user.id), tokens=result.tokens)
    no_store(response)
    response.headers["Location"] = str(request.url_for("getCurrentUser"))
    set_refresh_cookie(response, result.tokens.refresh_token, request)
    return auth_response(result)


@router.post(
    "/login",
    response_model=AuthResultResponse,
    operation_id="login",
)
async def login(
    request: Request,
    response: Response,
    auth: AuthServiceDependency,
    context: AuthContextDependency,
    username: str = Form(min_length=1, max_length=320),
    password: str = Form(min_length=1, max_length=256),
    scope: str = Form(default=""),
) -> AuthResultResponse:
    del scope
    try:
        result = await auth.login(username, password, context)
    except Exception as error:
        raise_auth_error(error)
    no_store(response)
    set_refresh_cookie(response, result.tokens.refresh_token, request)
    return auth_response(result)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="logout",
)
async def logout(
    request: Request,
    response: Response,
    auth: AuthServiceDependency,
    context: AuthContextDependency,
) -> None:
    token = request.cookies.get(request.app.state.settings.refresh_cookie_name)
    if token is not None:
        await auth.logout(token, context)
    clear_refresh_cookie(response, request)


@router.post(
    "/token/refresh",
    response_model=TokenPairResponse,
    operation_id="refreshToken",
)
async def refresh_token(
    request: Request,
    response: Response,
    auth: AuthServiceDependency,
    context: AuthContextDependency,
) -> TokenPairResponse:
    token = request.cookies.get(request.app.state.settings.refresh_cookie_name)
    if token is None:
        raise_auth_error(InvalidTokenError("refresh token is required"))
    try:
        tokens = await auth.rotate_refresh_token(token, context)
    except Exception as error:
        raise_auth_error(error)
    no_store(response)
    set_refresh_cookie(response, tokens.refresh_token, request)
    return token_response(tokens)


@router.get(
    "/me",
    response_model=UserResponse,
    operation_id="getCurrentUser",
    name="getCurrentUser",
    tags=["Current user"],
)
async def get_me(current: CurrentUserDependency) -> UserResponse:
    return user_response(current.user)


@router.patch(
    "/me",
    response_model=UserResponse,
    operation_id="updateCurrentUser",
    tags=["Current user"],
)
async def update_me(
    request: UpdateCurrentUserRequest,
    current: CurrentUserDependency,
    auth: AuthServiceDependency,
    context: AuthContextDependency,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: RuntimeSettingsDependency,
    resources: ResourcesDependency,
) -> UserResponse:
    if "email" not in request.model_fields_set:
        raise HTTPException(
            status_code=422,
            detail={"code": "validation_error", "message": "email is required"},
        )
    config = await setting_values(session, settings)
    if bool(config["email_verification_enabled"]) and request.email is not None:
        validate_allowlist(str(request.email), str(config["email_domain_allowlist"]))
        try:
            link = await begin_email_verification(session, user_id=current.user.id, email=str(request.email), site_url=validate_site_url(str(config["site_url"])))
            await send_verification_email(str(request.email), link, settings, resources.sessions)
        except ValueError as error:
            raise HTTPException(status_code=422, detail={"code": "email_verification_configuration_error", "message": str(error)}) from error
        return user_response(await auth.get_user(current.user.id))
    try:
        user = await auth.update_profile(
            current.user.id,
            str(request.email) if request.email is not None else None,
            context,
        )
    except Exception as error:
        raise_auth_error(error)
    return user_response(user)


@router.post("/email-verifications/confirm", response_model=EmailVerificationResponse, operation_id="confirmEmailVerification")
async def confirm_email(body: EmailVerificationConfirmRequest, session: Annotated[AsyncSession, Depends(get_session)]) -> EmailVerificationResponse:
    user = await confirm_email_verification(session, body.token)
    return EmailVerificationResponse(status="verified", email=user.email)


@router.post("/me/email-verifications/resend", response_model=EmailVerificationResponse, operation_id="resendEmailVerification")
async def resend_email_verification(
    current: CurrentUserDependency,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: RuntimeSettingsDependency,
    resources: ResourcesDependency,
) -> EmailVerificationResponse:
    user = await session.get(AppUser, current.user.id)
    if user is None or not user.pending_email:
        raise HTTPException(status_code=409, detail={"code": "email_verification_not_pending", "message": "there is no pending email verification"})
    config = await setting_values(session, settings)
    try:
        link = await begin_email_verification(session, user_id=user.id, email=user.pending_email, site_url=validate_site_url(str(config["site_url"])))
        await send_verification_email(user.pending_email, link, settings, resources.sessions)
    except ValueError as error:
        raise HTTPException(status_code=422, detail={"code": "email_verification_configuration_error", "message": str(error)}) from error
    return EmailVerificationResponse(status="pending", pending_email=user.pending_email)


@router.post(
    "/me/password",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="changePassword",
    tags=["Current user"],
)
async def change_password(
    request: ChangePasswordRequest,
    current: CurrentUserDependency,
    auth: AuthServiceDependency,
    context: AuthContextDependency,
) -> None:
    try:
        await auth.change_password(
            current,
            request.current_password,
            request.new_password,
            context,
        )
    except Exception as error:
        raise_auth_error(error)


@router.get(
    "/me/settings",
    response_model=UserSettings,
    operation_id="getCurrentUserSettings",
    tags=["Current user"],
)
async def get_settings(current: CurrentUserDependency) -> UserSettings:
    return UserSettings.model_validate(current.user.preferences)


@router.patch(
    "/me/settings",
    response_model=UserSettings,
    operation_id="updateCurrentUserSettings",
    tags=["Current user"],
)
async def update_settings(
    request: UserSettingsPatch,
    current: CurrentUserDependency,
    auth: AuthServiceDependency,
    context: AuthContextDependency,
) -> UserSettings:
    if not request.model_fields_set:
        raise HTTPException(
            status_code=422,
            detail={"code": "validation_error", "message": "no settings supplied"},
        )
    supplied = request.model_dump(include=request.model_fields_set)
    if any(value is None for value in supplied.values()):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "validation_error",
                "message": "settings cannot be null",
            },
        )
    existing = UserSettings.model_validate(current.user.preferences)
    updated = UserSettings.model_validate(
        {**existing.model_dump(), **supplied}
    )
    user = await auth.update_settings(
        current.user.id,
        updated.model_dump(),
        context,
    )
    return UserSettings.model_validate(user.preferences)

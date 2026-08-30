from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from domainsmanager_api.dependencies import (
    AdminUserDependency,
    AuthContextDependency,
    ResourcesDependency,
    RuntimeSettingsDependency,
    TaskServiceDependency,
    get_session,
)
from domainsmanager_api.global_setting_registry import (
    GLOBAL_SETTING_BY_KEY,
    GLOBAL_SETTINGS,
)
from domainsmanager_api.operations import collect_operational_metrics
from domainsmanager_api.schemas.admin import (
    AdminSessionPageResponse,
    AdminSessionResponse,
    AdminUpdateUserRequest,
    AdminUserPageResponse,
    AdminUserResponse,
    AdminUserSummaryResponse,
    BanUserRequest,
)
from domainsmanager_api.schemas.admin_domains import (
    AdminDomainCheckPageResponse,
    AdminDomainCheckResponse,
    AdminDomainPageResponse,
    AdminDomainSummaryResponse,
    AdminManagedDomainResponse,
    AdminUpdateDomainRequest,
    CheckStatisticsResponse,
    NotificationOutboxMetricsResponse,
    OperationalMetricsResponse,
    RefreshTaskMetricsResponse,
    SecurityAuditEventPageResponse,
    SecurityAuditEventResponse,
    UserReferenceResponse,
)
from domainsmanager_api.schemas.global_settings import (
    GlobalSettingBatchPatch,
    GlobalSettingPatch,
    GlobalSettingResponse,
    TestEmailRequest,
    TestEmailResponse,
)
from domainsmanager_api.notifier import send_test_email
from domainsmanager_api.schemas.refresh_policy import (
    RefreshPolicyPatch,
    RefreshPolicyResponse,
)
from domainsmanager_api.schemas.tasks import (
    RefreshTaskResponse,
    TaskErrorResponse,
    TaskResultResponse,
)
from domainsmanager_application.domains import DomainError
from domainsmanager_application.tasks import IdempotencyConflictError, TaskError
from domainsmanager_persistence.models import (
    AppUser,
    AuthRefreshToken,
    AuthSession,
    DomainCheck,
    GlobalSetting,
    ManagedDomain,
    SecurityAuditEvent,
)

router = APIRouter(prefix="/admin", tags=["Admin users", "Admin domains"])
GLOBAL_SETTING_KEY = "successful_refresh_ttl_seconds"


@router.post(
    "/settings/test-email",
    response_model=TestEmailResponse,
    operation_id="sendTestEmail",
)
async def test_email(
    body: TestEmailRequest,
    _: AdminUserDependency,
    settings: RuntimeSettingsDependency,
    resources: ResourcesDependency,
) -> TestEmailResponse:
    try:
        await send_test_email(str(body.email), settings, resources.sessions)
    except Exception as error:  # noqa: BLE001 - SMTP errors must be sanitised at the API boundary
        raise HTTPException(
            status_code=422,
            detail={"code": "test_email_failed", "message": "test email could not be sent"},
        ) from error
    return TestEmailResponse()


def setting_value(definition, value: str) -> object:
    if definition.kind == "boolean":
        return value == "true"
    if definition.kind == "integer":
        return int(value)
    if definition.kind == "number":
        return float(value)
    if definition.kind == "choice":
        return value
    if definition.kind == "json":
        return json.loads(value)
    return value


def setting_response(
    definition, setting: GlobalSetting | None, default: float | bool | str | None
) -> GlobalSettingResponse:
    if definition.secret:
        value = setting.value if setting is not None else default
        if hasattr(value, "get_secret_value"):
            value = value.get_secret_value()
        configured = bool(value)
    else:
        value = setting_value(definition, setting.value) if setting else default
        if hasattr(value, "get_secret_value"):
            value = value.get_secret_value()
        configured = value is not None and value != ""
    return GlobalSettingResponse(
        key=definition.key,
        group=definition.group,
        label=definition.label,
        description=definition.description,
        kind=definition.kind,
        value=value,
        configured=configured,
        version=setting.version if setting else 0,
        source=(
            "database"
            if setting
            else "registry_default"
            if definition.uses_registry_default
            else "environment_default"
        ),
        updated_at=setting.updated_at if setting else None,
        minimum=definition.minimum,
        maximum=definition.maximum,
        unit=definition.unit,
        choices=definition.choices,
        live=definition.live,
        editor=definition.editor,
        language=definition.language,
        placeholder=definition.placeholder,
    )


def valid_setting_value(definition, value: object) -> bool:
    if definition.secret:
        return value is None or (isinstance(value, str) and len(value) <= 4096)
    if definition.kind == "boolean":
        return isinstance(value, bool)
    if definition.kind == "integer":
        return (
            isinstance(value, int)
            and not isinstance(value, bool)
            and (definition.minimum is None or definition.minimum <= value)
            and (definition.maximum is None or value <= definition.maximum)
        )
    if definition.kind == "number":
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and (definition.minimum is None or definition.minimum <= value)
            and (definition.maximum is None or value <= definition.maximum)
        )
    if definition.kind == "choice":
        return isinstance(value, str) and value in (definition.choices or ())
    if definition.kind == "json":
        if not isinstance(value, list) or len(value) > 20:
            return False
        return all(
            isinstance(item, dict)
            and set(item) == {"label", "url"}
            and isinstance(item["label"], str)
            and 1 <= len(item["label"].strip()) <= 80
            and isinstance(item["url"], str)
            and item["url"].startswith(("/", "http://", "https://"))
            and len(item["url"]) <= 2048
            for item in value
        )
    return isinstance(value, str)


async def valid_runtime_setting_combination(
    session: AsyncSession,
    settings,
    key: str,
    value: object,
) -> bool:
    """Keep per-setting writes from creating an invalid policy combination."""
    rows = {
        row.key: row.value
        for row in (
            await session.execute(
                select(GlobalSetting).where(
                    GlobalSetting.key.in_(GLOBAL_SETTING_BY_KEY)
                )
            )
        ).scalars()
    }
    overrides = {
        setting_key: setting_value(GLOBAL_SETTING_BY_KEY[setting_key], raw)
        for setting_key, raw in rows.items()
        if not GLOBAL_SETTING_BY_KEY[setting_key].secret
    }
    overrides[key] = value
    try:
        settings.__class__.model_validate({**settings.model_dump(), **overrides})
    except ValidationError:
        return False
    return True


def setting_storage_value(definition, value: object, encryption_key) -> str:
    if definition.secret:
        return str(value) if value is not None else ""
    if definition.kind == "boolean":
        return str(value).lower()
    if definition.kind == "json":
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


@router.get(
    "/settings/refresh-policy",
    response_model=RefreshPolicyResponse,
    operation_id="getRefreshPolicy",
)
async def get_refresh_policy(
    _: AdminUserDependency, tasks: TaskServiceDependency
) -> RefreshPolicyResponse:
    return RefreshPolicyResponse(
        successful_refresh_ttl_seconds=await tasks.get_successful_refresh_ttl_seconds()
    )


@router.patch(
    "/settings/refresh-policy",
    response_model=RefreshPolicyResponse,
    operation_id="updateRefreshPolicy",
)
async def update_refresh_policy(
    body: RefreshPolicyPatch,
    admin: AdminUserDependency,
    context: AuthContextDependency,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RefreshPolicyResponse:
    key = GLOBAL_SETTING_KEY
    setting = await session.get(GlobalSetting, key)
    old_value = int(setting.value) if setting is not None else 1800
    now = datetime.now(UTC)
    if setting is None:
        session.add(
            GlobalSetting(
                key=key,
                value=str(body.successful_refresh_ttl_seconds),
                version=1,
                updated_by_user_id=admin.user.id,
                updated_at=now,
            )
        )
    else:
        (
            setting.value,
            setting.version,
            setting.updated_by_user_id,
            setting.updated_at,
        ) = (
            str(body.successful_refresh_ttl_seconds),
            setting.version + 1,
            admin.user.id,
            now,
        )
    session.add(
        SecurityAuditEvent(
            id=uuid4(),
            actor_user_id=admin.user.id,
            event_type="admin.global_setting_updated",
            target_type="global_setting",
            target_id=None,
            request_id=context.request_id,
            event_metadata={
                "key": key,
                "old": old_value,
                "new": body.successful_refresh_ttl_seconds,
            },
            occurred_at=now,
        )
    )
    await session.commit()
    return RefreshPolicyResponse(
        successful_refresh_ttl_seconds=body.successful_refresh_ttl_seconds
    )


@router.get(
    "/settings",
    response_model=list[GlobalSettingResponse],
    operation_id="listGlobalSettings",
)
async def list_global_settings(
    _: AdminUserDependency,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: RuntimeSettingsDependency,
) -> list[GlobalSettingResponse]:
    rows = {
        row.key: row
        for row in (
            await session.execute(
                select(GlobalSetting).where(
                    GlobalSetting.key.in_(GLOBAL_SETTING_BY_KEY)
                )
            )
        ).scalars()
    }
    return [
        setting_response(
            definition, rows.get(definition.key), definition.default(settings)
        )
        for definition in GLOBAL_SETTINGS
    ]


@router.put(
    "/settings",
    response_model=list[GlobalSettingResponse],
    operation_id="updateGlobalSettings",
)
async def update_global_settings(
    body: GlobalSettingBatchPatch,
    admin: AdminUserDependency,
    context: AuthContextDependency,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: RuntimeSettingsDependency,
    resources: ResourcesDependency,
) -> list[GlobalSettingResponse]:
    items = {item.key: item for item in body.settings}
    if len(items) != len(body.settings):
        raise HTTPException(
            status_code=422,
            detail={"code": "validation_error", "message": "duplicate setting keys"},
        )
    for key, item in items.items():
        definition = GLOBAL_SETTING_BY_KEY.get(key)
        if definition is None:
            not_found()
        if not valid_setting_value(definition, item.value):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "validation_error",
                    "message": "setting value is invalid",
                },
            )
    rows = {
        row.key: row
        for row in (
            await session.execute(
                select(GlobalSetting).where(GlobalSetting.key.in_(items))
            )
        ).scalars()
    }
    for key, item in items.items():
        if (rows[key].version if key in rows else 0) != item.version:
            raise HTTPException(
                status_code=409,
                detail={"code": "version_conflict", "message": "setting has changed"},
            )
    overrides = {
        key: item.value
        for key, item in items.items()
        if not GLOBAL_SETTING_BY_KEY[key].secret
    }
    current = {
        row.key: setting_value(GLOBAL_SETTING_BY_KEY[row.key], row.value)
        for row in (
            await session.execute(
                select(GlobalSetting).where(
                    GlobalSetting.key.in_(GLOBAL_SETTING_BY_KEY)
                )
            )
        ).scalars()
        if not GLOBAL_SETTING_BY_KEY[row.key].secret
    }
    try:
        settings.__class__.model_validate(
            {**settings.model_dump(), **current, **overrides}
        )
    except ValidationError as error:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "validation_error",
                "message": "settings conflict with the active runtime policy",
            },
        ) from error
    now = datetime.now(UTC)
    for key, item in items.items():
        definition = GLOBAL_SETTING_BY_KEY[key]
        setting = rows.get(key)
        if definition.secret and item.value is None:
            if setting is not None:
                await session.delete(setting)
            event_type = "admin.global_setting_secret_cleared"
        else:
            stored = setting_storage_value(
                definition, item.value, settings.configuration_encryption_key
            )
            if setting is None:
                setting = GlobalSetting(
                    key=key,
                    value=stored,
                    version=1,
                    updated_by_user_id=admin.user.id,
                    updated_at=now,
                )
                session.add(setting)
                rows[key] = setting
            else:
                (
                    setting.value,
                    setting.version,
                    setting.updated_by_user_id,
                    setting.updated_at,
                ) = stored, setting.version + 1, admin.user.id, now
            event_type = "admin.global_setting_updated"
        session.add(
            SecurityAuditEvent(
                id=uuid4(),
                actor_user_id=admin.user.id,
                event_type=event_type,
                target_type="global_setting",
                target_id=None,
                request_id=context.request_id,
                event_metadata={"key": key},
                occurred_at=now,
            )
        )
    await session.commit()
    await resources.reload_global_policies()
    return [
        setting_response(
            definition, rows.get(definition.key), definition.default(settings)
        )
        for definition in GLOBAL_SETTINGS
    ]


@router.put(
    "/settings/{key}",
    response_model=GlobalSettingResponse,
    operation_id="updateGlobalSetting",
)
async def update_global_setting(
    key: str,
    body: GlobalSettingPatch,
    admin: AdminUserDependency,
    context: AuthContextDependency,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: RuntimeSettingsDependency,
    resources: ResourcesDependency,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> GlobalSettingResponse:
    definition = GLOBAL_SETTING_BY_KEY.get(key)
    if definition is None:
        not_found()
    if not valid_setting_value(definition, body.value):
        raise HTTPException(
            status_code=422,
            detail={"code": "validation_error", "message": "setting value is invalid"},
        )
    if not definition.secret and not await valid_runtime_setting_combination(
        session, settings, key, body.value
    ):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "validation_error",
                "message": "setting value conflicts with the active runtime policy",
            },
        )
    if if_match is None or not if_match.isdigit():
        raise HTTPException(
            status_code=428,
            detail={
                "code": "precondition_required",
                "message": "If-Match version is required",
            },
        )
    setting = await session.get(GlobalSetting, key)
    version = setting.version if setting else 0
    if version != int(if_match):
        raise HTTPException(
            status_code=409,
            detail={"code": "version_conflict", "message": "setting has changed"},
        )
    now = datetime.now(UTC)
    if definition.secret and body.value is None:
        if setting is not None:
            await session.delete(setting)
        session.add(
            SecurityAuditEvent(
                id=uuid4(),
                actor_user_id=admin.user.id,
                event_type="admin.global_setting_secret_cleared",
                target_type="global_setting",
                request_id=context.request_id,
                event_metadata={"key": key},
                occurred_at=now,
            )
        )
        await session.commit()
        return setting_response(definition, None, definition.default(settings))
    stored_value = setting_storage_value(
        definition, body.value, settings.configuration_encryption_key
    )
    if setting is None:
        setting = GlobalSetting(
            key=key,
            value=stored_value,
            version=1,
            updated_by_user_id=admin.user.id,
            updated_at=now,
        )
        session.add(setting)
    else:
        (
            setting.value,
            setting.version,
            setting.updated_by_user_id,
            setting.updated_at,
        ) = stored_value, version + 1, admin.user.id, now
    session.add(
        SecurityAuditEvent(
            id=uuid4(),
            actor_user_id=admin.user.id,
            event_type="admin.global_setting_updated",
            target_type="global_setting",
            request_id=context.request_id,
            event_metadata={
                "key": key,
                "old_version": version,
                "new_version": setting.version,
            },
            occurred_at=now,
        )
    )
    await session.commit()
    await resources.reload_global_policies()
    return setting_response(definition, setting, definition.default(settings))


def not_found() -> None:
    raise HTTPException(
        status_code=404,
        detail={"code": "not_found", "message": "resource was not found"},
    )


def user_response(user: AppUser, domain_count: int) -> AdminUserResponse:
    return AdminUserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        role=user.role,
        status="banned"
        if user.banned_at is not None or not user.is_active
        else "active",
        last_login_at=user.last_login_at,
        created_at=user.created_at,
        updated_at=user.updated_at,
        domain_count=domain_count,
        banned_at=user.banned_at,
        ban_reason=user.ban_reason,
    )


async def user_with_count(
    session: AsyncSession, user_id: UUID
) -> tuple[AppUser, int] | None:
    return (
        await session.execute(
            select(AppUser, func.count(ManagedDomain.id))
            .outerjoin(
                ManagedDomain,
                (ManagedDomain.user_id == AppUser.id)
                & ManagedDomain.deleted_at.is_(None),
            )
            .where(AppUser.id == user_id)
            .group_by(AppUser.id)
        )
    ).one_or_none()


@router.get("/users", response_model=AdminUserPageResponse, operation_id="listUsers")
async def list_users(
    _: AdminUserDependency,
    session: Annotated[AsyncSession, Depends(get_session)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    query: Annotated[str | None, Query(max_length=320)] = None,
    status: Literal["active", "banned"] | None = None,
    sort: Literal[
        "created_at",
        "-created_at",
        "username",
        "-username",
        "last_login_at",
        "-last_login_at",
    ] = "-created_at",
) -> AdminUserPageResponse:
    filters = []
    if query:
        filters.append(AppUser.username.ilike(f"%{query}%"))
    if status == "active":
        filters.extend([AppUser.is_active.is_(True), AppUser.banned_at.is_(None)])
    elif status == "banned":
        filters.append(AppUser.banned_at.is_not(None) | AppUser.is_active.is_(False))
    total = await session.scalar(
        select(func.count()).select_from(AppUser).where(*filters)
    )
    sort_columns = {
        "created_at": (AppUser.created_at.asc(), AppUser.id),
        "-created_at": (AppUser.created_at.desc(), AppUser.id.desc()),
        "username": (AppUser.username_normalized.asc(), AppUser.id),
        "-username": (AppUser.username_normalized.desc(), AppUser.id.desc()),
        "last_login_at": (AppUser.last_login_at.asc(), AppUser.id),
        "-last_login_at": (AppUser.last_login_at.desc(), AppUser.id.desc()),
    }
    rows = (
        await session.execute(
            select(AppUser, func.count(ManagedDomain.id))
            .outerjoin(
                ManagedDomain,
                (ManagedDomain.user_id == AppUser.id)
                & ManagedDomain.deleted_at.is_(None),
            )
            .where(*filters)
            .group_by(AppUser.id)
            .order_by(*sort_columns[sort])
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    summary_total = await session.scalar(select(func.count()).select_from(AppUser))
    summary_admins = await session.scalar(
        select(func.count()).select_from(AppUser).where(AppUser.role == "admin")
    )
    summary_banned = await session.scalar(
        select(func.count())
        .select_from(AppUser)
        .where(AppUser.banned_at.is_not(None) | AppUser.is_active.is_(False))
    )
    return AdminUserPageResponse(
        items=[user_response(user, count) for user, count in rows],
        page=page,
        page_size=page_size,
        total=total or 0,
        summary=AdminUserSummaryResponse(
            total=summary_total or 0,
            admins=summary_admins or 0,
            banned=summary_banned or 0,
        ),
    )


@router.get(
    "/users/{user_id}", response_model=AdminUserResponse, operation_id="getUserAsAdmin"
)
async def get_user(
    user_id: UUID,
    _: AdminUserDependency,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AdminUserResponse:
    row = await user_with_count(session, user_id)
    if row is None:
        not_found()
    return user_response(*row)


@router.patch(
    "/users/{user_id}",
    response_model=AdminUserResponse,
    operation_id="updateUserAsAdmin",
)
async def update_user(
    user_id: UUID,
    body: AdminUpdateUserRequest,
    admin: AdminUserDependency,
    context: AuthContextDependency,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AdminUserResponse:
    if "email" not in body.model_fields_set:
        raise HTTPException(
            status_code=422,
            detail={"code": "validation_error", "message": "email is required"},
        )
    user = await session.get(AppUser, user_id)
    if user is None:
        not_found()
    now = datetime.now(UTC)
    user.email, user.updated_at = (
        (str(body.email) if body.email is not None else None),
        now,
    )
    session.add(
        SecurityAuditEvent(
            id=uuid4(),
            actor_user_id=admin.user.id,
            event_type="admin.user_updated",
            target_type="user",
            target_id=user.id,
            request_id=context.request_id,
            occurred_at=now,
        )
    )
    await session.commit()
    row = await user_with_count(session, user_id)
    assert row is not None
    return user_response(*row)


async def set_ban_state(
    session: AsyncSession,
    user_id: UUID,
    admin_id: UUID,
    context_id: str | None,
    reason: str | None,
) -> AdminUserResponse:
    user = await session.get(AppUser, user_id)
    if user is None:
        not_found()
    now = datetime.now(UTC)
    event = "admin.user_banned" if reason else "admin.user_unbanned"
    if reason:
        changed = await session.execute(
            update(AppUser)
            .where(
                AppUser.id == user_id,
                AppUser.banned_at.is_(None),
                AppUser.is_active.is_(True),
            )
            .values(
                banned_at=now,
                ban_reason=reason,
                banned_by_user_id=admin_id,
                updated_at=now,
            )
        )
    else:
        changed = await session.execute(
            update(AppUser)
            .where(
                AppUser.id == user_id,
                AppUser.banned_at.is_not(None) | AppUser.is_active.is_(False),
            )
            .values(
                is_active=True,
                banned_at=None,
                ban_reason=None,
                banned_by_user_id=None,
                updated_at=now,
            )
        )
    if changed.rowcount == 1:
        if reason:
            session_ids = select(AuthSession.id).where(AuthSession.user_id == user_id)
            await session.execute(
                update(AuthSession)
                .where(
                    AuthSession.user_id == user_id,
                    AuthSession.revoked_at.is_(None),
                )
                .values(revoked_at=now, revoke_reason="admin_banned")
            )
            await session.execute(
                update(AuthRefreshToken)
                .where(
                    AuthRefreshToken.session_id.in_(session_ids),
                    AuthRefreshToken.revoked_at.is_(None),
                )
                .values(revoked_at=now)
            )
        session.add(
            SecurityAuditEvent(
                id=uuid4(),
                actor_user_id=admin_id,
                event_type=event,
                target_type="user",
                target_id=user_id,
                request_id=context_id,
                event_metadata={"reason": reason} if reason else {},
                occurred_at=now,
            )
        )
    await session.commit()
    row = await user_with_count(session, user_id)
    assert row is not None
    return user_response(*row)


@router.post(
    "/users/{user_id}/ban", response_model=AdminUserResponse, operation_id="banUser"
)
async def ban_user(
    user_id: UUID,
    body: BanUserRequest,
    admin: AdminUserDependency,
    context: AuthContextDependency,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AdminUserResponse:
    if user_id == admin.user.id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "cannot_ban_self",
                "message": "administrators cannot ban themselves",
            },
        )
    return await set_ban_state(
        session, user_id, admin.user.id, context.request_id, body.reason
    )


@router.post(
    "/users/{user_id}/unban", response_model=AdminUserResponse, operation_id="unbanUser"
)
async def unban_user(
    user_id: UUID,
    admin: AdminUserDependency,
    context: AuthContextDependency,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AdminUserResponse:
    return await set_ban_state(
        session, user_id, admin.user.id, context.request_id, None
    )


def session_response(auth_session: AuthSession) -> AdminSessionResponse:
    return AdminSessionResponse(
        id=auth_session.id,
        created_at=auth_session.created_at,
        last_seen_at=auth_session.last_seen_at,
        absolute_expires_at=auth_session.absolute_expires_at,
        revoked_at=auth_session.revoked_at,
        revoke_reason=auth_session.revoke_reason,
    )


@router.get(
    "/users/{user_id}/sessions",
    response_model=AdminSessionPageResponse,
    operation_id="listUserSessionsAsAdmin",
)
async def list_user_sessions(
    user_id: UUID,
    _: AdminUserDependency,
    session: Annotated[AsyncSession, Depends(get_session)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> AdminSessionPageResponse:
    if await session.get(AppUser, user_id) is None:
        not_found()
    total = await session.scalar(
        select(func.count())
        .select_from(AuthSession)
        .where(AuthSession.user_id == user_id)
    )
    rows = (
        (
            await session.execute(
                select(AuthSession)
                .where(AuthSession.user_id == user_id)
                .order_by(AuthSession.created_at.desc(), AuthSession.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return AdminSessionPageResponse(
        items=[session_response(row) for row in rows],
        page=page,
        page_size=page_size,
        total=total or 0,
    )


@router.post(
    "/users/{user_id}/sessions/{session_id}/revoke",
    response_model=AdminSessionResponse,
    operation_id="revokeUserSessionAsAdmin",
)
async def revoke_user_session(
    user_id: UUID,
    session_id: UUID,
    admin: AdminUserDependency,
    context: AuthContextDependency,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AdminSessionResponse:
    auth_session = await session.get(AuthSession, session_id)
    if auth_session is None or auth_session.user_id != user_id:
        not_found()
    if session_id == admin.session.id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "cannot_revoke_current_session",
                "message": "administrators cannot revoke their current session",
            },
        )
    now = datetime.now(UTC)
    changed = await session.execute(
        update(AuthSession)
        .where(
            AuthSession.id == session_id,
            AuthSession.user_id == user_id,
            AuthSession.revoked_at.is_(None),
        )
        .values(revoked_at=now, revoke_reason="admin_revoked")
    )
    if changed.rowcount == 1:
        await session.execute(
            update(AuthRefreshToken)
            .where(
                AuthRefreshToken.session_id == session_id,
                AuthRefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        session.add(
            SecurityAuditEvent(
                id=uuid4(),
                actor_user_id=admin.user.id,
                event_type="admin.session_revoked",
                target_type="session",
                target_id=session_id,
                request_id=context.request_id,
                event_metadata={"user_id": str(user_id)},
                occurred_at=now,
            )
        )
    await session.commit()
    await session.refresh(auth_session)
    return session_response(auth_session)


def admin_domain_response(
    domain: ManagedDomain, owner: AppUser
) -> AdminManagedDomainResponse:
    return AdminManagedDomainResponse(
        id=domain.id,
        identity={
            "ascii_name": domain.name_ascii,
            "unicode_name": domain.name_unicode,
            "registrable_domain": domain.registrable_domain,
            "public_suffix": domain.public_suffix,
            "tld": domain.tld,
        },
        monitor_enabled=domain.monitor_enabled,
        renewal_mode=domain.renewal_mode,
        notes=domain.notes,
        last_outcome=domain.last_outcome,
        registrar_expires_at=domain.registrar_expires_at,
        expiration_status=domain.expiration_status,
        expiration_checked_at=domain.expiration_checked_at,
        version=domain.version,
        created_at=domain.created_at,
        updated_at=domain.updated_at,
        owner=UserReferenceResponse(id=owner.id, username=owner.username),
        deleted_at=domain.deleted_at,
        deleted_by_user_id=domain.deleted_by_user_id,
    )


def admin_check_response(
    check: DomainCheck, domain_name: str | None
) -> AdminDomainCheckResponse:
    snapshot = check.snapshot if isinstance(check.snapshot, dict) else None
    snapshot_domain = snapshot.get("domain") if snapshot else None
    return AdminDomainCheckResponse(
        id=check.id,
        domain_id=check.managed_domain_id,
        domain_name=domain_name
        or (snapshot_domain if isinstance(snapshot_domain, str) else "")
        or "",
        checked_at=check.checked_at,
        duration_ms=check.duration_ms,
        outcome=check.outcome,
        error_code=check.error_code,
        error_message=check.error_message,
        protocol=check.protocol if check.protocol in {"rdap", "whois"} else None,
        source=check.source,
        snapshot=check.snapshot,
        changed_fields=check.changed_fields,
        is_stale=check.is_stale,
        created_at=check.created_at,
    )


@router.get(
    "/operations/metrics",
    response_model=OperationalMetricsResponse,
    operation_id="getOperationalMetrics",
)
async def get_operational_metrics(
    _: AdminUserDependency,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OperationalMetricsResponse:
    metrics = await collect_operational_metrics(session)
    return OperationalMetricsResponse(
        generated_at=metrics.generated_at,
        refresh_tasks=RefreshTaskMetricsResponse(
            queued=metrics.refresh_tasks_queued,
            running=metrics.refresh_tasks_running,
            expired_leases=metrics.refresh_tasks_expired_leases,
        ),
        notification_outbox=NotificationOutboxMetricsResponse(
            pending=metrics.notification_outbox_pending,
            running=metrics.notification_outbox_running,
            dead_letter=metrics.notification_outbox_dead_letter,
            expired_leases=metrics.notification_outbox_expired_leases,
        ),
        overdue_monitored_domains=metrics.overdue_monitored_domains,
    )


@router.get(
    "/security-audit-events",
    response_model=SecurityAuditEventPageResponse,
    operation_id="listSecurityAuditEvents",
)
async def list_security_audit_events(
    _: AdminUserDependency,
    session: Annotated[AsyncSession, Depends(get_session)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    event_type: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    actor_user_id: UUID | None = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
) -> SecurityAuditEventPageResponse:
    filters = []
    if event_type:
        filters.append(SecurityAuditEvent.event_type == event_type)
    if actor_user_id:
        filters.append(SecurityAuditEvent.actor_user_id == actor_user_id)
    if occurred_from:
        filters.append(SecurityAuditEvent.occurred_at >= occurred_from)
    if occurred_to:
        filters.append(SecurityAuditEvent.occurred_at <= occurred_to)
    total = await session.scalar(
        select(func.count()).select_from(SecurityAuditEvent).where(*filters)
    )
    events = (
        (
            await session.execute(
                select(SecurityAuditEvent)
                .where(*filters)
                .order_by(
                    SecurityAuditEvent.occurred_at.desc(), SecurityAuditEvent.id.desc()
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    actor_ids = {event.actor_user_id for event in events if event.actor_user_id}
    user_ids = {
        event.target_id
        for event in events
        if event.target_type == "user" and event.target_id
    }
    domain_ids = {
        event.target_id
        for event in events
        if event.target_type == "domain" and event.target_id
    }
    user_lookup_ids = actor_ids | user_ids
    usernames = {
        user.id: user.username
        for user in (
            (
                await session.execute(
                    select(AppUser).where(AppUser.id.in_(user_lookup_ids))
                )
            ).scalars()
            if user_lookup_ids
            else []
        )
    }
    domain_names = {
        domain.id: domain.name_unicode
        for domain in (
            (
                await session.execute(
                    select(ManagedDomain).where(ManagedDomain.id.in_(domain_ids))
                )
            ).scalars()
            if domain_ids
            else []
        )
    }
    return SecurityAuditEventPageResponse(
        items=[
            SecurityAuditEventResponse(
                id=event.id,
                event_type=event.event_type,
                actor_user_id=event.actor_user_id,
                actor_username=(
                    usernames.get(event.actor_user_id) if event.actor_user_id else None
                ),
                target_type=event.target_type,
                target_id=event.target_id,
                target_name=(
                    usernames.get(event.target_id)
                    if event.target_type == "user" and event.target_id
                    else domain_names.get(event.target_id)
                    if event.target_type == "domain" and event.target_id
                    else None
                ),
                occurred_at=event.occurred_at,
            )
            for event in events
        ],
        page=page,
        page_size=page_size,
        total=total or 0,
    )


@router.get(
    "/domain-checks",
    response_model=AdminDomainCheckPageResponse,
    operation_id="listDomainChecksAsAdmin",
)
async def list_domain_checks_as_admin(
    _: AdminUserDependency,
    session: Annotated[AsyncSession, Depends(get_session)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    domain_id: UUID | None = None,
    user_id: UUID | None = None,
    outcome: Literal[
        "success",
        "invalid_domain",
        "not_found",
        "unsupported",
        "rate_limited",
        "temporary_failure",
        "unexpected_response",
        "cancelled",
    ]
    | None = None,
    protocol: Literal["rdap", "whois"] | None = None,
    checked_from: datetime | None = None,
    checked_to: datetime | None = None,
) -> AdminDomainCheckPageResponse:
    filters = []
    if domain_id is not None:
        filters.append(DomainCheck.managed_domain_id == domain_id)
    if user_id is not None:
        filters.append(ManagedDomain.user_id == user_id)
    if outcome is not None:
        filters.append(DomainCheck.outcome == outcome)
    if protocol is not None:
        filters.append(DomainCheck.protocol == protocol)
    if checked_from is not None:
        filters.append(DomainCheck.checked_at >= checked_from)
    if checked_to is not None:
        filters.append(DomainCheck.checked_at <= checked_to)
    domain_join = ManagedDomain.id == DomainCheck.managed_domain_id
    base = (
        select(DomainCheck, ManagedDomain.name_unicode.label("domain_name"))
        .outerjoin(ManagedDomain, domain_join)
        .where(*filters)
    )
    total = await session.scalar(
        select(func.count()).select_from(
            select(DomainCheck.id)
            .outerjoin(ManagedDomain, domain_join)
            .where(*filters)
            .subquery()
        )
    )
    rows = (
        await session.execute(
            base.order_by(DomainCheck.checked_at.desc(), DomainCheck.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    grouped = (
        await session.execute(
            select(DomainCheck.outcome, func.count())
            .outerjoin(ManagedDomain, domain_join)
            .where(*filters)
            .group_by(DomainCheck.outcome)
        )
    ).all()
    return AdminDomainCheckPageResponse(
        items=[admin_check_response(row[0], row.domain_name) for row in rows],
        page=page,
        page_size=page_size,
        total=total or 0,
        statistics=CheckStatisticsResponse(
            count_by_outcome={outcome: count for outcome, count in grouped}
        ),
    )


@router.get(
    "/domains",
    response_model=AdminDomainPageResponse,
    operation_id="listDomainsAsAdmin",
)
async def list_domains_as_admin(
    _: AdminUserDependency,
    session: Annotated[AsyncSession, Depends(get_session)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    query: Annotated[str | None, Query(max_length=253)] = None,
    user_id: UUID | None = None,
    public_suffix: Annotated[str | None, Query(max_length=253)] = None,
    monitor_enabled: bool | None = None,
    expires_from: datetime | None = None,
    expires_to: datetime | None = None,
    last_outcome: Literal[
        "success",
        "invalid_domain",
        "not_found",
        "unsupported",
        "rate_limited",
        "temporary_failure",
        "unexpected_response",
        "cancelled",
    ]
    | None = None,
    deleted: Literal["exclude", "include", "only"] = "exclude",
    sort: Literal[
        "created_at",
        "-created_at",
        "name",
        "-name",
        "expires_at",
        "-expires_at",
        "last_check_at",
        "-last_check_at",
    ] = "-created_at",
) -> AdminDomainPageResponse:
    filters = []
    if query:
        filters.append(ManagedDomain.name_ascii.ilike(f"%{query}%"))
    if user_id is not None:
        filters.append(ManagedDomain.user_id == user_id)
    if public_suffix is not None:
        filters.append(ManagedDomain.public_suffix == public_suffix)
    if monitor_enabled is not None:
        filters.append(ManagedDomain.monitor_enabled.is_(monitor_enabled))
    if expires_from is not None:
        filters.append(ManagedDomain.registrar_expires_at >= expires_from)
    if expires_to is not None:
        filters.append(ManagedDomain.registrar_expires_at <= expires_to)
    if last_outcome is not None:
        filters.append(ManagedDomain.last_outcome == last_outcome)
    if deleted == "exclude":
        filters.append(ManagedDomain.deleted_at.is_(None))
    elif deleted == "only":
        filters.append(ManagedDomain.deleted_at.is_not(None))
    total = await session.scalar(
        select(func.count()).select_from(ManagedDomain).where(*filters)
    )
    sort_columns = {
        "created_at": (ManagedDomain.created_at.asc(), ManagedDomain.id),
        "-created_at": (ManagedDomain.created_at.desc(), ManagedDomain.id.desc()),
        "name": (ManagedDomain.name_ascii.asc(), ManagedDomain.id),
        "-name": (ManagedDomain.name_ascii.desc(), ManagedDomain.id.desc()),
        "expires_at": (ManagedDomain.registrar_expires_at.asc(), ManagedDomain.id),
        "-expires_at": (
            ManagedDomain.registrar_expires_at.desc(),
            ManagedDomain.id.desc(),
        ),
        "last_check_at": (ManagedDomain.last_check_at.asc(), ManagedDomain.id),
        "-last_check_at": (
            ManagedDomain.last_check_at.desc(),
            ManagedDomain.id.desc(),
        ),
    }
    rows = (
        await session.execute(
            select(ManagedDomain, AppUser)
            .join(AppUser, AppUser.id == ManagedDomain.user_id)
            .where(*filters)
            .order_by(*sort_columns[sort])
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    summary_total = await session.scalar(
        select(func.count())
        .select_from(ManagedDomain)
        .where(ManagedDomain.deleted_at.is_(None))
    )
    summary_monitored = await session.scalar(
        select(func.count())
        .select_from(ManagedDomain)
        .where(
            ManagedDomain.deleted_at.is_(None),
            ManagedDomain.monitor_enabled.is_(True),
        )
    )
    summary_deleted = await session.scalar(
        select(func.count())
        .select_from(ManagedDomain)
        .where(ManagedDomain.deleted_at.is_not(None))
    )
    return AdminDomainPageResponse(
        items=[admin_domain_response(domain, owner) for domain, owner in rows],
        page=page,
        page_size=page_size,
        total=total or 0,
        summary=AdminDomainSummaryResponse(
            total=summary_total or 0,
            monitored=summary_monitored or 0,
            deleted=summary_deleted or 0,
        ),
    )


@router.get(
    "/domains/{domain_id}",
    response_model=AdminManagedDomainResponse,
    operation_id="getDomainAsAdmin",
)
async def get_domain_as_admin(
    domain_id: UUID,
    response: Response,
    _: AdminUserDependency,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AdminManagedDomainResponse:
    row = (
        await session.execute(
            select(ManagedDomain, AppUser)
            .join(AppUser, AppUser.id == ManagedDomain.user_id)
            .where(ManagedDomain.id == domain_id)
        )
    ).one_or_none()
    if row is None:
        not_found()
    response.headers["ETag"] = f'"{row.ManagedDomain.version}"'
    return admin_domain_response(*row)


@router.patch(
    "/domains/{domain_id}",
    response_model=AdminManagedDomainResponse,
    operation_id="updateDomainAsAdmin",
)
async def update_domain_as_admin(
    domain_id: UUID,
    body: AdminUpdateDomainRequest,
    response: Response,
    admin: AdminUserDependency,
    context: AuthContextDependency,
    session: Annotated[AsyncSession, Depends(get_session)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> AdminManagedDomainResponse:
    if (
        not body.model_fields_set
        or if_match is None
        or len(if_match) < 3
        or not if_match[1:-1].isdigit()
    ):
        raise HTTPException(
            status_code=422 if body.model_fields_set else 428,
            detail={
                "code": "validation_error"
                if body.model_fields_set
                else "precondition_required",
                "message": "valid fields and If-Match are required",
            },
        )
    version, now = int(if_match[1:-1]), datetime.now(UTC)
    values = {"updated_at": now, "version": ManagedDomain.version + 1}
    for field in ("monitor_enabled", "renewal_mode", "notes"):
        if field in body.model_fields_set:
            values[field] = getattr(body, field)
    updated = await session.scalar(
        update(ManagedDomain)
        .where(ManagedDomain.id == domain_id, ManagedDomain.version == version)
        .values(**values)
        .returning(ManagedDomain.id)
    )
    if updated is None:
        if await session.get(ManagedDomain, domain_id) is None:
            not_found()
        raise HTTPException(
            status_code=409,
            detail={"code": "version_conflict", "message": "domain has changed"},
        )
    session.add(
        SecurityAuditEvent(
            id=uuid4(),
            actor_user_id=admin.user.id,
            event_type="admin.domain_updated",
            target_type="domain",
            target_id=domain_id,
            request_id=context.request_id,
            occurred_at=now,
        )
    )
    await session.commit()
    row = (
        await session.execute(
            select(ManagedDomain, AppUser)
            .join(AppUser, AppUser.id == ManagedDomain.user_id)
            .where(ManagedDomain.id == domain_id)
        )
    ).one()
    response.headers["ETag"] = f'"{row.ManagedDomain.version}"'
    return admin_domain_response(*row)


@router.delete(
    "/domains/{domain_id}", status_code=204, operation_id="deleteDomainAsAdmin"
)
async def delete_domain_as_admin(
    domain_id: UUID,
    admin: AdminUserDependency,
    context: AuthContextDependency,
    session: Annotated[AsyncSession, Depends(get_session)],
    confirm: Annotated[str | None, Header(alias="X-Confirm-Action")] = None,
) -> None:
    if confirm != "soft-delete":
        raise HTTPException(
            status_code=422,
            detail={
                "code": "validation_error",
                "message": "X-Confirm-Action must be soft-delete",
            },
        )
    domain = await session.get(ManagedDomain, domain_id)
    if domain is None:
        not_found()
    if domain.deleted_at is None:
        now = datetime.now(UTC)
        (
            domain.deleted_at,
            domain.deleted_by_user_id,
            domain.updated_at,
            domain.version,
        ) = now, admin.user.id, now, domain.version + 1
        session.add(
            SecurityAuditEvent(
                id=uuid4(),
                actor_user_id=admin.user.id,
                event_type="admin.domain_deleted",
                target_type="domain",
                target_id=domain_id,
                request_id=context.request_id,
                occurred_at=now,
            )
        )
        await session.commit()


def task_response(task) -> RefreshTaskResponse:
    error = (
        TaskErrorResponse(
            code=task.error_code, message=task.error_message or "task failed"
        )
        if task.error_code is not None
        else None
    )
    result = None
    if task.result_code is not None:
        result = TaskResultResponse(
            code=task.result_code,
            message=task.result_message,
            source_check_id=task.source_check_id,
            fresh_until=task.fresh_until,
        )
    return RefreshTaskResponse(
        id=task.id,
        status=task.status,
        domain_id=task.domain_id,
        domain_name=task.domain_name,
        check_id=task.check_id,
        error=error,
        result=result,
        created_at=task.created_at,
        started_at=task.started_at,
        completed_at=task.completed_at,
        updated_at=task.updated_at,
    )


@router.post(
    "/domains/{domain_id}/refresh",
    response_model=RefreshTaskResponse,
    status_code=202,
    operation_id="refreshDomainAsAdmin",
)
async def refresh_domain_as_admin(
    domain_id: UUID,
    request: Request,
    response: Response,
    _: AdminUserDependency,
    tasks: TaskServiceDependency,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> RefreshTaskResponse:
    if idempotency_key is None or not 8 <= len(idempotency_key) <= 128:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "validation_error",
                "message": "Idempotency-Key must be 8 to 128 characters",
            },
        )
    try:
        task = await tasks.enqueue_as_admin(
            domain_id,
            force_refresh=True,
            idempotency_key=idempotency_key,
        )
    except DomainError as error:
        if error.code == "not_found":
            not_found()
        raise HTTPException(
            status_code=422,
            detail={"code": error.code, "message": str(error)},
        ) from error
    except IdempotencyConflictError as error:
        raise HTTPException(
            status_code=409,
            detail={"code": error.code, "message": str(error)},
        ) from error
    except TaskError as error:
        raise HTTPException(
            status_code=422,
            detail={"code": error.code, "message": str(error)},
        ) from error
    response.headers["Location"] = str(request.url_for("getTask", task_id=task.id))
    return task_response(task)

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from domainsmanager_api.dependencies import (
    AdminUserDependency,
    AuthContextDependency,
    TaskServiceDependency,
    get_session,
)
from domainsmanager_api.schemas.admin import (
    AdminUpdateUserRequest,
    AdminUserPageResponse,
    AdminUserResponse,
    BanUserRequest,
)
from domainsmanager_api.schemas.admin_domains import (
    AdminDomainPageResponse,
    AdminManagedDomainResponse,
    AdminUpdateDomainRequest,
    UserReferenceResponse,
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
    ManagedDomain,
    SecurityAuditEvent,
)

router = APIRouter(prefix="/admin", tags=["Admin users", "Admin domains"])


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
    query: str | None = None,
    status: Literal["active", "banned"] | None = None,
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
            .order_by(AppUser.created_at.desc(), AppUser.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return AdminUserPageResponse(
        items=[user_response(user, count) for user, count in rows],
        page=page,
        page_size=page_size,
        total=total or 0,
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
    user.banned_at, user.ban_reason, user.banned_by_user_id, user.updated_at = (
        (now, reason, admin_id, now) if reason else (None, None, None, now)
    )
    event = "admin.user_banned" if reason else "admin.user_unbanned"
    if reason:
        session_ids = select(AuthSession.id).where(AuthSession.user_id == user_id)
        await session.execute(
            update(AuthSession)
            .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
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
        version=domain.version,
        created_at=domain.created_at,
        updated_at=domain.updated_at,
        owner=UserReferenceResponse(id=owner.id, username=owner.username),
        deleted_at=domain.deleted_at,
        deleted_by_user_id=domain.deleted_by_user_id,
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
    query: str | None = None,
    user_id: UUID | None = None,
    deleted: Literal["exclude", "include", "only"] = "exclude",
) -> AdminDomainPageResponse:
    filters = []
    if query:
        filters.append(ManagedDomain.name_ascii.ilike(f"%{query}%"))
    if user_id is not None:
        filters.append(ManagedDomain.user_id == user_id)
    if deleted == "exclude":
        filters.append(ManagedDomain.deleted_at.is_(None))
    elif deleted == "only":
        filters.append(ManagedDomain.deleted_at.is_not(None))
    total = await session.scalar(
        select(func.count()).select_from(ManagedDomain).where(*filters)
    )
    rows = (
        await session.execute(
            select(ManagedDomain, AppUser)
            .join(AppUser, AppUser.id == ManagedDomain.user_id)
            .where(*filters)
            .order_by(ManagedDomain.created_at.desc(), ManagedDomain.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return AdminDomainPageResponse(
        items=[admin_domain_response(domain, owner) for domain, owner in rows],
        page=page,
        page_size=page_size,
        total=total or 0,
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
        result = TaskResultResponse(code=task.result_code, message=task.result_message, source_check_id=task.source_check_id, fresh_until=task.fresh_until)
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

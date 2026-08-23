from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response, status

from domainsmanager_api.dependencies import CurrentUserDependency, TaskServiceDependency
from domainsmanager_api.schemas.tasks import (
    DomainCheckPageResponse,
    DomainCheckResponse,
    RefreshDomainRequest,
    RefreshTaskResponse,
    TaskErrorResponse,
)
from domainsmanager_application.domains import DomainError
from domainsmanager_application.tasks import (
    DomainCheckRecord,
    IdempotencyConflictError,
    RefreshTaskRecord,
    TaskError,
    TaskNotFoundError,
)

router = APIRouter(tags=["Domains", "Tasks"])


def task_response(task: RefreshTaskRecord) -> RefreshTaskResponse:
    error = None
    if task.error_code is not None:
        error = TaskErrorResponse(
            code=task.error_code,
            message=task.error_message or "task failed",
        )
    return RefreshTaskResponse(
        id=task.id,
        status=task.status,
        domain_id=task.domain_id,
        check_id=task.check_id,
        error=error,
        created_at=task.created_at,
        started_at=task.started_at,
        completed_at=task.completed_at,
        updated_at=task.updated_at,
    )


def check_response(check: DomainCheckRecord) -> DomainCheckResponse:
    protocol = check.protocol if check.protocol in {"rdap", "whois"} else None
    return DomainCheckResponse(
        id=check.id,
        domain_id=check.domain_id,
        checked_at=check.checked_at,
        duration_ms=check.duration_ms,
        outcome=check.outcome,
        error_code=check.error_code,
        error_message=check.error_message,
        protocol=protocol,
        source=check.source,
        snapshot=check.snapshot,
        changed_fields=check.changed_fields,
        is_stale=check.is_stale,
        created_at=check.created_at,
    )


def raise_task_error(error: DomainError | TaskError) -> None:
    if isinstance(error, (TaskNotFoundError, DomainError)):
        status_code = 404 if error.code == "not_found" else 422
    elif isinstance(error, IdempotencyConflictError):
        status_code = 409
    else:
        status_code = 422
    raise HTTPException(
        status_code=status_code,
        detail={"code": error.code, "message": str(error)},
    ) from error


@router.post(
    "/domains/{domain_id}/refresh",
    response_model=RefreshTaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="refreshDomain",
    name="refreshDomain",
)
async def refresh_domain(
    domain_id: UUID,
    body: RefreshDomainRequest,
    request: Request,
    response: Response,
    current: CurrentUserDependency,
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
        task = await tasks.enqueue(
            current.user.id,
            domain_id,
            force_refresh=body.force_refresh,
            idempotency_key=idempotency_key,
        )
    except (DomainError, TaskError) as error:
        raise_task_error(error)
    response.headers["Location"] = str(request.url_for("getTask", task_id=task.id))
    return task_response(task)


@router.get(
    "/tasks/{task_id}",
    response_model=RefreshTaskResponse,
    operation_id="getTask",
    name="getTask",
)
async def get_task(
    task_id: UUID,
    response: Response,
    current: CurrentUserDependency,
    tasks: TaskServiceDependency,
) -> RefreshTaskResponse:
    try:
        task = await tasks.get(current.user.id, task_id)
    except TaskError as error:
        raise_task_error(error)
    if task.status in {"queued", "running"}:
        response.headers["Retry-After"] = "2"
    return task_response(task)


@router.get(
    "/domains/{domain_id}/checks",
    response_model=DomainCheckPageResponse,
    operation_id="listDomainChecks",
)
async def list_domain_checks(
    domain_id: UUID,
    current: CurrentUserDependency,
    tasks: TaskServiceDependency,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    outcome: str | None = None,
    checked_from: datetime | None = None,
    checked_to: datetime | None = None,
) -> DomainCheckPageResponse:
    if (
        checked_from is not None
        and checked_to is not None
        and checked_from > checked_to
    ):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "validation_error",
                "message": "checked_from must not be after checked_to",
            },
        )
    try:
        checks = await tasks.list_checks(
            current.user.id,
            domain_id,
            page=page,
            page_size=page_size,
            outcome=outcome,
            checked_from=checked_from,
            checked_to=checked_to,
        )
    except (DomainError, TaskError) as error:
        raise_task_error(error)
    return DomainCheckPageResponse(
        items=[check_response(check) for check in checks.items],
        page=checks.page,
        page_size=checks.page_size,
        total=checks.total,
    )


@router.get(
    "/domains/{domain_id}/checks/{check_id}",
    response_model=DomainCheckResponse,
    operation_id="getDomainCheck",
)
async def get_domain_check(
    domain_id: UUID,
    check_id: UUID,
    current: CurrentUserDependency,
    tasks: TaskServiceDependency,
) -> DomainCheckResponse:
    try:
        check = await tasks.get_check(current.user.id, domain_id, check_id)
    except (DomainError, TaskError) as error:
        raise_task_error(error)
    return check_response(check)

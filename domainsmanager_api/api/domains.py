from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response, status

from domainsmanager_api.dependencies import (
    CurrentUserDependency,
    DomainServiceDependency,
)
from domainsmanager_api.schemas.domains import (
    CreateDomainRequest,
    CreateDomainResult,
    DomainIdentityResponse,
    DomainListParameters,
    DomainPageResponse,
    ManagedDomainResponse,
    UpdateDomainRequest,
)
from domainsmanager_application.domains import (
    DomainAlreadyManagedError,
    DomainError,
    DomainListQuery,
    DomainNotFoundError,
    DomainVersionConflictError,
    ManagedDomainRecord,
)

router = APIRouter(prefix="/domains", tags=["Domains"])


def domain_response(record: ManagedDomainRecord) -> ManagedDomainResponse:
    return ManagedDomainResponse(
        id=record.id,
        identity=DomainIdentityResponse(
            ascii_name=record.name_ascii,
            unicode_name=record.name_unicode,
            registrable_domain=record.registrable_domain,
            public_suffix=record.public_suffix,
            tld=record.tld,
        ),
        monitor_enabled=record.monitor_enabled,
        renewal_mode=record.renewal_mode,
        notes=record.notes,
        registered_at=record.registered_at,
        expires_at=record.expires_at,
        registry_updated_at=record.registry_updated_at,
        dnssec_enabled=record.dnssec_enabled,
        version=record.version,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def set_etag(response: Response, version: int) -> None:
    response.headers["ETag"] = f'"{version}"'


def parse_if_match(value: str | None) -> int:
    if value is None:
        raise HTTPException(
            status_code=428,
            detail={
                "code": "precondition_required",
                "message": "If-Match is required",
            },
        )
    if len(value) < 3 or value[0] != '"' or value[-1] != '"':
        raise HTTPException(
            status_code=422,
            detail={"code": "validation_error", "message": "If-Match is invalid"},
        )
    try:
        version = int(value[1:-1])
    except ValueError as error:
        raise HTTPException(
            status_code=422,
            detail={"code": "validation_error", "message": "If-Match is invalid"},
        ) from error
    if version < 1 or str(version) != value[1:-1]:
        raise HTTPException(
            status_code=422,
            detail={"code": "validation_error", "message": "If-Match is invalid"},
        )
    return version


def raise_domain_error(error: DomainError) -> None:
    if isinstance(error, DomainNotFoundError):
        status_code = 404
    elif isinstance(error, (DomainAlreadyManagedError, DomainVersionConflictError)):
        status_code = 409
    else:
        status_code = 422
    raise HTTPException(
        status_code=status_code,
        detail={"code": error.code, "message": str(error)},
    ) from error


@router.get("", response_model=DomainPageResponse, operation_id="listDomains")
async def list_domains(
    current: CurrentUserDependency,
    domains: DomainServiceDependency,
    parameters: Annotated[DomainListParameters, Query()],
) -> DomainPageResponse:
    page = await domains.list(
        current.user.id,
        DomainListQuery(**parameters.model_dump()),
    )
    return DomainPageResponse(
        items=[domain_response(record) for record in page.items],
        page=page.page,
        page_size=page.page_size,
        total=page.total,
    )


@router.post(
    "",
    response_model=CreateDomainResult,
    status_code=status.HTTP_201_CREATED,
    operation_id="createDomain",
    responses={200: {"description": "Soft-deleted domain restored"}},
)
async def create_domain(
    body: CreateDomainRequest,
    request: Request,
    response: Response,
    current: CurrentUserDependency,
    domains: DomainServiceDependency,
) -> CreateDomainResult:
    try:
        record, restored = await domains.create(
            current.user.id,
            body.name,
            monitor_enabled=body.monitor_enabled,
        )
    except DomainError as error:
        raise_domain_error(error)
    if restored:
        response.status_code = status.HTTP_200_OK
    response.headers["Location"] = str(
        request.url_for("getDomain", domain_id=record.id)
    )
    set_etag(response, record.version)
    return CreateDomainResult(domain=domain_response(record))


@router.get(
    "/{domain_id}",
    response_model=ManagedDomainResponse,
    operation_id="getDomain",
    name="getDomain",
)
async def get_domain(
    domain_id: UUID,
    response: Response,
    current: CurrentUserDependency,
    domains: DomainServiceDependency,
) -> ManagedDomainResponse:
    try:
        record = await domains.get(current.user.id, domain_id)
    except DomainError as error:
        raise_domain_error(error)
    set_etag(response, record.version)
    return domain_response(record)


@router.patch(
    "/{domain_id}",
    response_model=ManagedDomainResponse,
    operation_id="updateDomain",
)
async def update_domain(
    domain_id: UUID,
    body: UpdateDomainRequest,
    response: Response,
    current: CurrentUserDependency,
    domains: DomainServiceDependency,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> ManagedDomainResponse:
    if not body.model_fields_set:
        raise HTTPException(
            status_code=422,
            detail={"code": "validation_error", "message": "no fields supplied"},
        )
    if "monitor_enabled" in body.model_fields_set and body.monitor_enabled is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "validation_error",
                "message": "monitor_enabled cannot be null",
            },
        )
    version = parse_if_match(if_match)
    try:
        record = await domains.update(
            current.user.id,
            domain_id,
            version,
            monitor_enabled=body.monitor_enabled,
            renewal_mode=body.renewal_mode,
            notes=body.notes,
            fields=frozenset(body.model_fields_set),
        )
    except DomainError as error:
        raise_domain_error(error)
    set_etag(response, record.version)
    return domain_response(record)


@router.delete(
    "/{domain_id}", status_code=status.HTTP_204_NO_CONTENT, operation_id="deleteDomain"
)
async def delete_domain(
    domain_id: UUID,
    current: CurrentUserDependency,
    domains: DomainServiceDependency,
) -> None:
    try:
        await domains.delete(current.user.id, domain_id)
    except DomainError as error:
        raise_domain_error(error)

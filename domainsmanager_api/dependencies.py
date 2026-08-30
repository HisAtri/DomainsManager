from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from domainsmanager_api.resources import Resources
from domainsmanager_api.settings import Settings
from domainsmanager_application.domains import DomainService
from domainsmanager_application.notifications import NotificationRuleService
from domainsmanager_application.services import (
    AccountBannedError,
    AuthContext,
    AuthenticatedUser,
    AuthService,
    InvalidTokenError,
)
from domainsmanager_application.tasks import RefreshTaskService
from domainsmanager_lookup import DomainLookup


def get_resources(request: Request) -> Resources:
    return request.app.state.resources


ResourcesDependency = Annotated[Resources, Depends(get_resources)]


def get_runtime_settings(resources: ResourcesDependency) -> Settings:
    return resources.settings


RuntimeSettingsDependency = Annotated[Settings, Depends(get_runtime_settings)]
bearer_scheme = HTTPBearer(auto_error=False)


def get_auth_service(resources: ResourcesDependency) -> AuthService:
    return resources.auth


AuthServiceDependency = Annotated[AuthService, Depends(get_auth_service)]


def get_domain_service(resources: ResourcesDependency) -> DomainService:
    return resources.domains


DomainServiceDependency = Annotated[DomainService, Depends(get_domain_service)]


def get_task_service(resources: ResourcesDependency) -> RefreshTaskService:
    return resources.tasks


TaskServiceDependency = Annotated[RefreshTaskService, Depends(get_task_service)]


def get_notification_service(resources: ResourcesDependency) -> NotificationRuleService:
    return resources.notifications


NotificationServiceDependency = Annotated[NotificationRuleService, Depends(get_notification_service)]


def get_auth_context(request: Request) -> AuthContext:
    return AuthContext(
        request_id=request.state.request_id,
        user_agent=request.headers.get("user-agent"),
    )


AuthContextDependency = Annotated[AuthContext, Depends(get_auth_context)]


async def get_current_user(
    request: Request,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer_scheme),
    ],
    auth: AuthServiceDependency,
) -> AuthenticatedUser:
    if credentials is None or credentials.scheme.casefold() != "bearer":
        raise HTTPException(
            status_code=401,
            detail={"code": "invalid_token", "message": "Bearer token is required"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        current = await auth.authenticate_access_token(credentials.credentials)
    except InvalidTokenError as error:
        raise HTTPException(
            status_code=401,
            detail={"code": error.code, "message": str(error)},
            headers={"WWW-Authenticate": "Bearer"},
        ) from error
    except AccountBannedError as error:
        raise HTTPException(
            status_code=403,
            detail={"code": error.code, "message": str(error)},
        ) from error
    route = request.scope.get("route")
    policy = (
        "expensive"
        if getattr(route, "name", None)
        in {"createDomain", "refreshDomain", "refreshDomainAsAdmin"}
        else "normal"
    )
    allowed, retry_after = await request.app.state.resources.rate_limiter.consume(
        str(current.user.id), policy
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "rate_limited",
                "message": "Too many requests",
            },
            headers={"Retry-After": str(retry_after), "Cache-Control": "no-store"},
        )
    return current


CurrentUserDependency = Annotated[AuthenticatedUser, Depends(get_current_user)]


async def require_admin(current: CurrentUserDependency) -> AuthenticatedUser:
    if current.user.role != "admin":
        raise HTTPException(
            status_code=403,
            detail={"code": "forbidden", "message": "Administrator role is required"},
        )
    return current


AdminUserDependency = Annotated[AuthenticatedUser, Depends(require_admin)]


async def get_session(
    resources: ResourcesDependency,
) -> AsyncIterator[AsyncSession]:
    async with resources.sessions() as session:
        yield session


def get_domain_lookup(resources: ResourcesDependency) -> DomainLookup:
    return resources.lookup

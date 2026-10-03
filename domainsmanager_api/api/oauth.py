from __future__ import annotations

from datetime import datetime
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from domainsmanager_api.api.auth import (
    no_store,
    raise_auth_error,
    set_refresh_cookie,
    user_response,
)
from domainsmanager_api.dependencies import AuthContextDependency, CurrentUserDependency
from domainsmanager_api.schemas.auth import SetUsernameRequest, UserResponse
from domainsmanager_application.oauth import OAuthError, OAuthService, digest
from domainsmanager_application.security import (
    InvalidPasswordError,
    InvalidUsernameError,
)
from domainsmanager_application.services import (
    AccountBannedError,
    InvalidTokenError,
    RegistrationDisabledError,
    UsernameTakenError,
)

router = APIRouter(prefix="/auth", tags=["OAuth2"])


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OAuth2AvailabilityResponse(StrictModel):
    github: bool
    linuxdo: bool


class AuthorizationResponse(StrictModel):
    authorization_url: str


class LinkedIdentityResponse(StrictModel):
    provider_key: str
    provider_username: str | None
    display_name: str | None
    created_at: datetime


class AccountsResponse(StrictModel):
    items: list[LinkedIdentityResponse]
    password_auth_enabled: bool


class SetPasswordRequest(StrictModel):
    new_password: str = Field(min_length=6, max_length=256)


def service(request: Request) -> OAuthService:
    return request.app.state.resources.oauth


def oauth_http_error(error: OAuthError) -> None:
    raise HTTPException(
        error.status_code, detail={"code": error.code, "message": str(error)}
    ) from error


def cookie_name(state: str) -> str:
    return "domainsmanager_oauth_" + digest(state)[:24]


def cookie_options(request: Request) -> dict:
    settings = request.app.state.settings
    return {
        "httponly": True,
        "secure": service(request).base_url.startswith("https://"),
        "samesite": "lax",
        "path": f"{settings.api_prefix}/auth/oauth2",
    }


async def limit(request: Request) -> None:
    # 仅使用直接连接地址；代理转发地址的信任由服务器部署配置负责。
    subject = digest(request.client.host if request.client else "unknown")
    allowed, retry_after = await request.app.state.resources.rate_limiter.consume(
        "oauth:" + subject, "expensive"
    )
    if not allowed:
        raise HTTPException(
            429,
            detail={"code": "rate_limited", "message": "Too many OAuth requests"},
            headers={"Retry-After": str(retry_after)},
        )


@router.get(
    "/oauth2/availability",
    response_model=OAuth2AvailabilityResponse,
    operation_id="getOAuth2Availability",
)
async def availability(request: Request) -> OAuth2AvailabilityResponse:
    oauth = service(request)
    await oauth.load_configuration()
    return OAuth2AvailabilityResponse(
        github="github" in oauth.providers and bool(oauth.base_url),
        linuxdo="linuxdo" in oauth.providers and bool(oauth.base_url),
    )


@router.get(
    "/oauth2/accounts",
    response_model=AccountsResponse,
    operation_id="listOAuth2Accounts",
)
async def accounts(
    request: Request, current: CurrentUserDependency, response: Response
) -> AccountsResponse:
    no_store(response)
    return AccountsResponse(
        items=[
            LinkedIdentityResponse(
                provider_key=i.provider_key,
                provider_username=i.provider_username,
                display_name=i.display_name,
                created_at=i.created_at,
            )
            for i in await service(request).accounts(current.user.id)
        ],
        password_auth_enabled=current.user.password_auth_enabled,
    )


@router.post(
    "/me/username", response_model=UserResponse, operation_id="setInitialUsername"
)
async def set_initial_username(
    payload: SetUsernameRequest,
    request: Request,
    response: Response,
    current: CurrentUserDependency,
    context: AuthContextDependency,
) -> UserResponse:
    no_store(response)
    try:
        return user_response(
            await service(request).set_username(current, payload.username, context)
        )
    except OAuthError as error:
        oauth_http_error(error)
    except (InvalidUsernameError, UsernameTakenError, AccountBannedError) as error:
        raise_auth_error(error)


async def authorize(provider: str, request: Request) -> Response:
    await limit(request)
    try:
        url, state, browser = await service(request).begin(provider)
    except OAuthError as error:
        oauth_http_error(error)
    response = RedirectResponse(url, status_code=302)
    response.set_cookie(
        cookie_name(state),
        browser,
        max_age=service(request).ttl,
        **cookie_options(request),
    )
    no_store(response)
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


async def link(
    provider: str, request: Request, response: Response, current: CurrentUserDependency
) -> AuthorizationResponse:
    await limit(request)
    try:
        url, state, browser = await service(request).begin(provider, current)
    except OAuthError as error:
        oauth_http_error(error)
    response.set_cookie(
        cookie_name(state),
        browser,
        max_age=service(request).ttl,
        **cookie_options(request),
    )
    no_store(response)
    return AuthorizationResponse(authorization_url=url)


async def callback(
    provider: str,
    request: Request,
    context: AuthContextDependency,
    state: Annotated[str, Query(max_length=512)] = "",
    code: Annotated[str | None, Query(max_length=2048)] = None,
    error: Annotated[str | None, Query(max_length=256)] = None,
) -> Response:
    await limit(request)
    oauth = service(request)
    await oauth.load_configuration()
    result = None
    try:
        result, target = await oauth.complete(
            provider,
            state,
            request.cookies.get(cookie_name(state), ""),
            code,
            error,
            request.cookies.get(request.app.state.settings.refresh_cookie_name),
            context,
        )
        fragment = (
            target + "?" + urlencode({"oauth": "success" if result else "linked"})
        )
    except (
        OAuthError,
        RegistrationDisabledError,
        AccountBannedError,
        InvalidTokenError,
    ) as problem:
        fragment = "dashboard?" + urlencode({"oauth_error": problem.code})
    response = RedirectResponse(oauth.base_url + "/#" + fragment, status_code=303)
    response.delete_cookie(cookie_name(state), **cookie_options(request))
    if result:
        set_refresh_cookie(response, result.tokens.refresh_token, request)
    no_store(response)
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


async def unlink(
    provider: str,
    request: Request,
    current: CurrentUserDependency,
    context: AuthContextDependency,
) -> None:
    await service(request).load_configuration()
    try:
        await service(request).unlink(provider, current, context)
    except OAuthError as error:
        oauth_http_error(error)
    except AccountBannedError as error:
        raise_auth_error(error)


@router.get(
    "/oauth2/github/authorize", status_code=302, operation_id="beginGitHubAuthorization"
)
async def authorize_github(request: Request) -> Response:
    return await authorize("github", request)


@router.post(
    "/oauth2/github/authorize",
    response_model=AuthorizationResponse,
    operation_id="beginGitHubLink",
)
async def link_github(
    request: Request, response: Response, current: CurrentUserDependency
) -> AuthorizationResponse:
    return await link("github", request, response, current)


@router.get(
    "/oauth2/github/callback",
    status_code=303,
    operation_id="completeGitHubAuthorization",
)
async def callback_github(
    request: Request,
    context: AuthContextDependency,
    state: Annotated[str, Query(max_length=512)] = "",
    code: Annotated[str | None, Query(max_length=2048)] = None,
    error: Annotated[str | None, Query(max_length=256)] = None,
) -> Response:
    return await callback("github", request, context, state, code, error)


@router.delete("/oauth2/github", status_code=204, operation_id="unlinkGitHubIdentity")
async def unlink_github(
    request: Request, current: CurrentUserDependency, context: AuthContextDependency
) -> None:
    await unlink("github", request, current, context)


@router.get(
    "/oauth2/linuxdo/authorize",
    status_code=302,
    operation_id="beginLinuxDoAuthorization",
)
async def authorize_linuxdo(request: Request) -> Response:
    return await authorize("linuxdo", request)


@router.post(
    "/oauth2/linuxdo/authorize",
    response_model=AuthorizationResponse,
    operation_id="beginLinuxDoLink",
)
async def link_linuxdo(
    request: Request, response: Response, current: CurrentUserDependency
) -> AuthorizationResponse:
    return await link("linuxdo", request, response, current)


@router.get(
    "/oauth2/linuxdo/callback",
    status_code=303,
    operation_id="completeLinuxDoAuthorization",
)
async def callback_linuxdo(
    request: Request,
    context: AuthContextDependency,
    state: Annotated[str, Query(max_length=512)] = "",
    code: Annotated[str | None, Query(max_length=2048)] = None,
    error: Annotated[str | None, Query(max_length=256)] = None,
) -> Response:
    return await callback("linuxdo", request, context, state, code, error)


@router.delete("/oauth2/linuxdo", status_code=204, operation_id="unlinkLinuxDoIdentity")
async def unlink_linuxdo(
    request: Request, current: CurrentUserDependency, context: AuthContextDependency
) -> None:
    await unlink("linuxdo", request, current, context)


@router.post("/me/password/set", operation_id="setInitialPassword", status_code=204)
async def set_password(
    body: SetPasswordRequest,
    request: Request,
    current: CurrentUserDependency,
    context: AuthContextDependency,
) -> None:
    try:
        await service(request).set_password(current, body.new_password, context)
    except OAuthError as error:
        oauth_http_error(error)
    except (InvalidPasswordError, AccountBannedError) as error:
        raise_auth_error(error)

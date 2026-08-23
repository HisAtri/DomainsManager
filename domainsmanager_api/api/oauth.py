from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from domainsmanager_api.settings import Settings

router = APIRouter(prefix="/auth/oauth2", tags=["OAuth2"])


class OAuth2ProviderResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    display_name: str


class OAuth2ProviderListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[OAuth2ProviderResponse]


def provider_list(settings: Settings) -> OAuth2ProviderListResponse:
    return OAuth2ProviderListResponse(
        items=[
            OAuth2ProviderResponse(key=provider, display_name=provider.title())
            for provider in settings.oauth_providers
        ]
    )


def unavailable(provider: str) -> None:
    raise HTTPException(
        status_code=404,
        detail={
            "code": "oauth_provider_not_found",
            "message": f"OAuth provider {provider} is not configured",
        },
    )


@router.get(
    "/providers",
    response_model=OAuth2ProviderListResponse,
    operation_id="listOAuth2Providers",
)
async def list_providers(request: Request) -> OAuth2ProviderListResponse:
    return provider_list(request.app.state.settings)


@router.get("/{provider}/authorize", operation_id="beginOAuth2Authorization")
async def authorize(provider: str) -> None:
    unavailable(provider)


@router.get("/{provider}/callback", operation_id="completeOAuth2Authorization")
async def callback(provider: str) -> None:
    unavailable(provider)

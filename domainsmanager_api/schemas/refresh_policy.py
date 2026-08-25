from pydantic import Field

from domainsmanager_api.schemas.domains import StrictModel


class RefreshPolicyResponse(StrictModel):
    successful_refresh_ttl_seconds: int = Field(ge=60, le=2_592_000)


class RefreshPolicyPatch(StrictModel):
    successful_refresh_ttl_seconds: int = Field(ge=60, le=2_592_000)

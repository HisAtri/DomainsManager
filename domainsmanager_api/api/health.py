from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from domainsmanager_api.dependencies import ResourcesDependency
from domainsmanager_api.errors import error_response

router = APIRouter(prefix="/health", tags=["Health"])


class HealthStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"] = "ok"


@router.get(
    "/live",
    response_model=HealthStatus,
    operation_id="getLiveness",
)
async def live() -> HealthStatus:
    return HealthStatus()


@router.get(
    "/ready",
    response_model=HealthStatus,
    operation_id="getReadiness",
    responses={503: {"description": "Database is unavailable"}},
)
async def ready(
    request: Request,
    resources: ResourcesDependency,
) -> HealthStatus | JSONResponse:
    if not await resources.database_ready():
        return error_response(
            request,
            status_code=503,
            code="service_unavailable",
            message="Database is unavailable",
        )
    return HealthStatus()

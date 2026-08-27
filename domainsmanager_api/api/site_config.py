from __future__ import annotations

import hashlib
import json
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from domainsmanager_api.dependencies import RuntimeSettingsDependency, get_session
from domainsmanager_api.global_setting_registry import SITE_SETTINGS
from domainsmanager_api.schemas.site_config import PublicSiteConfig
from domainsmanager_persistence.models import GlobalSetting

router = APIRouter(prefix="/site", tags=["Site configuration"])


@router.get("/config", response_model=PublicSiteConfig, operation_id="getPublicSiteConfig")
async def get_public_site_config(
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: RuntimeSettingsDependency,
) -> PublicSiteConfig | Response:
    rows = {
        row.key: row
        for row in (
            await session.execute(
                select(GlobalSetting).where(GlobalSetting.key.in_([item.key for item in SITE_SETTINGS]))
            )
        ).scalars()
    }
    values: dict[str, object] = {}
    revisions: list[str] = []
    for definition in SITE_SETTINGS:
        row = rows.get(definition.key)
        if row is None:
            values[definition.key] = definition.default(settings)
        elif definition.kind == "json":
            values[definition.key] = json.loads(row.value)
            revisions.append(f"{row.key}:{row.version}")
        else:
            values[definition.key] = row.value
            revisions.append(f"{row.key}:{row.version}")
    revision = hashlib.sha256("|".join(revisions).encode()).hexdigest()[:16]
    etag = f'"{revision}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    response.headers["ETag"] = etag
    return PublicSiteConfig(revision=revision, **values)

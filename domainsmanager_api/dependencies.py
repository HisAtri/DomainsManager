from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from domainsmanager_lookup import DomainLookup

from domainsmanager_api.resources import Resources


def get_resources(request: Request) -> Resources:
    return request.app.state.resources


ResourcesDependency = Annotated[Resources, Depends(get_resources)]


async def get_session(
    resources: ResourcesDependency,
) -> AsyncIterator[AsyncSession]:
    async with resources.sessions() as session:
        yield session


def get_domain_lookup(resources: ResourcesDependency) -> DomainLookup:
    return resources.lookup

from __future__ import annotations

import asyncio

from sqlalchemy import select

from domainsmanager_api.resources import create_resources
from domainsmanager_api.settings import get_settings
from domainsmanager_application.domains import DomainAlreadyManagedError
from domainsmanager_application.services import AuthContext, UsernameTakenError
from domainsmanager_persistence.models import AppUser


async def run() -> None:
    settings = get_settings().model_copy(update={"registration_enabled": True})
    resources = create_resources(settings)
    try:
        for username, password in (
            ("demo-admin", "demo-admin-123"),
            ("demo-user", "demo-user-123"),
            ("demo-viewer", "demo-viewer-123"),
        ):
            try:
                result = await resources.auth.register(
                    username, password, None, AuthContext(request_id="seed")
                )
            except UsernameTakenError:
                continue
            if username == "demo-admin":
                async with resources.sessions() as session, session.begin():
                    user = await session.get(AppUser, result.user.id)
                    assert user is not None
                    user.role = "admin"
        async with resources.sessions() as session:
            users = (
                (
                    await session.execute(
                        select(AppUser).where(AppUser.username == "demo-user")
                    )
                )
                .scalars()
                .all()
            )
        if users:
            for name in ("example.com", "example.org", "example.net"):
                try:
                    await resources.domains.create(users[0].id, name)
                except DomainAlreadyManagedError:
                    continue
    finally:
        await resources.close()


def main() -> None:
    asyncio.run(run())

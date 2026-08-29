from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from domainsmanager_api.main import create_app
from domainsmanager_api.settings import Settings
from domainsmanager_persistence.db import (
    create_engine,
    create_session_factory,
    run_migrations,
)
from domainsmanager_persistence.models import (
    DomainCheck,
    ManagedDomain,
    SecurityAuditEvent,
)
from tests.database import sqlite_database


async def make_client(tmp_path: Path) -> TestClient:
    database = tmp_path / "admin-api.db"
    await run_migrations(sqlite_database(database))
    return TestClient(
        create_app(
            Settings(
                _env_file=None,
                database_type="sqlite",
                database_path=str(database),
                jwt_secret_key="x",
                refresh_token_pepper="y",
                registration_enabled=True,
                bootstrap_admin_username="admin",
                bootstrap_admin_password="123456",
            )
        )
    )


def login(client: TestClient, username: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login", data={"username": username, "password": "123456"}
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['tokens']['access_token']}"}


@pytest.mark.asyncio
@pytest.mark.api
async def test_admin_security_audit_events_are_filterable_and_sanitized(
    tmp_path: Path,
) -> None:
    client = await make_client(tmp_path)
    with client:
        admin = login(client, "admin")
        member_response = client.post(
            "/api/v1/auth/register",
            json={"username": "audit-member", "password": "123456"},
        )
        member = {
            "Authorization": f"Bearer {member_response.json()['tokens']['access_token']}"
        }
        admin_id = UUID(client.get("/api/v1/auth/me", headers=admin).json()["id"])
        now = datetime(2026, 8, 28, tzinfo=UTC)
        engine = create_engine(sqlite_database(tmp_path / "admin-api.db"))
        try:
            async with create_session_factory(engine).begin() as session:
                session.add_all(
                    [
                        SecurityAuditEvent(
                            id=uuid4(),
                            actor_user_id=admin_id,
                            event_type="admin.user_banned",
                            target_type="user",
                            target_id=uuid4(),
                            request_id="request-secret",
                            ip_hash="ip-secret",
                            event_metadata={"secret": "do-not-return"},
                            occurred_at=now,
                        ),
                        SecurityAuditEvent(
                            id=uuid4(),
                            actor_user_id=admin_id,
                            event_type="admin.user_unbanned",
                            target_type="user",
                            target_id=uuid4(),
                            event_metadata={},
                            occurred_at=now.replace(day=27),
                        ),
                    ]
                )
        finally:
            await engine.dispose()

        assert (
            client.get(
                "/api/v1/admin/security-audit-events", headers=member
            ).status_code
            == 403
        )
        response = client.get(
            "/api/v1/admin/security-audit-events",
            params={"event_type": "admin.user_banned", "actor_user_id": str(admin_id)},
            headers=admin,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["event_type"] == "admin.user_banned"
        assert body["items"][0]["actor_username"] == "admin"
        assert body["items"][0]["actor_user_id"] == str(admin_id)
        assert "event_metadata" not in body["items"][0]
        assert "ip_hash" not in body["items"][0]
        assert "request_id" not in body["items"][0]


@pytest.mark.asyncio
@pytest.mark.api
async def test_admin_user_and_domain_access(tmp_path: Path) -> None:
    client = await make_client(tmp_path)
    with client:
        admin = login(client, "admin")
        registered = client.post(
            "/api/v1/auth/register",
            json={"username": "member", "password": "123456"},
        )
        assert registered.status_code == 201
        member_id = registered.json()["user"]["id"]
        member = {
            "Authorization": f"Bearer {registered.json()['tokens']['access_token']}"
        }
        domain = client.post(
            "/api/v1/domains", json={"name": "example.com"}, headers=member
        ).json()["domain"]

        assert client.get("/api/v1/admin/users", headers=member).status_code == 403
        users = client.get("/api/v1/admin/users", headers=admin)
        assert users.status_code == 200
        assert users.json()["total"] == 2
        assert users.json()["summary"] == {"total": 2, "admins": 1, "banned": 0}
        domains = client.get("/api/v1/admin/domains", headers=admin)
        assert domains.status_code == 200
        assert domains.json()["items"][0]["id"] == domain["id"]
        assert domains.json()["summary"] == {"total": 1, "monitored": 1, "deleted": 0}
        assert domains.json()["items"][0]["last_outcome"] is None
        engine = create_engine(sqlite_database(tmp_path / "admin-api.db"))
        async with engine.begin() as connection:
            await connection.execute(
                update(ManagedDomain)
                .where(ManagedDomain.id == UUID(domain["id"]))
                .values(last_outcome="not_found")
            )
        await engine.dispose()
        failed_domains = client.get("/api/v1/admin/domains", headers=admin)
        assert failed_domains.json()["items"][0]["last_outcome"] == "not_found"
        queued = client.post(
            f"/api/v1/admin/domains/{domain['id']}/refresh",
            headers={**admin, "Idempotency-Key": "admin-refresh-123"},
        )
        assert queued.status_code == 202
        assert queued.json()["domain_id"] == domain["id"]
        checks = client.get(
            f"/api/v1/admin/domain-checks?domain_id={domain['id']}", headers=admin
        )
        assert checks.status_code == 200
        assert checks.json() == {
            "items": [],
            "page": 1,
            "page_size": 20,
            "total": 0,
            "statistics": {"count_by_outcome": {}},
        }
        assert (
            client.get("/api/v1/admin/operations/metrics", headers=member).status_code
            == 403
        )
        metrics = client.get("/api/v1/admin/operations/metrics", headers=admin)
        assert metrics.status_code == 200
        assert metrics.json()["refresh_tasks"] == {
            "queued": 2,
            "running": 0,
            "expired_leases": 0,
        }
        assert metrics.json()["notification_outbox"] == {
            "pending": 0,
            "running": 0,
            "dead_letter": 0,
            "expired_leases": 0,
        }
        assert metrics.json()["overdue_monitored_domains"] == 0

        banned = client.post(
            f"/api/v1/admin/users/{member_id}/ban",
            json={"reason": "test ban"},
            headers=admin,
        )
        assert banned.status_code == 200
        assert banned.json()["status"] == "banned"
        assert client.get("/api/v1/admin/users", headers=admin).json()["summary"]["banned"] == 1
        repeated_ban = client.post(
            f"/api/v1/admin/users/{member_id}/ban",
            json={"reason": "test ban"},
            headers=admin,
        )
        assert repeated_ban.status_code == 200
        assert client.get("/api/v1/auth/me", headers=member).status_code == 403

        unbanned = client.post(f"/api/v1/admin/users/{member_id}/unban", headers=admin)
        assert unbanned.status_code == 200
        assert unbanned.json()["status"] == "active"
        repeated_unban = client.post(
            f"/api/v1/admin/users/{member_id}/unban", headers=admin
        )
        assert repeated_unban.status_code == 200
        member = login(client, "member")
        sessions = client.get(
            f"/api/v1/admin/users/{member_id}/sessions", headers=admin
        )
        assert sessions.status_code == 200
        assert sessions.json()["total"] == 2
        session_id = next(
            item["id"]
            for item in sessions.json()["items"]
            if item["revoked_at"] is None
        )
        revoked = client.post(
            f"/api/v1/admin/users/{member_id}/sessions/{session_id}/revoke",
            headers=admin,
        )
        assert revoked.status_code == 200
        assert revoked.json()["revoke_reason"] == "admin_revoked"
        repeated_revoke = client.post(
            f"/api/v1/admin/users/{member_id}/sessions/{session_id}/revoke",
            headers=admin,
        )
        assert repeated_revoke.status_code == 200
        assert client.get("/api/v1/auth/me", headers=member).status_code == 401

        engine = create_engine(sqlite_database(tmp_path / "admin-api.db"))
        async with create_session_factory(engine)() as session:
            audit_counts = dict(
                (
                    await session.execute(
                        select(SecurityAuditEvent.event_type, func.count())
                        .where(
                            SecurityAuditEvent.event_type.in_(
                                {
                                    "admin.user_banned",
                                    "admin.user_unbanned",
                                    "admin.session_revoked",
                                }
                            )
                        )
                        .group_by(SecurityAuditEvent.event_type)
                    )
                ).all()
            )
        await engine.dispose()
        assert audit_counts == {
            "admin.session_revoked": 1,
            "admin.user_banned": 1,
            "admin.user_unbanned": 1,
        }


@pytest.mark.asyncio
@pytest.mark.api
async def test_admin_list_filters_and_stable_sorting(tmp_path: Path) -> None:
    client = await make_client(tmp_path)
    with client:
        admin = login(client, "admin")
        domains: dict[str, dict] = {}
        for username, name in (("zebra", "zeta.net"), ("alpha", "alpha.com")):
            registered = client.post(
                "/api/v1/auth/register",
                json={"username": username, "password": "123456"},
            )
            headers = {
                "Authorization": f"Bearer {registered.json()['tokens']['access_token']}"
            }
            domains[name] = client.post(
                "/api/v1/domains",
                json={"name": name},
                headers=headers,
            ).json()["domain"]

        checked_early = datetime(2026, 1, 1, tzinfo=UTC)
        checked_late = datetime(2026, 6, 1, tzinfo=UTC)
        engine = create_engine(sqlite_database(tmp_path / "admin-api.db"))
        async with create_session_factory(engine)() as session:
            alpha_id = UUID(domains["alpha.com"]["id"])
            zeta_id = UUID(domains["zeta.net"]["id"])
            await session.execute(
                update(ManagedDomain)
                .where(ManagedDomain.id == alpha_id)
                    .values(
                        expires_at=datetime(2026, 12, 1, tzinfo=UTC),
                        registrar_expires_at=datetime(2026, 12, 1, tzinfo=UTC),
                        last_check_at=checked_late,
                    last_outcome="success",
                )
            )
            await session.execute(
                update(ManagedDomain)
                .where(ManagedDomain.id == zeta_id)
                .values(
                        monitor_enabled=False,
                        expires_at=datetime(2027, 12, 1, tzinfo=UTC),
                        registrar_expires_at=datetime(2027, 12, 1, tzinfo=UTC),
                        last_check_at=checked_early,
                    last_outcome="not_found",
                )
            )
            session.add_all(
                [
                    DomainCheck(
                        id=uuid4(),
                        managed_domain_id=zeta_id,
                        checked_at=checked_early,
                        outcome="unsupported",
                        error_code="unsupported",
                        error_message="TLD is not supported",
                        protocol="whois",
                        snapshot=None,
                        changed_fields=[],
                        is_stale=False,
                        created_at=checked_early,
                    ),
                    DomainCheck(
                        id=uuid4(),
                        managed_domain_id=alpha_id,
                        checked_at=checked_late,
                        outcome="success",
                        protocol="rdap",
                        snapshot={"domain": "alpha.com"},
                        changed_fields=[],
                        is_stale=False,
                        created_at=checked_late,
                    ),
                ]
            )
            await session.commit()
        await engine.dispose()

        users = client.get("/api/v1/admin/users?sort=username", headers=admin).json()
        assert [item["username"] for item in users["items"]] == [
            "admin",
            "alpha",
            "zebra",
        ]

        ordered = client.get("/api/v1/admin/domains?sort=name", headers=admin).json()
        assert [item["identity"]["ascii_name"] for item in ordered["items"]] == [
            "alpha.com",
            "zeta.net",
        ]
        assert ordered["summary"] == {"total": 2, "monitored": 1, "deleted": 0}
        filtered = client.get(
            "/api/v1/admin/domains"
            "?public_suffix=com&monitor_enabled=true&last_outcome=success"
            "&expires_from=2026-01-01T00:00:00Z&expires_to=2026-12-31T23:59:59Z",
            headers=admin,
        ).json()
        assert filtered["total"] == 1
        assert filtered["items"][0]["id"] == domains["alpha.com"]["id"]

        checks = client.get(
            "/api/v1/admin/domain-checks"
            "?checked_from=2026-02-01T00:00:00Z&checked_to=2026-12-31T23:59:59Z",
            headers=admin,
        ).json()
        assert checks["total"] == 1
        assert checks["items"][0]["domain_id"] == domains["alpha.com"]["id"]
        assert checks["items"][0]["domain_name"] == "alpha.com"
        assert checks["statistics"]["count_by_outcome"] == {"success": 1}

        failed = client.get(
            "/api/v1/admin/domain-checks?outcome=unsupported",
            headers=admin,
        ).json()
        assert failed["total"] == 1
        assert failed["items"][0]["domain_id"] == domains["zeta.net"]["id"]
        assert failed["items"][0]["domain_name"] == "zeta.net"
        assert failed["items"][0]["snapshot"] is None
        assert failed["items"][0]["error_code"] == "unsupported"

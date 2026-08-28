from datetime import UTC, datetime, timedelta

import pytest

from domainsmanager_lookup._internal.lifecycle import (
    ExpirationStatus,
    determine_expiration_status,
)

NOW = datetime(2026, 8, 28, tzinfo=UTC)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("registry_exists", "registry_expires_at", "registrar_expires_at", "expected"),
    [
        (False, None, None, ExpirationStatus.RELEASED),
        (True, None, None, ExpirationStatus.UNKNOWN),
        (
            True,
            NOW + timedelta(days=365),
            NOW + timedelta(days=365),
            ExpirationStatus.ACTIVE,
        ),
        (
            True,
            NOW + timedelta(days=365),
            NOW - timedelta(seconds=1),
            ExpirationStatus.GRACE_PERIOD,
        ),
        (
            True,
            NOW - timedelta(seconds=1),
            NOW - timedelta(seconds=1),
            ExpirationStatus.EXPIRED,
        ),
    ],
)
def test_expiration_status_uses_only_rdap_availability_and_dates(
    registry_exists: bool,
    registry_expires_at: datetime | None,
    registrar_expires_at: datetime | None,
    expected: ExpirationStatus,
) -> None:
    assert (
        determine_expiration_status(
            registry_exists=registry_exists,
            registry_expires_at=registry_expires_at,
            registrar_expires_at=registrar_expires_at,
            now=NOW,
        )
        is expected
    )

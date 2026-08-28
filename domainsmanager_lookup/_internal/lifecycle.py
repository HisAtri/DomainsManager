from datetime import datetime
from enum import StrEnum


class ExpirationStatus(StrEnum):
    ACTIVE = "active"
    GRACE_PERIOD = "grace_period"
    EXPIRED = "expired"
    RELEASED = "released"
    UNKNOWN = "unknown"


def determine_expiration_status(
    *,
    registry_exists: bool,
    registry_expires_at: datetime | None,
    registrar_expires_at: datetime | None,
    now: datetime,
) -> ExpirationStatus:
    """Classify lifecycle state exclusively from RDAP availability and dates."""
    if not registry_exists:
        return ExpirationStatus.RELEASED
    if registrar_expires_at is None:
        return ExpirationStatus.UNKNOWN
    if registrar_expires_at > now:
        return ExpirationStatus.ACTIVE
    if registry_expires_at is not None and registry_expires_at > now:
        return ExpirationStatus.GRACE_PERIOD
    return ExpirationStatus.EXPIRED

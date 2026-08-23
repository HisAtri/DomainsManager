from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from domainsmanager_application.security import (
    AccessTokenService,
    InvalidAccessTokenError,
    InvalidPasswordError,
    PasswordService,
    RefreshTokenService,
    normalize_username,
    validate_password,
)

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.mark.unit
@pytest.mark.parametrize("length", [6, 256])
def test_password_length_boundaries_are_accepted(length: int) -> None:
    validate_password("x" * length)


@pytest.mark.unit
@pytest.mark.parametrize("length", [0, 5, 257])
def test_password_outside_length_boundaries_is_rejected(length: int) -> None:
    with pytest.raises(InvalidPasswordError):
        validate_password("x" * length)


@pytest.mark.unit
def test_password_service_hashes_and_verifies_without_complexity_rules() -> None:
    service = PasswordService()
    password_hash = service.hash("123456")

    assert service.verify("123456", password_hash)
    assert not service.verify("654321", password_hash)


@pytest.mark.unit
def test_username_normalization_is_case_insensitive() -> None:
    assert normalize_username(" Test.User ") == ("Test.User", "test.user")


@pytest.mark.unit
def test_access_token_accepts_short_nonempty_secret_and_validates_time() -> None:
    service = AccessTokenService(
        secret="x",
        issuer="issuer",
        audience="audience",
        ttl=timedelta(minutes=15),
        clock_skew=timedelta(0),
    )
    user_id = uuid4()
    session_id = uuid4()
    password_changed_at = NOW - timedelta(hours=1)
    token = service.issue(user_id, session_id, password_changed_at, NOW)

    claims = service.decode(token, NOW + timedelta(minutes=1))

    assert claims.user_id == user_id
    assert claims.session_id == session_id
    assert claims.password_version == int(password_changed_at.timestamp() * 1_000_000)
    with pytest.raises(InvalidAccessTokenError):
        service.decode(token, NOW + timedelta(minutes=15))


@pytest.mark.unit
def test_access_token_preserves_microsecond_password_version() -> None:
    service = AccessTokenService(
        secret="x",
        issuer="issuer",
        audience="audience",
        ttl=timedelta(minutes=15),
    )
    password_changed_at = NOW.replace(microsecond=123456)
    token = service.issue(uuid4(), uuid4(), password_changed_at, NOW)

    claims = service.decode(token, NOW)

    assert claims.password_version == int(password_changed_at.timestamp() * 1_000_000)


@pytest.mark.unit
def test_refresh_token_digest_detects_tampering() -> None:
    service = RefreshTokenService("x")
    issued = service.issue()
    token_id, digest = service.parse(issued.value)

    assert token_id == issued.token_id
    assert service.matches(issued.digest, digest)
    _, secret = issued.value.split(".", 1)
    _, tampered = service.parse(f"{issued.token_id}.{secret}x")
    assert not service.matches(issued.digest, tampered)

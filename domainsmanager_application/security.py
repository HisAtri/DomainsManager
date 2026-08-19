from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import warnings
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import jwt
from jwt.warnings import InsecureKeyLengthWarning
from pwdlib import PasswordHash

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,128}$")
PASSWORD_MIN_LENGTH = 6
PASSWORD_MAX_LENGTH = 256


class InvalidUsernameError(ValueError):
    pass


class InvalidPasswordError(ValueError):
    pass


class InvalidAccessTokenError(ValueError):
    pass


class InvalidRefreshTokenError(ValueError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_username(username: str) -> tuple[str, str]:
    display = username.strip()
    if not USERNAME_PATTERN.fullmatch(display):
        raise InvalidUsernameError(
            "username must contain 3-128 ASCII letters, digits, dots, dashes, or underscores"
        )
    return display, display.casefold()


def validate_password(password: str) -> None:
    if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
        raise InvalidPasswordError("password must contain 6-256 characters")


class PasswordService:
    def __init__(self) -> None:
        self._passwords = PasswordHash.recommended()
        self._dummy_hash = self._passwords.hash("dummy-password-not-used")

    def hash(self, password: str) -> str:
        validate_password(password)
        return self._passwords.hash(password)

    def verify(self, password: str, password_hash: str) -> bool:
        try:
            return self._passwords.verify(password, password_hash)
        except Exception:
            return False

    def verify_dummy(self, password: str) -> None:
        self.verify(password, self._dummy_hash)


@dataclass(frozen=True, slots=True)
class AccessClaims:
    user_id: UUID
    session_id: UUID
    issued_at: datetime
    expires_at: datetime
    token_id: UUID
    password_version: int


class AccessTokenService:
    def __init__(
        self,
        *,
        secret: str,
        issuer: str,
        audience: str,
        ttl: timedelta,
        clock_skew: timedelta = timedelta(seconds=30),
    ) -> None:
        if not secret:
            raise ValueError("JWT secret must not be empty")
        self._secret = secret
        self._issuer = issuer
        self._audience = audience
        self._ttl = ttl
        self._clock_skew = clock_skew

    def issue(
        self,
        user_id: UUID,
        session_id: UUID,
        password_changed_at: datetime,
        now: datetime,
    ) -> str:
        expires_at = now + self._ttl
        token_id = uuid4()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", InsecureKeyLengthWarning)
            return jwt.encode(
                {
                    "iss": self._issuer,
                    "aud": self._audience,
                    "sub": str(user_id),
                    "sid": str(session_id),
                    "jti": str(token_id),
                    "pwd": int(password_changed_at.timestamp() * 1_000_000),
                    "iat": now,
                    "nbf": now,
                    "exp": expires_at,
                },
                self._secret,
                algorithm="HS256",
            )

    def decode(self, token: str, now: datetime | None = None) -> AccessClaims:
        effective_now = now or utc_now()
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", InsecureKeyLengthWarning)
                payload = jwt.decode(
                    token,
                    self._secret,
                    algorithms=["HS256"],
                    audience=self._audience,
                    issuer=self._issuer,
                    options={
                        "verify_exp": False,
                        "verify_iat": False,
                        "verify_nbf": False,
                        "require": [
                            "iss",
                            "aud",
                            "sub",
                            "sid",
                            "jti",
                            "pwd",
                            "iat",
                            "nbf",
                            "exp",
                        ],
                    },
                )
            issued_at = datetime.fromtimestamp(payload["iat"], timezone.utc)
            not_before = datetime.fromtimestamp(payload["nbf"], timezone.utc)
            expires_at = datetime.fromtimestamp(payload["exp"], timezone.utc)
            if issued_at > effective_now + self._clock_skew:
                raise InvalidAccessTokenError("access token was issued in the future")
            if not_before > effective_now + self._clock_skew:
                raise InvalidAccessTokenError("access token is not active")
            if expires_at <= effective_now - self._clock_skew:
                raise InvalidAccessTokenError("access token has expired")
            return AccessClaims(
                user_id=UUID(payload["sub"]),
                session_id=UUID(payload["sid"]),
                token_id=UUID(payload["jti"]),
                issued_at=issued_at,
                expires_at=expires_at,
                password_version=int(payload["pwd"]),
            )
        except (jwt.PyJWTError, KeyError, TypeError, ValueError) as error:
            raise InvalidAccessTokenError("access token is invalid") from error


@dataclass(frozen=True, slots=True)
class IssuedRefreshToken:
    token_id: UUID
    value: str
    digest: bytes


class RefreshTokenService:
    def __init__(self, pepper: str) -> None:
        if not pepper:
            raise ValueError("refresh token pepper must not be empty")
        self._pepper = pepper.encode("utf-8")

    def issue(self) -> IssuedRefreshToken:
        token_id = uuid4()
        secret = secrets.token_urlsafe(32)
        value = f"{token_id}.{secret}"
        return IssuedRefreshToken(
            token_id=token_id,
            value=value,
            digest=self._digest(value),
        )

    def parse(self, value: str) -> tuple[UUID, bytes]:
        try:
            selector, secret = value.split(".", 1)
            token_id = UUID(selector)
        except (ValueError, AttributeError) as error:
            raise InvalidRefreshTokenError("refresh token is invalid") from error
        if not secret:
            raise InvalidRefreshTokenError("refresh token is invalid")
        return token_id, self._digest(value)

    @staticmethod
    def matches(expected: bytes, actual: bytes) -> bool:
        return hmac.compare_digest(expected, actual)

    def _digest(self, value: str) -> bytes:
        return hmac.new(
            self._pepper,
            value.encode("utf-8"),
            hashlib.sha256,
        ).digest()

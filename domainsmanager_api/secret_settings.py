from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken
from pydantic import SecretStr


class SecretSettingError(ValueError):
    """A deployment key is missing or cannot decrypt a stored secret."""


def encrypt_secret(value: str, key: SecretStr | None) -> str:
    if key is None or not key.get_secret_value():
        raise SecretSettingError("DOMAINSMANAGER_CONFIGURATION_ENCRYPTION_KEY is required")
    try:
        token = Fernet(key.get_secret_value().encode()).encrypt(value.encode())
    except (TypeError, ValueError) as error:
        raise SecretSettingError("configuration encryption key is invalid") from error
    return f"fernet:v1:{token.decode()}"


def decrypt_secret(value: str, key: SecretStr | None) -> str:
    if not value.startswith("fernet:v1:"):
        raise SecretSettingError("stored secret uses an unsupported encryption format")
    if key is None or not key.get_secret_value():
        raise SecretSettingError("DOMAINSMANAGER_CONFIGURATION_ENCRYPTION_KEY is required")
    try:
        return Fernet(key.get_secret_value().encode()).decrypt(value[10:].encode()).decode()
    except (InvalidToken, TypeError, ValueError, UnicodeDecodeError) as error:
        raise SecretSettingError("stored secret cannot be decrypted with this key") from error

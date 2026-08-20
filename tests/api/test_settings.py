import pytest
from pydantic import ValidationError

from domainsmanager_api.settings import Settings


@pytest.mark.unit
def test_settings_load_prefixed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOMAINSMANAGER_SERVER_HOST", "0.0.0.0")
    monkeypatch.setenv("DOMAINSMANAGER_SERVER_PORT", "8080")
    monkeypatch.setenv("DOMAINSMANAGER_REGISTRATION_ENABLED", "true")
    monkeypatch.setenv("DOMAINSMANAGER_DATABASE_TYPE", "postgresql")
    monkeypatch.setenv("DOMAINSMANAGER_DATABASE_HOST", "db.example")
    monkeypatch.setenv("DOMAINSMANAGER_DATABASE_NAME", "domains")
    monkeypatch.setenv("DOMAINSMANAGER_DATABASE_USER", "app")

    settings = Settings(_env_file=None)

    assert settings.server_host == "0.0.0.0"
    assert settings.server_port == 8080
    assert settings.registration_enabled is True
    database = settings.database_config()
    assert database.host == "db.example"
    assert database.name == "domains"
    assert database.user == "app"


@pytest.mark.unit
def test_settings_default_to_local_only_server() -> None:
    settings = Settings(_env_file=None)

    assert settings.server_host == "127.0.0.1"
    assert settings.server_port == 7920
    assert settings.registration_enabled is False
    assert not hasattr(settings, "database_url")


@pytest.mark.unit
def test_settings_accept_nonempty_auth_secrets() -> None:
    settings = Settings(
        _env_file=None,
        jwt_secret_key="x",
        refresh_token_pepper="y",
    )

    assert settings.jwt_secret_key is not None
    assert settings.jwt_secret_key.get_secret_value() == "x"
    assert settings.refresh_token_pepper is not None
    assert settings.refresh_token_pepper.get_secret_value() == "y"
    assert "x" not in repr(settings.jwt_secret_key)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("username", "password"),
    [("admin", None), (None, "password")],
)
def test_bootstrap_admin_settings_must_be_configured_together(
    username: str | None,
    password: str | None,
) -> None:
    with pytest.raises(ValidationError, match="must be configured together"):
        Settings(
            _env_file=None,
            bootstrap_admin_username=username,
            bootstrap_admin_password=password,
        )

import pytest

from domainsmanager_api.settings import Settings


@pytest.mark.unit
def test_settings_load_prefixed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOMAINSMANAGER_SERVER_HOST", "0.0.0.0")
    monkeypatch.setenv("DOMAINSMANAGER_SERVER_PORT", "8080")
    monkeypatch.setenv("DOMAINSMANAGER_REGISTRATION_ENABLED", "true")

    settings = Settings(_env_file=None)

    assert settings.server_host == "0.0.0.0"
    assert settings.server_port == 8080
    assert settings.registration_enabled is True


@pytest.mark.unit
def test_settings_default_to_local_only_server() -> None:
    settings = Settings(_env_file=None)

    assert settings.server_host == "127.0.0.1"
    assert settings.server_port == 7920
    assert settings.registration_enabled is False

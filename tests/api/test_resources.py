import logging

import pytest

from domainsmanager_api.resources import create_resources
from domainsmanager_api.settings import Settings


@pytest.mark.unit
@pytest.mark.parametrize(
    ("jwt_secret", "refresh_pepper", "generated_name"),
    [
        (None, "y", "JWT_SECRET_KEY"),
        ("", "y", "JWT_SECRET_KEY"),
        ("x", None, "REFRESH_TOKEN_PEPPER"),
        ("x", "", "REFRESH_TOKEN_PEPPER"),
    ],
)
def test_resource_factory_generates_missing_auth_secrets(
    jwt_secret: str | None,
    refresh_pepper: str | None,
    generated_name: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = Settings(
        _env_file=None,
        jwt_secret_key=jwt_secret,
        refresh_token_pepper=refresh_pepper,
    )

    with caplog.at_level(logging.WARNING, logger="domainsmanager_api.resources"):
        resources = create_resources(settings)

    assert resources.settings.jwt_secret_key is not None
    assert resources.settings.jwt_secret_key.get_secret_value()
    assert resources.settings.refresh_token_pepper is not None
    assert resources.settings.refresh_token_pepper.get_secret_value()
    assert generated_name in caplog.text
    assert "preserve sessions" in caplog.text


@pytest.mark.unit
def test_resource_factory_generates_distinct_auth_secrets() -> None:
    settings = Settings(_env_file=None)

    resources = create_resources(settings)

    assert resources.settings.jwt_secret_key is not None
    assert resources.settings.refresh_token_pepper is not None
    assert (
        resources.settings.jwt_secret_key.get_secret_value()
        != resources.settings.refresh_token_pepper.get_secret_value()
    )


@pytest.mark.unit
def test_resource_factory_preserves_configured_auth_secrets(
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = Settings(
        _env_file=None,
        jwt_secret_key="configured-jwt",
        refresh_token_pepper="configured-pepper",
    )

    resources = create_resources(settings)

    assert resources.settings.jwt_secret_key is not None
    assert resources.settings.jwt_secret_key.get_secret_value() == "configured-jwt"
    assert resources.settings.refresh_token_pepper is not None
    assert (
        resources.settings.refresh_token_pepper.get_secret_value()
        == "configured-pepper"
    )
    assert not caplog.records

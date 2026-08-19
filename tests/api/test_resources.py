import pytest

from domainsmanager_api.resources import create_resources
from domainsmanager_api.settings import Settings


@pytest.mark.unit
@pytest.mark.parametrize(
    ("jwt_secret", "refresh_pepper", "message"),
    [
        (None, "y", "JWT_SECRET_KEY"),
        ("", "y", "JWT_SECRET_KEY"),
        ("x", None, "REFRESH_TOKEN_PEPPER"),
        ("x", "", "REFRESH_TOKEN_PEPPER"),
    ],
)
def test_resource_factory_requires_nonempty_auth_secrets(
    jwt_secret: str | None,
    refresh_pepper: str | None,
    message: str,
) -> None:
    settings = Settings(
        _env_file=None,
        jwt_secret_key=jwt_secret,
        refresh_token_pepper=refresh_pepper,
    )

    with pytest.raises(ValueError, match=message):
        create_resources(settings)

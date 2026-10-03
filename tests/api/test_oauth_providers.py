from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from pydantic import ValidationError

from domainsmanager_api.oauth_providers import (
    GitHubOAuthProvider,
    LinuxDoOAuthProvider,
    OAuthClientSettings,
    build_oauth_providers,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "field", ["authorize_endpoint", "token_endpoint", "user_endpoint"]
)
@pytest.mark.parametrize(
    "endpoint",
    [
        "http://example.com/token",
        "http://localhost.evil.test/token",
        "https://user:password@example.com/token",
        "https://example.com/token?secret=1",
        "https://example.com/token#fragment",
        "https://example.com:99999/token",
        "https://example.com:0/token",
        "https://example.com/\n",
        "//example.com/token",
    ],
)
def test_custom_endpoints_reject_unsafe_urls(field, endpoint):
    with pytest.raises(ValidationError):
        OAuthClientSettings(
            client_id="client", client_secret="secret", **{field: endpoint}
        )


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://id.example.com/oauth",
        "http://localhost:8000/oauth",
        "http://127.0.0.1:8000/oauth",
        "http://[::1]:8000/oauth",
    ],
)
def test_custom_endpoints_accept_https_and_local_http(endpoint):
    config = OAuthClientSettings(
        client_id="client",
        client_secret="secret",
        authorize_endpoint=endpoint,
        token_endpoint=endpoint,
        user_endpoint=endpoint,
    )
    assert config.authorize_endpoint == endpoint


@pytest.mark.parametrize("provider_type", [GitHubOAuthProvider, LinuxDoOAuthProvider])
@pytest.mark.parametrize("scopes", [[], ["profile", "read:user"]])
def test_custom_scopes_replace_defaults(provider_type, scopes):
    provider = provider_type(
        OAuthClientSettings(client_id="client", client_secret="secret", scopes=scopes)
    )
    params = parse_qs(
        urlsplit(
            provider.authorization_url("state", "https://example.com/cb", "challenge")
        ).query
    )
    assert params.get("scope") == ([" ".join(scopes)] if scopes else None)


@pytest.mark.parametrize(
    "scopes", [[""], ["two scopes"], ["line\nbreak"], ['quote"'], ["back\\slash"]]
)
def test_scopes_must_be_individual_oauth_tokens(scopes):
    with pytest.raises(ValidationError):
        OAuthClientSettings(client_id="client", client_secret="secret", scopes=scopes)


@pytest.mark.parametrize("provider_type", [GitHubOAuthProvider, LinuxDoOAuthProvider])
@pytest.mark.parametrize("redirect_at", [None, "token", "user"])
async def test_custom_endpoints_keep_secrets_upstream_without_following_redirects(
    monkeypatch, provider_type, redirect_at
):
    from domainsmanager_application.oauth import OAuthError

    config = OAuthClientSettings(
        client_id="client",
        client_secret="private-client-secret",
        authorize_endpoint="https://id.example.com/authorize",
        token_endpoint="https://id.example.com/token",
        user_endpoint="https://api.example.com/user",
    )
    provider = provider_type(config)
    url = provider.authorization_url("state", "https://app.example.com/cb", "challenge")
    assert url.startswith(config.authorize_endpoint + "?")
    assert "private-client-secret" not in url
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.query == b""
        if str(request.url) == config.token_endpoint:
            assert request.method == "POST"
            if redirect_at == "token":
                return httpx.Response(
                    307, headers={"Location": "https://evil.example.com/steal"}
                )
            return httpx.Response(200, json={"access_token": "private-access-token"})
        assert str(request.url) == config.user_endpoint
        assert request.headers["Authorization"] == "Bearer private-access-token"
        assert "private-client-secret" not in str(request.headers)
        assert not request.content
        if redirect_at == "user":
            return httpx.Response(
                302, headers={"Location": "https://evil.example.com/steal"}
            )
        return httpx.Response(200, json={"id": 123, "active": True})

    clients = mock_client(monkeypatch, handler)
    if redirect_at:
        with pytest.raises(OAuthError):
            await provider.fetch_identity(
                "code", "https://app.example.com/cb", "verifier"
            )
    else:
        assert (
            await provider.fetch_identity(
                "code", "https://app.example.com/cb", "verifier"
            )
        ).subject == "123"
    assert len(requests) == (1 if redirect_at == "token" else 2)
    assert clients[0].is_closed


def test_only_supported_integrations_are_built_from_complete_database_values():
    assert build_oauth_providers({"github_client_id": "client"}) == {}
    providers = build_oauth_providers(
        {
            "github_client_id": "client",
            "github_client_secret": "secret",
            "unknown_client_id": "other",
            "unknown_client_secret": "other",
        }
    )
    assert list(providers) == ["github"]
    assert isinstance(providers["github"], GitHubOAuthProvider)


@pytest.mark.parametrize("key", ["github", "linuxdo"])
def test_provider_switch_requires_both_credentials_and_preserves_them(key):
    values = {f"{key}_enabled": "true"}
    assert build_oauth_providers(values) == {}
    values[f"{key}_client_id"] = "client"
    assert build_oauth_providers(values) == {}
    values[f"{key}_client_secret"] = "secret"
    assert key in build_oauth_providers(values)
    values[f"{key}_enabled"] = "false"
    assert build_oauth_providers(values) == {}
    assert values[f"{key}_client_secret"] == "secret"
    values[f"{key}_enabled"] = "true"
    assert key in build_oauth_providers(values)


@pytest.mark.parametrize("provider_type", [GitHubOAuthProvider, LinuxDoOAuthProvider])
def test_authorization_parameters(provider_type):
    provider = provider_type(
        OAuthClientSettings(client_id="client", client_secret="secret")
    )
    params = parse_qs(
        urlsplit(
            provider.authorization_url(
                "state+value", "https://example.com/cb", "challenge"
            )
        ).query
    )
    assert params["state"] == ["state+value"]
    assert params["redirect_uri"] == ["https://example.com/cb"]
    if provider.pkce:
        assert params["scope"] == ["read:user"]
        assert params["code_challenge"] == ["challenge"]
        assert params["code_challenge_method"] == ["S256"]
    else:
        assert "scope" not in params
        assert "code_challenge" not in params
    assert "client_secret" not in params


def mock_client(monkeypatch, handler):
    client_type = httpx.AsyncClient
    clients = []

    def create(**kwargs):
        client = client_type(transport=httpx.MockTransport(handler), **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(httpx, "AsyncClient", create)
    return clients


@pytest.mark.parametrize("provider_type", [GitHubOAuthProvider, LinuxDoOAuthProvider])
async def test_exchange_authentication_profile_whitelist_and_client_cleanup(
    monkeypatch, provider_type
):
    provider = provider_type(
        OAuthClientSettings(client_id="client", client_secret="secret")
    )
    requests = []

    def handler(request):
        requests.append(request)
        if request.method == "POST":
            fields = parse_qs(request.content.decode())
            assert fields["redirect_uri"] == ["https://example.com/cb"]
            assert fields["code"] == ["code"]
            if provider.pkce:
                assert fields["client_secret"] == ["secret"]
                assert fields["code_verifier"] == ["verifier"]
            else:
                assert request.headers["Authorization"] == "Basic Y2xpZW50OnNlY3JldA=="
                assert "client_secret" not in fields
                assert "code_verifier" not in fields
            return httpx.Response(200, json={"access_token": "token"})
        assert request.headers["Authorization"] == "Bearer token"
        return httpx.Response(
            200,
            json={
                "id": 42,
                "login": "login",
                "username": "username",
                "name": "Name",
                "active": True,
                "avatar_template": "/user_avatar/test/{size}/1.png",
                "api_key": "must-not-store",
                "external_ids": {"private": "id"},
            },
        )

    clients = mock_client(monkeypatch, handler)
    identity = await provider.fetch_identity(
        "code", "https://example.com/cb", "verifier"
    )
    assert identity.provider_key == provider.key
    assert identity.subject == "42"
    assert identity.active is True
    assert "api_key" not in identity.profile
    assert "external_ids" not in identity.profile
    if not provider.pkce:
        assert identity.avatar_url == "https://linux.do/user_avatar/test/288/1.png"
    assert len(requests) == 2
    assert clients[0].is_closed


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(
            200, json={"error": "bad_code", "error_description": "sensitive"}
        ),
        httpx.Response(401, text="sensitive"),
        httpx.Response(500, text="sensitive"),
        httpx.Response(200, text="sensitive"),
        httpx.Response(200, json=[]),
        httpx.Response(302, headers={"Location": "https://example.com/sensitive"}),
    ],
)
async def test_upstream_failures_are_sanitized_and_close_client(monkeypatch, response):
    from domainsmanager_application.oauth import OAuthError

    clients = mock_client(monkeypatch, lambda request: response)
    provider = GitHubOAuthProvider(
        OAuthClientSettings(client_id="client", client_secret="secret")
    )
    with pytest.raises(OAuthError) as exc:
        await provider.fetch_identity("code", "https://example.com/cb", "verifier")
    assert "sensitive" not in str(exc.value)
    assert clients[0].is_closed


@pytest.mark.parametrize("subject", [None, True, [], {}, ""])
async def test_invalid_subject_is_rejected(monkeypatch, subject):
    from domainsmanager_application.oauth import OAuthError

    def handler(request):
        payload = (
            {"access_token": "token"} if request.method == "POST" else {"id": subject}
        )
        return httpx.Response(200, json=payload)

    mock_client(monkeypatch, handler)
    provider = LinuxDoOAuthProvider(
        OAuthClientSettings(client_id="client", client_secret="secret")
    )
    with pytest.raises(OAuthError):
        await provider.fetch_identity("code", "https://example.com/cb", None)


@pytest.mark.parametrize("active", [False, None, "true", 1])
async def test_linuxdo_does_not_promote_missing_or_invalid_active_flag(
    monkeypatch, active
):
    def handler(request):
        payload = (
            {"access_token": "token"}
            if request.method == "POST"
            else {"id": 42, "active": active}
        )
        return httpx.Response(200, json=payload)

    mock_client(monkeypatch, handler)
    provider = LinuxDoOAuthProvider(
        OAuthClientSettings(client_id="client", client_secret="secret")
    )
    identity = await provider.fetch_identity("code", "https://example.com/cb", None)
    assert identity.active is False


async def test_network_failure_is_sanitized(monkeypatch):
    from domainsmanager_application.oauth import OAuthError

    def handler(request):
        raise httpx.ReadTimeout("sensitive", request=request)

    clients = mock_client(monkeypatch, handler)
    provider = GitHubOAuthProvider(
        OAuthClientSettings(client_id="client", client_secret="secret")
    )
    with pytest.raises(OAuthError) as exc:
        await provider.fetch_identity("code", "https://example.com/cb", "verifier")
    assert "sensitive" not in str(exc.value)
    assert clients[0].is_closed

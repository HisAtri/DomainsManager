"""OAuth 协议适配；账户归属和登录策略由应用层处理。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar
from urllib.parse import urlencode, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from domainsmanager_application.oauth import ExternalIdentity


class OAuthClientSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    client_id: str = Field(min_length=1)
    client_secret: SecretStr
    authorize_endpoint: str | None = None
    token_endpoint: str | None = None
    user_endpoint: str | None = None
    # None 使用提供方默认权限，空列表则明确不发送 scope。
    scopes: list[str] | None = None

    @field_validator("authorize_endpoint", "token_endpoint", "user_endpoint")
    @classmethod
    def validate_endpoint(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value)
        local_http = parsed.scheme == "http" and parsed.hostname in {
            "localhost",
            "127.0.0.1",
            "::1",
        }
        if (
            not parsed.hostname
            or (parsed.scheme != "https" and not local_http)
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.port == 0
            or any(char.isspace() for char in value)
            or "\\" in value
        ):
            raise ValueError(
                "OAuth endpoints require HTTPS (HTTP only on localhost), without credentials, query or fragment"
            )
        return value

    @field_validator("scopes")
    @classmethod
    def validate_scopes(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and any(
            not scope
            or any(
                not (char == "!" or "#" <= char <= "[" or "]" <= char <= "~")
                for char in scope
            )
            for scope in value
        ):
            raise ValueError("OAuth scopes must be nonempty RFC 6749 scope tokens")
        return value

    @field_validator("client_id")
    @classmethod
    def validate_client_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("OAuth client_id must not be blank")
        return value.strip()

    @field_validator("client_secret")
    @classmethod
    def validate_client_secret(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("OAuth client_secret must not be blank")
        return value


class OAuthProvider:
    key: ClassVar[str]
    display_name: ClassVar[str]
    pkce: ClassVar[bool]
    authorize_endpoint: str
    token_endpoint: str
    user_endpoint: str
    default_scopes: ClassVar[tuple[str, ...]] = ()
    profile_fields: ClassVar[tuple[str, ...]]
    username_field: ClassVar[str]
    avatar_field: ClassVar[str]

    def __init__(self, config: OAuthClientSettings) -> None:
        self.config = config
        self.authorize_endpoint = config.authorize_endpoint or self.authorize_endpoint
        self.token_endpoint = config.token_endpoint or self.token_endpoint
        self.user_endpoint = config.user_endpoint or self.user_endpoint
        self.scopes = (
            config.scopes if config.scopes is not None else self.default_scopes
        )

    def authorization_url(
        self, state: str, redirect_uri: str, code_challenge: str | None
    ) -> str:
        params = {
            "client_id": self.config.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "state": state,
        }
        if self.pkce:
            if not code_challenge:
                raise ValueError("GitHub OAuth requires a PKCE challenge")
            params.update(
                code_challenge=code_challenge,
                code_challenge_method="S256",
            )
        if self.scopes:
            params["scope"] = " ".join(self.scopes)
        return f"{self.authorize_endpoint}?{urlencode(params)}"

    async def fetch_identity(
        self, code: str, redirect_uri: str, code_verifier: str | None
    ) -> ExternalIdentity:
        from domainsmanager_application.oauth import OAuthError

        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        }
        auth = None
        if self.pkce:
            if not code_verifier:
                raise OAuthError("oauth_invalid_verifier", "PKCE verifier is required")
            data.update(
                client_id=self.config.client_id,
                client_secret=self.config.client_secret.get_secret_value(),
                code_verifier=code_verifier,
            )
        else:
            auth = httpx.BasicAuth(
                self.config.client_id, self.config.client_secret.get_secret_value()
            )

        # 每次交换使用独立客户端，令牌不跨请求保留，且不跟随上游重定向。
        try:
            async with httpx.AsyncClient(
                timeout=15.0,
                follow_redirects=False,
                headers={"Accept": "application/json", "User-Agent": "DomainsManager"},
            ) as client:
                token_response = await client.post(
                    self.token_endpoint, data=data, auth=auth
                )
                if 400 <= token_response.status_code < 500:
                    raise OAuthError(
                        "oauth_token_exchange_failed",
                        "OAuth authorization was rejected",
                    )
                token = self._read_json(token_response)
                access_token = token.get("access_token")
                if (
                    token.get("error")
                    or not isinstance(access_token, str)
                    or not access_token
                ):
                    raise OAuthError(
                        "oauth_token_exchange_failed",
                        "OAuth authorization was rejected",
                    )
                response = await client.get(
                    self.user_endpoint,
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                raw = self._read_json(response)
        except OAuthError:
            raise
        except (httpx.HTTPError, ValueError, TypeError):
            # 不传播响应正文或异常上下文，避免授权码和令牌进入日志或错误响应。
            raise OAuthError(
                "oauth_provider_unavailable",
                "OAuth provider request failed",
                status_code=502,
            ) from None

        return self.parse_identity(raw)

    def parse_identity(self, raw: dict) -> ExternalIdentity:
        raise NotImplementedError

    @staticmethod
    def require_subject(raw: dict) -> str:
        from domainsmanager_application.oauth import OAuthError

        subject = raw.get("id")
        if (
            isinstance(subject, bool)
            or not isinstance(subject, (str, int))
            or not str(subject).strip()
        ):
            raise OAuthError(
                "oauth_invalid_identity",
                "OAuth provider returned an invalid identity",
                status_code=502,
            )
        return str(subject)

    def profile(self, raw: dict) -> dict:
        return {
            field: raw[field]
            for field in self.profile_fields
            if field in raw and isinstance(raw[field], (str, int, bool, type(None)))
        }

    @staticmethod
    def _read_json(response: httpx.Response) -> dict:
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise TypeError("OAuth response must be an object")
        return payload

    @staticmethod
    def _optional_text(value: object) -> str | None:
        return value if isinstance(value, str) and value else None


class GitHubOAuthProvider(OAuthProvider):
    default_scopes = ("read:user",)
    key = "github"
    display_name = "GitHub"
    pkce = True
    authorize_endpoint = "https://github.com/login/oauth/authorize"
    token_endpoint = "https://github.com/login/oauth/access_token"
    user_endpoint = "https://api.github.com/user"
    username_field = "login"
    avatar_field = "avatar_url"
    profile_fields = ("id", "login", "name", "avatar_url", "html_url")

    def parse_identity(self, raw: dict) -> ExternalIdentity:
        profile = self.profile(raw)
        return ExternalIdentity(
            self.key,
            self.require_subject(raw),
            self._optional_text(profile.get("login")),
            self._optional_text(profile.get("name")),
            self._optional_text(profile.get("avatar_url")),
            True,
            profile,
        )


class LinuxDoOAuthProvider(OAuthProvider):
    key = "linuxdo"
    display_name = "LinuxDo"
    pkce = False
    authorize_endpoint = "https://connect.linux.do/oauth2/authorize"
    token_endpoint = "https://connect.linux.do/oauth2/token"
    user_endpoint = "https://connect.linux.do/api/user"
    username_field = "username"
    avatar_field = "avatar_template"
    profile_fields = (
        "id",
        "username",
        "name",
        "avatar_template",
        "active",
        "trust_level",
        "silenced",
    )

    def parse_identity(self, raw: dict) -> ExternalIdentity:
        # LinuxDo 响应含额外密钥和关联 ID，只提取登录所需字段。
        profile = self.profile(raw)
        avatar = self._optional_text(profile.get("avatar_template"))
        if avatar:
            avatar = avatar.replace("{size}", "288")
            if avatar.startswith("/") and not avatar.startswith("//"):
                avatar = f"https://linux.do{avatar}"
        return ExternalIdentity(
            self.key,
            self.require_subject(raw),
            self._optional_text(profile.get("username")),
            self._optional_text(profile.get("name")),
            avatar,
            raw.get("active") is True,
            profile,
        )


def build_oauth_providers(values: Mapping[str, str]) -> dict[str, OAuthProvider]:
    """仅装配当前明确支持的两种接入；数据库不决定协议实现。"""
    providers: dict[str, OAuthProvider] = {}
    for key, provider_type in (
        ("github", GitHubOAuthProvider),
        ("linuxdo", LinuxDoOAuthProvider),
    ):
        client_id = values.get(f"{key}_client_id", "").strip()
        client_secret = values.get(f"{key}_client_secret", "").strip()
        # 缺省沿用已有凭据的启用状态，显式停用不需要删除凭据。
        if (
            values.get(f"{key}_enabled", "true") == "true"
            and client_id
            and client_secret
        ):
            providers[key] = provider_type(
                OAuthClientSettings(client_id=client_id, client_secret=client_secret)
            )
    return providers

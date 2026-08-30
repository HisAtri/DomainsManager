import re
from pathlib import Path

import pytest
import yaml

from domainsmanager_api.main import create_app
from domainsmanager_api.settings import Settings

OPENAPI_PATH = Path(__file__).parents[2] / "docs" / "api" / "openapi.yaml"


def resolve_reference(document: dict, reference: str) -> object:
    value: object = document
    for part in reference.removeprefix("#/").split("/"):
        assert isinstance(value, dict)
        value = value[part]
    return value


@pytest.mark.contract
def test_openapi_references_and_path_parameters_are_valid() -> None:
    document = yaml.safe_load(OPENAPI_PATH.read_text(encoding="utf-8"))
    references: list[str] = []
    operation_ids: list[str] = []

    def collect(value: object) -> None:
        if isinstance(value, dict):
            reference = value.get("$ref")
            if isinstance(reference, str):
                references.append(reference)
            for item in value.values():
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(document)
    for reference in references:
        if reference.startswith("#/"):
            resolve_reference(document, reference)

    for path, path_item in document["paths"].items():
        expected = set(re.findall(r"{([^}]+)}", path))
        for method, operation in path_item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            operation_ids.append(operation["operationId"])
            parameters = path_item.get("parameters", []) + operation.get(
                "parameters", []
            )
            actual = set()
            for parameter in parameters:
                if "$ref" in parameter:
                    parameter = resolve_reference(document, parameter["$ref"])
                if parameter.get("in") == "path" and parameter.get("required"):
                    actual.add(parameter["name"])
            assert actual == expected

    assert len(operation_ids) == len(set(operation_ids))


@pytest.mark.contract
def test_implemented_health_operations_match_contract() -> None:
    document = yaml.safe_load(OPENAPI_PATH.read_text(encoding="utf-8"))
    expected = {
        operation["operationId"]
        for path in ("/health/live", "/health/ready")
        for method, operation in document["paths"][path].items()
        if method == "get"
    }
    app = create_app(
        Settings(
            database_type="sqlite",
            database_path=":memory:",
            jwt_secret_key="x",
            refresh_token_pepper="y",
        )
    )
    runtime_document = app.openapi()
    actual = {
        operation["operationId"]
        for path in ("/health/live", "/health/ready")
        for method, operation in runtime_document["paths"][path].items()
        if method == "get"
    }

    assert actual == expected


@pytest.mark.contract
def test_public_site_config_operation_matches_contract() -> None:
    document = yaml.safe_load(OPENAPI_PATH.read_text(encoding="utf-8"))
    expected = document["paths"]["/site/config"]["get"]["operationId"]
    app = create_app(
        Settings(
            database_type="sqlite",
            database_path=":memory:",
            jwt_secret_key="x",
            refresh_token_pepper="y",
        )
    )
    assert (
        app.openapi()["paths"]["/api/v1/site/config"]["get"]["operationId"] == expected
    )


@pytest.mark.contract
def test_implemented_domain_operations_match_contract() -> None:
    expected = {
        ("/api/v1/domains", "get", "listDomains"),
        ("/api/v1/domains", "post", "createDomain"),
        ("/api/v1/domains/stats", "get", "getDomainStats"),
        ("/api/v1/domains/{domain_id}", "get", "getDomain"),
        ("/api/v1/domains/{domain_id}", "patch", "updateDomain"),
        ("/api/v1/domains/{domain_id}", "delete", "deleteDomain"),
        ("/api/v1/domains/{domain_id}/refresh", "post", "refreshDomain"),
        ("/api/v1/domains/{domain_id}/checks", "get", "listDomainChecks"),
        ("/api/v1/domains/{domain_id}/checks/{check_id}", "get", "getDomainCheck"),
        ("/api/v1/tasks", "get", "listTasks"),
        ("/api/v1/tasks/{task_id}", "get", "getTask"),
    }
    app = create_app(
        Settings(
            database_type="sqlite",
            database_path=":memory:",
            jwt_secret_key="x",
            refresh_token_pepper="y",
        )
    )
    runtime_document = app.openapi()
    actual = {
        (path, method, operation["operationId"])
        for path, path_item in runtime_document["paths"].items()
        if path.startswith(("/api/v1/domains", "/api/v1/tasks"))
        for method, operation in path_item.items()
        if method in {"get", "post", "patch", "delete"}
    }

    assert actual == expected


@pytest.mark.contract
def test_implemented_admin_operations_match_contract() -> None:
    expected = {
        ("/api/v1/admin/settings/refresh-policy", "get", "getRefreshPolicy"),
        ("/api/v1/admin/settings/refresh-policy", "patch", "updateRefreshPolicy"),
        ("/api/v1/admin/settings", "get", "listGlobalSettings"),
        ("/api/v1/admin/settings/test-email", "post", "sendTestEmail"),
        ("/api/v1/admin/users", "get", "listUsers"),
        ("/api/v1/admin/users/{user_id}", "get", "getUserAsAdmin"),
        ("/api/v1/admin/users/{user_id}", "patch", "updateUserAsAdmin"),
        ("/api/v1/admin/users/{user_id}/ban", "post", "banUser"),
        ("/api/v1/admin/users/{user_id}/unban", "post", "unbanUser"),
        ("/api/v1/admin/users/{user_id}/sessions", "get", "listUserSessionsAsAdmin"),
        (
            "/api/v1/admin/users/{user_id}/sessions/{session_id}/revoke",
            "post",
            "revokeUserSessionAsAdmin",
        ),
        ("/api/v1/admin/domains", "get", "listDomainsAsAdmin"),
        ("/api/v1/admin/domains/{domain_id}", "get", "getDomainAsAdmin"),
        ("/api/v1/admin/domains/{domain_id}", "patch", "updateDomainAsAdmin"),
        ("/api/v1/admin/domains/{domain_id}", "delete", "deleteDomainAsAdmin"),
        ("/api/v1/admin/domains/{domain_id}/refresh", "post", "refreshDomainAsAdmin"),
        ("/api/v1/admin/domain-checks", "get", "listDomainChecksAsAdmin"),
        ("/api/v1/admin/operations/metrics", "get", "getOperationalMetrics"),
        (
            "/api/v1/admin/security-audit-events",
            "get",
            "listSecurityAuditEvents",
        ),
    }
    app = create_app(
        Settings(
            database_type="sqlite",
            database_path=":memory:",
            jwt_secret_key="x",
            refresh_token_pepper="y",
        )
    )
    actual = {
        (path, method, operation["operationId"])
        for path, path_item in app.openapi()["paths"].items()
        if path.startswith("/api/v1/admin/")
        for method, operation in path_item.items()
        if method in {"get", "post", "patch", "delete"}
    }
    assert actual == expected

    document = yaml.safe_load(OPENAPI_PATH.read_text(encoding="utf-8"))
    implemented_paths = {
        "/auth/register",
        "/auth/login",
        "/auth/logout",
        "/auth/token/refresh",
        "/auth/me",
        "/auth/me/password",
            "/auth/me/settings",
            "/auth/email-verifications/confirm",
            "/auth/me/email-verifications/resend",
        "/auth/oauth2/providers",
        "/auth/oauth2/{provider}/authorize",
        "/auth/oauth2/{provider}/callback",
    }
    expected = {
        (f"/api/v1{path}", method, operation["operationId"])
        for path, path_item in document["paths"].items()
        if path in implemented_paths
        for method, operation in path_item.items()
        if method in {"get", "post", "patch", "delete"}
    }
    app = create_app(
        Settings(
            database_type="sqlite",
            database_path=":memory:",
            jwt_secret_key="x",
            refresh_token_pepper="y",
        )
    )
    runtime_document = app.openapi()
    actual = {
        (path, method, operation["operationId"])
        for path, path_item in runtime_document["paths"].items()
        if path.startswith("/api/v1/auth/")
        for method, operation in path_item.items()
        if method in {"get", "post", "patch", "delete"}
    }

    assert actual == expected

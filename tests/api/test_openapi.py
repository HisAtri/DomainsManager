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
    app = create_app(Settings(database_url="sqlite+aiosqlite://"))
    runtime_document = app.openapi()
    actual = {
        operation["operationId"]
        for path in ("/health/live", "/health/ready")
        for method, operation in runtime_document["paths"][path].items()
        if method == "get"
    }

    assert actual == expected

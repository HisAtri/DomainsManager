import pytest

from domainsmanager_lookup._internal.status_codes import (
    DEFAULT_DOMAIN_STATUS_REGISTRY,
    STANDARD_DOMAIN_STATUS_CODES,
    DomainStatusCode,
    DomainStatusCodeRegistry,
)

EXPECTED_STATUS_PAIRS = (
    ("addPeriod", "add period"),
    ("autoRenewPeriod", "auto renew period"),
    ("inactive", "inactive"),
    ("ok", "active"),
    ("pendingCreate", "pending create"),
    ("pendingDelete", "pending delete"),
    ("pendingRenew", "pending renew"),
    ("pendingRestore", "pending restore"),
    ("pendingTransfer", "pending transfer"),
    ("pendingUpdate", "pending update"),
    ("redemptionPeriod", "redemption period"),
    ("renewPeriod", "renew period"),
    ("serverDeleteProhibited", "server delete prohibited"),
    ("serverHold", "server hold"),
    ("serverRenewProhibited", "server renew prohibited"),
    ("serverTransferProhibited", "server transfer prohibited"),
    ("serverUpdateProhibited", "server update prohibited"),
    ("transferPeriod", "transfer period"),
    ("clientDeleteProhibited", "client delete prohibited"),
    ("clientHold", "client hold"),
    ("clientRenewProhibited", "client renew prohibited"),
    ("clientTransferProhibited", "client transfer prohibited"),
    ("clientUpdateProhibited", "client update prohibited"),
)


def test_standard_registry_contains_complete_icann_mapping() -> None:
    assert (
        tuple(
            (status.epp_code, status.rdap_code)
            for status in STANDARD_DOMAIN_STATUS_CODES
        )
        == EXPECTED_STATUS_PAIRS
    )

    for epp_code, rdap_code in EXPECTED_STATUS_PAIRS:
        resolved_by_epp = DEFAULT_DOMAIN_STATUS_REGISTRY.get_by_epp(epp_code)
        resolved_by_rdap = DEFAULT_DOMAIN_STATUS_REGISTRY.get_by_rdap(rdap_code)
        assert resolved_by_epp is resolved_by_rdap
        assert resolved_by_epp is not None
        assert resolved_by_epp.rdap_code == rdap_code
        assert DEFAULT_DOMAIN_STATUS_REGISTRY.resolve(epp_code) is resolved_by_epp
        assert DEFAULT_DOMAIN_STATUS_REGISTRY.resolve(rdap_code) is resolved_by_rdap


def test_normalizes_both_vocabularies_and_deduplicates_in_input_order() -> None:
    assert DEFAULT_DOMAIN_STATUS_REGISTRY.normalize_many(
        [
            " clientTransferProhibited ",
            "CLIENT   TRANSFER PROHIBITED",
            "ok",
            "ACTIVE",
            "Registry   Custom Status",
            "registry custom status",
            "  ",
        ]
    ) == [
        "client transfer prohibited",
        "active",
        "registry custom status",
    ]


@pytest.mark.parametrize(
    "codes, message",
    [
        (
            [DomainStatusCode("one", "first"), DomainStatusCode("ONE", "second")],
            "duplicate EPP status code",
        ),
        (
            [DomainStatusCode("one", "first"), DomainStatusCode("two", "FIRST")],
            "duplicate RDAP status code",
        ),
        (
            [DomainStatusCode("one", "shared"), DomainStatusCode("shared", "two")],
            "ambiguous status code",
        ),
    ],
)
def test_registry_rejects_ambiguous_definitions(
    codes: list[DomainStatusCode], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        DomainStatusCodeRegistry(codes)


@pytest.mark.parametrize("field", ["epp", "rdap"])
def test_status_code_rejects_empty_vocabulary_values(field: str) -> None:
    values = {"epp_code": "ok", "rdap_code": "active"}
    values[f"{field}_code"] = "  "
    with pytest.raises(
        ValueError, match=f"{field.upper()} status code must not be empty"
    ):
        DomainStatusCode(**values)

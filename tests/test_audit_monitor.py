from datetime import UTC, datetime

import pytest

from domainsmanager_api.audit_monitor import parse_since


def test_parse_since_normalizes_timezone() -> None:
    assert parse_since("2026-08-28T08:00:00+08:00") == datetime(2026, 8, 28, tzinfo=UTC)


def test_parse_since_requires_timezone() -> None:
    with pytest.raises(Exception, match="timezone"):
        parse_since("2026-08-28T00:00:00")

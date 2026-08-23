from pathlib import Path

from domainsmanager_persistence.database_config import DatabaseConfig


def sqlite_database(path: str | Path = ":memory:") -> DatabaseConfig:
    return DatabaseConfig(type="sqlite", path=str(path))

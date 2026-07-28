"""PostgreSQL migration adapter boundary for container production deployments.

The single-user local runtime uses SqliteRepository. This adapter deliberately keeps
PostgreSQL DDL and migration execution behind a separate port until the PostgreSQL
repository implementation is selected by deployment configuration.
"""

from __future__ import annotations

from pathlib import Path


class PostgresMigrationAdapter:
    def __init__(self, migrations_directory: Path) -> None:
        self.migrations_directory = migrations_directory

    def migration_files(self) -> list[Path]:
        return sorted(self.migrations_directory.glob("*.sql"))

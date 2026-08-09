"""Explicit PostgreSQL schema provisioning entry point for Compose."""

from __future__ import annotations

import os
from pathlib import Path

from app.adapters.postgres import PostgresRepository
from app.core.config import database_backend


def main() -> None:
    database_url = os.environ.get("YUANZHIKU_DATABASE_URL", "")
    if database_backend(database_url) != "postgresql":
        raise RuntimeError("专用 migrate 服务必须配置 PostgreSQL YUANZHIKU_DATABASE_URL")
    migrations = Path(__file__).resolve().parents[1] / "migrations" / "postgresql"
    PostgresRepository(database_url, migrations).migrate_to_head()


if __name__ == "__main__":
    main()

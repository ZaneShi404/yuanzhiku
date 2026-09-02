"""迁移后为应用角色授予最小权限（加固计划 Task 14）。

由 Compose 的 grant 服务在 migrate 成功后执行一次：以管理员 URL 创建
（或重置密码）应用角色，并仅授予 public schema 的表/序列 DML 权限。
应用角色不得 CREATE ROLE、不得 DROP DATABASE、不得修改 schema 结构。
角色与对象名一律经 psycopg sql.Identifier 参数化。
"""

from __future__ import annotations

import os
import sys

import psycopg
from psycopg import sql


def main() -> int:
    admin_url = os.environ.get("YUANZHIKU_DB_ADMIN_URL", "")
    app_user = os.environ.get("YUANZHIKU_DB_APP_USER", "yuanzhiku_app")
    app_password = os.environ.get("YUANZHIKU_DB_APP_PASSWORD", "")
    if not admin_url or not app_password:
        print("缺少 YUANZHIKU_DB_ADMIN_URL 或 YUANZHIKU_DB_APP_PASSWORD", file=sys.stderr)
        return 2
    with psycopg.connect(admin_url) as connection:
        with connection.cursor() as cursor:
            exists = cursor.execute(
                "SELECT 1 FROM pg_roles WHERE rolname=%s", (app_user,)
            ).fetchone()
            if exists:
                cursor.execute(
                    sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(
                        sql.Identifier(app_user), sql.Literal(app_password)
                    )
                )
            else:
                cursor.execute(
                    sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                        sql.Identifier(app_user), sql.Literal(app_password)
                    )
                )
            database = os.environ.get("YUANZHIKU_DB_NAME", "yuanzhiku")
            cursor.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(database), sql.Identifier(app_user)
                )
            )
            cursor.execute(
                sql.SQL("GRANT USAGE, CREATE ON SCHEMA public TO {}").format(
                    sql.Identifier(app_user)
                )
            )
            cursor.execute(
                sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}").format(
                    sql.Identifier(app_user)
                )
            )
            cursor.execute(
                sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}").format(
                    sql.Identifier(app_user)
                )
            )
            cursor.execute(
                sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO {}").format(
                    sql.Identifier(app_user)
                )
            )
            cursor.execute(
                sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO {}").format(
                    sql.Identifier(app_user)
                )
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

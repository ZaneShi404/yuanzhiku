# PostgreSQL URL Selection Repair - 20260728T173841Z

## Scope

Repair the independent retest P2 in `reports/testing/20260728T172345Z-independent-retest-report.md` where the Compose `postgresql+psycopg://...` URL was not recognized and startup silently initialized SQLite.

## Requirement Citations

- `REQ-045` requires a PostgreSQL production adapter/migrations, Compose deployment, and isolated Compose runtime data: `docs/requirements.md:43`.
- The Compose test scope requires `tests/runtime/compose-<run-id>` data and loopback publication: `docs/test-plan.md:13`.
- Development self-tests are not independent testing or acceptance: `docs/test-plan.md:15`.

## Repair

- Added URL backend classification that accepts `postgresql://`, `postgres://`, and SQLAlchemy driver forms including Compose's `postgresql+psycopg://...`.
- Routed every recognized PostgreSQL URL through `PostgresRepository` before any SQLite repository can be created.
- Unsupported database schemes now fail with an actionable `YUANZHIKU_DATABASE_URL` configuration error instead of being treated as SQLite.
- Retained default SQLite behavior when no database URL is configured.
- Kept the current Compose `postgresql+psycopg://...` configuration, which is now a supported selected URL. PostgreSQL remains explicitly fail-closed until a feature-complete PostgreSQL repository exists.
- Updated operational configuration documentation with supported URL forms and fail-closed behavior.

## Automated Regression Coverage

- `tests/unit/test_database_url_selection.py:test_postgresql_url_variants_select_postgresql_backend` covers bare and SQLAlchemy-driver PostgreSQL schemes.
- `tests/unit/test_database_url_selection.py:test_compose_driver_url_reaches_postgres_adapter_without_sqlite_fallback` reads the checked Compose URL, verifies PostgreSQL adapter selection, and asserts no SQLite database is created.
- `tests/unit/test_database_url_selection.py:test_postgresql_driver_url_fails_closed_without_sqlite` exercises an invalid `postgresql+psycopg://` configuration and verifies its explicit PostgreSQL initialization failure cannot create SQLite.
- `tests/unit/test_database_url_selection.py:test_unsupported_database_url_fails_before_sqlite_creation` verifies unsupported schemes fail before SQLite creation.
- `tests/unit/test_database_url_selection.py:test_default_database_url_retains_local_sqlite` verifies the local default remains SQLite.

## Self-Test Evidence

Executed from `E:\源知库` using the isolated root `tests/runtime/pgfix-20260728T173841Z`:

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="E:/源知库/backend" "E:/源知库/.venv/Scripts/python.exe" -m pytest -p no:cacheprovider tests/unit/test_database_url_selection.py -q
```

Result: `8 passed in 4.17s`.

Also compiled the changed Python modules with `py_compile` successfully. Docker was not installed, started, or tested. This report records development self-testing only and does not claim independent test or acceptance success.

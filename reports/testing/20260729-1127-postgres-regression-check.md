# PostgreSQL Regression Check

Timestamp: 2026-07-29 11:27 CST
Role: independent test
Scope: PostgreSQL routing/fail-closed behavior, repository/API/worker operation coverage, migrations, Compose topology, relevant current tests, and frontend validation.

I did not read `reports/development` or `reports/infrastructure`.

## Findings

### P1: PostgreSQL logical backups omit backup records

`PostgresRepository.rows_for_backup()` and `insert_backup_rows()` enumerate settings and logical business tables but omit `backups` ([backend/app/adapters/postgres.py](../../backend/app/adapters/postgres.py):214, [backend/app/adapters/postgres.py](../../backend/app/adapters/postgres.py):227). The same adapter treats `backups` as a user-record table when checking an empty restore target ([backend/app/adapters/postgres.py](../../backend/app/adapters/postgres.py):247), and the PostgreSQL schema creates it ([backend/migrations/postgresql/001_initial.sql](../../backend/migrations/postgresql/001_initial.sql):58).

Impact: a PostgreSQL-created backup archive does not preserve existing backup catalog/history. PostgreSQL restore calls `insert_backup_rows()` ([backend/app/services/transfers.py](../../backend/app/services/transfers.py):297), so the omitted records are not restored. This is a recovery/backup integrity P1. No P0 was evidenced.

### Status of previous P1: dispatch path resolved by static and isolated subprocess evidence

The Compose-style `postgresql+psycopg://` URL is classified as PostgreSQL, including driver-qualified schemes ([backend/app/core/config.py](../../backend/app/core/config.py):79). `ApplicationServices` creates `PostgresRepository` and initializes it before exposing services; it has no SQLite fallback on this route ([backend/app/main.py](../../backend/app/main.py):48).

Independent isolated subprocess results:

- Compose-style URL produced `repository=PostgresRepository backend=postgresql sqlite_exists=False`.
- An unreachable `postgresql+psycopg://invalid:invalid@127.0.0.1:1/invalid` produced the PostgreSQL connection error and `sqlite_exists=False`.

The current `PostgresRepository` implements every method in `RepositoryPort` (59 required, none missing), inherits shared API/worker operations, translates SQLite-specific placeholders/conflict/collation syntax, and uses `FOR UPDATE SKIP LOCKED` for cross-process job claiming ([backend/app/adapters/postgres.py](../../backend/app/adapters/postgres.py):72, [backend/app/adapters/postgres.py](../../backend/app/adapters/postgres.py):256). API and worker both operate through the selected repository; worker execution calls `jobs.run_once()` ([backend/app/worker.py](../../backend/app/worker.py):15). Transfer logic chooses logical records for PostgreSQL and has a PostgreSQL target restoration path ([backend/app/services/transfers.py](../../backend/app/services/transfers.py):90, [backend/app/services/transfers.py](../../backend/app/services/transfers.py):254).

Migrations rendered offline successfully through Alembic to revision `002_json_text_compat`, creating the PostgreSQL schema and TEXT compatibility conversions. Evidence artifact: `tests/runtime/pgcheck-20260728T024619Z-13914/alembic-upgrade.sql`.

Compose statically contains `web`, `api`, `worker`, `postgres`, and `redis`. All published ports use loopback bindings: API `127.0.0.1:8765`, web `127.0.0.1:5173`, PostgreSQL `127.0.0.1:54329`, Redis `127.0.0.1:56379` ([docker-compose.yml](../../docker-compose.yml):1).

## Executed Validation

- Isolated runtime: `tests/runtime/pgcheck-20260728T024619Z-13914` only.
- Current backend unit/API modules: `29 passed, 1 skipped in 277.24s`.
  - The skipped test is the explicit real PostgreSQL API/worker workflow, skipped because `POSTGRES_TEST_URL` is unset ([tests/unit/test_postgres_repository.py](../../tests/unit/test_postgres_repository.py):55).
- Frontend type-check: passed.
- Frontend production build: passed to the isolated runtime output directory.
- Offline Alembic migration rendering: passed.

## Blockers

REAL POSTGRES/COMPOSE BLOCKED. Docker, Docker Compose, `psql`, and `pg_isready` are not installed; `POSTGRES_TEST_URL` is unset; loopback ports 54329 and 5432 were unreachable. Therefore no claim is made that migrations, SQL translation, concurrent API/worker startup, backup/restore, export/reimport, or jobs succeeded against a live PostgreSQL server or Compose stack.

## Conclusion

P0: none evidenced.

P1: open. The previous URL dispatch/SQLite-fallback P1 is resolved by executable isolated evidence, but PostgreSQL backup catalog omission is a separate P1. Live PostgreSQL/Compose validation remains blocked and is not passed.

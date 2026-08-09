# Independent Database and Frontend Retest

## Findings

### P1 - Compose V1 delivery is not operational with the configured PostgreSQL backend

- **Impact:** `REQ-045` requires Compose to run `web/api/worker/postgres/redis` and provide a PostgreSQL production adapter/migrations. The configured `api` and `worker` both select PostgreSQL, but the PostgreSQL adapter deliberately raises after migrations instead of providing a repository. The Compose `api` cannot become a usable API, and the `worker` cannot run. This is a V1 delivery blocker, not a SQLite-fallback defect.
- **Evidence:** [backend/app/adapters/postgres.py](E:/源知库/backend/app/adapters/postgres.py:56) always raises `RuntimeError("PostgreSQL schema 已迁移，但应用 PostgreSQL repository 尚未实现...")`; [backend/app/main.py](E:/源知库/backend/app/main.py:54) selects that adapter for PostgreSQL before any SQLite repository is constructed. Static test `test_compose_static_contract` recorded both service URLs as `postgresql+psycopg://...` in [compose-static-results.json](E:/源知库/tests/runtime/db-retest-20260728T181807Z/compose-static-results.json).
- **Process reproduction:** the isolated process test `test_actual_api_process_compose_url_fails_closed` ran:

  ```text
  E:/源知库/.venv/Scripts/python.exe -m uvicorn app.main:application --factory --host 127.0.0.1 --port <ephemeral>
  ```

  with `YUANZHIKU_DATABASE_URL=postgresql+psycopg://yuanzhiku:yuanzhiku_local_only@postgres:5432/yuanzhiku`. It exited `1`, emitted the actionable `PostgreSQL 运行时需要 SQLAlchemy、Alembic 和 psycopg` diagnostic because this local virtual environment lacks those locked dependencies, and created no `state/knowledge.db`; see [api-compose-url-results.json](E:/源知库/tests/runtime/db-retest-20260728T181807Z/api-compose-url-results.json) and [api-compose-url-failure.log](E:/源知库/tests/runtime/db-retest-20260728T181807Z/api-compose-url-failure.log). In the Docker image, the locked dependencies are installed, but the unconditional post-migration error above still prevents a usable API/worker.
- **Required disposition:** implement the PostgreSQL repository behavior or change the frozen deployment requirement before treating Compose V1 delivery as ready.

### P2 - Explicit non-SQLite URLs no longer silently create or use SQLite: resolved by fail-closed behavior

- **Impact:** The prior database-selection class of defect is resolved for the required safety property: no tested explicit PostgreSQL, invalid PostgreSQL-driver, or unsupported URL selected SQLite.
- **Evidence:** independent process test `test_database_url_selection_in_fresh_processes` covered:
  - Compose-style `postgresql+psycopg://...`
  - bare `postgresql://...`
  - bare `postgres://...`
  - another driver `postgresql+asyncpg://...`
  - unknown driver `postgresql+not-a-real-driver://...`
  - unsupported `mysql+pymysql://...`
  - invalid `not-a-database://...`

  Final command:

  ```text
  E:/源知库/.venv/Scripts/python.exe -m pytest -q E:/源知库/tests/runtime/db-retest-20260728T181807Z/independent_retest.py
  ```

  Result: `3 passed in 9.63s`. Each PostgreSQL URL selected the PostgreSQL initialization path and explicitly raised `RuntimeError` with an actionable PostgreSQL diagnostic; unknown/invalid schemes raised `DatabaseUrlConfigurationError` naming `YUANZHIKU_DATABASE_URL`; all had `sqlite_exists: false`. Full result data is in [database-results.json](E:/源知库/tests/runtime/db-retest-20260728T181807Z/database-results.json).
- **Default preserved:** with `YUANZHIKU_DATABASE_URL` unset, a fresh process started `SqliteRepository` and created only the isolated `state/knowledge.db`; recorded in [database-results.json](E:/源知库/tests/runtime/db-retest-20260728T181807Z/database-results.json).
- **File-state recheck:** `knowledge_db_default=true`, `knowledge_db_compose_url=false`, and `knowledge_db_invalid_scheme=false` under the run root.

### P3 - Compose configuration passes static contract checks; runtime validation is BLOCKED

- **Static evidence:** `test_compose_static_contract` parsed [docker-compose.yml](E:/源知库/docker-compose.yml:1) with PyYAML. It found `web`, `api`, `worker`, `postgres`, and `redis`; published ports were `127.0.0.1:5173:80`, `127.0.0.1:8765:8765`, `127.0.0.1:54329:5432`, and `127.0.0.1:56379:6379`; both application database URLs use supported `postgresql+psycopg://...`. See [compose-static-results.json](E:/源知库/tests/runtime/db-retest-20260728T181807Z/compose-static-results.json).
- **Runtime status: BLOCKED, not passed.** `docker --version`, `docker compose version`, and `docker-compose version` each returned `command not found`. Therefore neither `docker compose config` nor isolated startup was run. This prevents runtime validation of image build, Compose interpolation/health dependencies, network behavior, migrations, and inter-service API/UI behavior. Together with P1, Compose V1 must not be represented as delivered.

### P3 - Frontend static checks passed; browser UI acceptance was not performed

- `cd E:/源知库/frontend && npm run lint` exited `0` (`tsc -b --pretty false`).
- `cd E:/源知库/frontend && npm run build` exited `0`; Vite transformed `1577` modules and built the production bundle in `1.62s`.
- The build changed `frontend/dist` during validation; those generated changes were restored. `git diff -- frontend/dist` was empty after restoration.
- These checks do not establish browser-level UI acceptance.

## Scope and Method

- **Retest identity:** fresh independent retest; no application source, existing tests, documentation, Compose file, or existing report was edited.
- **Explicit independence statement:** I did **not** read `reports/development/` or `reports/infrastructure/`, and did not use developer claims. I read only the frozen documents `docs/requirements.md`, `docs/api-contract.md`, `docs/test-plan.md`, `docs/acceptance-matrix.md`, current source/configuration, and current Git state.
- **Environment:** Windows 10.0.26200 x64, Git Bash; Python `3.13.0`; Node `24.18.0`; npm `11.16.0`; project virtual environment had FastAPI/pytest/httpx but did not have SQLAlchemy/Alembic/psycopg installed. Docker CLI and Docker Compose V1/V2 were unavailable.
- **Isolated data root:** `E:\源知库\tests\runtime\db-retest-20260728T181807Z`. All new scripts, API data, logs, JSON evidence, and SQLite files are contained below that directory.

## Backend and API Evidence

- Independent test file: [independent_retest.py](E:/源知库/tests/runtime/db-retest-20260728T181807Z/independent_retest.py).
- The successful real Uvicorn process test used unset database configuration and an ephemeral loopback port. It returned:
  - `GET /api/v1/health`: `200`, `status=ok`, `database=sqlite`, `network=127.0.0.1 only`
  - `GET /openapi.json`: `200`, including `/api/v1/imports/paste`
  - `POST /api/v1/imports/paste`: `201`
  - `POST /api/v1/jobs/run-once`: `200`, `job.state=succeeded`
  - evidence endpoint: artifact SHA matched the import and locator type was `text_range`

  See [api-results.json](E:/源知库/tests/runtime/db-retest-20260728T181807Z/api-results.json) and [api-process.log](E:/源知库/tests/runtime/db-retest-20260728T181807Z/api-process.log).
- No new P0 was observed in the tested database, Compose-static, frontend static, or isolated API smoke scope. The P1 Compose V1 blocker above remains apparent.

## Skipped Tests and Residual Risk

- Actual Docker Compose configuration rendering and runtime startup: **BLOCKED** because no Docker CLI/Compose executable is installed. Not passed.
- Existing `tests/unit/test_database_url_selection.py` was not used as evidence: an attempted invocation without `PYTHONPATH` stopped at collection with `ModuleNotFoundError: No module named 'app'`; more importantly, its fixed runtime fixture points outside this authorized retest root. Equivalent behavior was independently exercised only in the allowed run directory.
- The broader existing backend suite was not run because its fixtures create/delete `tests/runtime` locations outside `db-retest-20260728T181807Z`, which this retest was prohibited from touching.
- No browser-driven UI smoke test was run; lint/build provide compile-time assurance only.
- PostgreSQL connection, migration execution against a real PostgreSQL service, and PostgreSQL repository semantics remain unverified. The current adapter source establishes that it will fail closed after migration until the repository is implemented.

## Conclusion

The explicit PostgreSQL URL P2 safety issue is resolved as a fail-closed behavior: explicit non-SQLite URLs did not produce SQLite state in isolated process tests, and unset configuration retained isolated SQLite behavior. There is no newly observed P0. A P1 remains: Compose V1 is not operational with the selected PostgreSQL backend. Compose runtime did not truly run; validation is BLOCKED. This report does not make final UI/API acceptance claims.

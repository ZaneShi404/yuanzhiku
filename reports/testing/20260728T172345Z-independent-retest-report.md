# Independent Retest Report - 20260728T172345Z

## Findings

### P2 - Compose PostgreSQL URL silently falls back to SQLite

`REQ-045` requires a PostgreSQL production adapter/migrations and a Compose deployment. The checked Compose URL uses `postgresql+psycopg://...`, but `ApplicationServices` recognizes only URLs beginning `postgresql://` or `postgres://`; it then creates `SqliteRepository` unconditionally. The intended PostgreSQL adapter's fail-closed behavior is bypassed.

- Reproduction / command: `YUANZHIKU_DATA_ROOT='E:/源知库/tests/runtime/retest-20260728T171033Z/postgres-url-selection' YUANZHIKU_DATABASE_URL='postgresql+psycopg://invalid:invalid@127.0.0.1:1/invalid' PYTHONPATH=/e/源知库/backend /e/源知库/.venv/Scripts/python.exe -c "from app.main import ApplicationServices; from app.core.config import data_paths; services=ApplicationServices(data_paths()); print({'database_path': str(services.paths.database), 'sqlite_exists': services.paths.database.is_file(), 'repository': type(services.repository).__name__})"`.
- Actual result: `{'sqlite_exists': True, 'repository': 'SqliteRepository'}`. No PostgreSQL connection was attempted, despite an invalid PostgreSQL URL.
- Relevant files: [main.py](E:/源知库/backend/app/main.py:53), [postgres.py](E:/源知库/backend/app/adapters/postgres.py:3), and [docker-compose.yml](E:/源知库/docker-compose.yml:8).
- Impact: the declared Compose database configuration is not honest in its current form. The application can run against SQLite while the Compose environment says PostgreSQL.

No P0 or P1 regression was reproduced. All seven original P1 paths pass the independent retests below. This remaining P2 does not clear `REQ-045` and Compose/PostgreSQL runtime was not executed because Docker is unavailable.

## Scope And Independence

- Tester role: fresh independent retest agent.
- I did **not** read `E:\源知库\reports\development\` or developer conversation/reports. I read frozen documents under `docs/`, the original independent test report, current source, current Git state, and test outcomes.
- I did not modify production code, existing tests, frozen requirements, or existing reports. No commit was made.
- New isolated root: `E:\源知库\tests\runtime\retest-20260728T171033Z`.
- Every application data root used during testing was a descendant of that root. `E:\源知库\data` was not used or changed.
- The only new report is this file. Harnesses and captured results remain under the permitted isolated runtime root.
- Severity convention: P0 is a system-wide security/data-loss blocker; P1 is material data, evidence, recovery, or security integrity failure; P2 is an unmet required capability or contract with narrower immediate impact; P3 is maintainability/architecture.

## Closed Original P1 Retests

| Test ID | Regression / requirement | Independent reproduction and result |
| --- | --- | --- |
| `T-ING-NEG-003-R` | Failed ingest artifact compensation; preserve shared artifact (`REQ-011`, `REQ-012`) | Injected `sqlite3.IntegrityError` after the artifact write. Failed new ingest left `[]` artifacts. A failure using an already referenced SHA retained exactly one verified shared artifact. Evidence: [focused-results.json](E:/源知库/tests/runtime/retest-20260728T171033Z/focused-results.json:3). Harness: [independent_retest.py](E:/源知库/tests/runtime/retest-20260728T171033Z/independent_retest.py). |
| `T-BACK-NEG-001-R` | Same-timestamp backup uniqueness and archive/metadata retention consistency (`REQ-040`) | Froze time to `2030-01-02T03:04:05Z`; two backup calls created distinct UUID-suffixed names. Retention left one same-day successful record and its archive existed. A separate 31-day seeded set retained 30 records/files and removed the oldest. Evidence: [focused-results.json](E:/源知库/tests/runtime/retest-20260728T171033Z/focused-results.json:11). |
| `T-LOC-001-R` | Accurate multi-page PDF / multi-paragraph DOCX evidence locators, or unknown (`REQ-020`, `REQ-021`, `REQ-023`) | Uploaded synthetic two-page PDF and two-paragraph DOCX through the API, ran parse jobs, and fetched evidence. PDF locators were page `1` and `2`; DOCX locators were paragraph ordinals `1` and `2`. No false all-content page/paragraph `1` locator was returned. Evidence: [focused-results.json](E:/源知库/tests/runtime/retest-20260728T171033Z/focused-results.json:22). Parser segment construction: [parsers.py](E:/源知库/backend/app/adapters/parsers.py:48), [parsers.py](E:/源知库/backend/app/adapters/parsers.py:69). |
| `T-BACK-NEG-003-R` | Reimport conflict is documented `4xx` and leaves no orphan (`REQ-041`, API contract line 18) | Target held an external-card natural-key collision; donor export also contained a distinct artifact. `POST /api/v1/reimports` returned `409`, `detail.conflicts` listed the external-card uniqueness conflict, target artifact files were `[]` before/after, and target source count was `0`. Evidence: [focused-results.json](E:/源知库/tests/runtime/retest-20260728T171033Z/focused-results.json:63). |
| `T-LIFE-NEG-001-R` | Purge all sharing-source states without FK failure and delete only when eligible (`REQ-012`, `REQ-034`) | Created two parsed sources sharing one artifact, soft-deleted both, then purged each. First purge returned `200` with `unreferenced_artifacts_removed: 0` and retained the file; second returned `200` with `1` and removed it. No FK error. Evidence: [focused-results.json](E:/源知库/tests/runtime/retest-20260728T171033Z/focused-results.json:78). |
| `T-JOB-CONFIG-001-R`, `T-JOB-BREAKER-001-R-timeout`, `T-JOB-BREAKER-002-R-no-progress` | Observable/enforced timeout and no-progress controls; no false success (`REQ-032`, `REQ-033`) | API configured `max_retry_attempts: 0`, timeout `60`, and no-progress `60`; new parse job had `max_attempts: 0`, and injected breaker observed both values and ended `failed` with source processing `failed`. Direct child-process tests separately produced `解析超时断路器已触发` and `解析无进展断路器已触发`, terminating/joining the child and closing the queue in each case. Evidence: [focused-results.json](E:/源知库/tests/runtime/retest-20260728T171033Z/focused-results.json:93) and [timed-parser-results.json](E:/源知库/tests/runtime/retest-20260728T171033Z/timed-parser-results.json:1). |
| `T-SEC-EXT-001-R` | Generic URL userinfo rejected/redacted; stored/list/export payload clean (`REQ-030`, `REQ-041`, API contract line 18) | `POST /api/v1/external/cards` with `https://user:independent-secret@example.test/path` returned `422`. A deliberately injected legacy userinfo URL did not expose the secret through `GET /external/cards` or exported `records.json`. Evidence: [focused-results.json](E:/源知库/tests/runtime/retest-20260728T171033Z/focused-results.json:106). Validation and redaction paths: [external_cards.py](E:/源知库/backend/app/services/external_cards.py:16), [sqlite.py](E:/源知库/backend/app/adapters/sqlite.py:668). |

## Prior P2 Retests

- `T-JOB-CONFIG-001-R`: PASS. API setting `max_retry_attempts: 0` created a new parse job with `max_attempts: 0`; see [focused-results.json](E:/源知库/tests/runtime/retest-20260728T171033Z/focused-results.json:93).
- `T-META-SEARCH-001-R`: PASS. Metadata revision endpoint returned two snapshots after an update, newest title was `Alpha title`; `sort=title` returned `200` and invalid sort returned `422`; see [focused-results.json](E:/源知库/tests/runtime/retest-20260728T171033Z/focused-results.json:112).
- Compose/PostgreSQL: FAIL as P2, described in the finding above. Docker absence blocks container-level verification and does not count as a pass.

## Executed Commands And Results

| Command / test | Result |
| --- | --- |
| `YUANZHIKU_EMBEDDED_WORKER=false PYTHONPATH=/e/源知库/backend /e/源知库/.venv/Scripts/python.exe tests/runtime/retest-20260728T171033Z/independent_retest.py` | PASS. Seven original P1 retests and metadata/search P2 retest passed; structured responses in [focused-results.json](E:/源知库/tests/runtime/retest-20260728T171033Z/focused-results.json:1). |
| `PYTHONPATH=/e/源知库/backend /e/源知库/.venv/Scripts/python.exe tests/runtime/retest-20260728T171033Z/timed_parser_retest.py` | PASS. Timeout and no-progress handlers terminate a simulated hanging parser process, not a successful job; [timed-parser-results.json](E:/源知库/tests/runtime/retest-20260728T171033Z/timed-parser-results.json:1). |
| Isolated Uvicorn: `YUANZHIKU_DATA_ROOT=.../http-loopback YUANZHIKU_EMBEDDED_WORKER=false ... uvicorn app.main:application --factory --host 127.0.0.1 --port 8782 --no-access-log` followed by [http_loopback_client.py](E:/源知库/tests/runtime/retest-20260728T171033Z/http_loopback_client.py) | PASS. `/health` `200` reported `network: 127.0.0.1 only`; capabilities reported external-card fetch `false`; userinfo card returned `422`; valid card returned `201`; secret was absent from listing. Evidence: [http-loopback-results.json](E:/源知库/tests/runtime/retest-20260728T171033Z/http-loopback-results.json:1). Server stopped after testing. |
| `YUANZHIKU_EMBEDDED_WORKER=false PYTHONPATH=/e/源知库/backend /e/源知库/.venv/Scripts/python.exe -m pytest tests/runtime/retest-20260728T171033Z/broad_api_retest.py -q` | PASS: `8 passed in 48.02s`. Coverage includes health/OpenAPI, normal import/evidence, knowledge publishing, external/Douyin validation, backup/restore/export/reimport/verify, lifecycle, file import/search, manual representation, DOCX paragraphs, and validation. Harness: [broad_api_retest.py](E:/源知库/tests/runtime/retest-20260728T171033Z/broad_api_retest.py). |
| `rg` audit for Python outbound clients (`requests`, `httpx`, `aiohttp`, `urllib.request`, `http.client`, `axios`, `fetch`, etc.) | Backend application source had no outbound HTTP client use. The only application `fetch` was frontend [App.tsx](E:/源知库/frontend/src/App.tsx:26), using relative `/api/v1` paths. Static evidence plus capability/API behavior supports the no-external-fetch restriction; no live external URL was requested. |
| `npm run lint`, `npm run build` in `E:\源知库\frontend` | BLOCKED: each exited `1` at `'tsc' 不是内部或外部命令` because `frontend/node_modules` is absent. Dependencies were not installed, to preserve the restriction against workspace modification outside the allowed runtime/report paths. |
| `docker compose version && docker compose -f E:/源知库/docker-compose.yml config` | BLOCKED: `docker: command not found`. This is not passed validation. |

## Skipped Or Blocked

- Existing checked-in unit files were not run directly because their fixtures delete/recreate other `tests/runtime/*` roots, contrary to this retest's isolated-data restriction. Equivalent independent API regression coverage was placed and run only under `tests/runtime/retest-20260728T171033Z`; it passed `8/8`.
- Frontend lint/build are blocked by absent dependencies, not passed.
- Docker Compose/PostgreSQL integration, migrations, loopback publication, worker/API interaction, and Redis behavior are blocked by absent Docker, not passed.
- GUI/browser acceptance (`T-UI-001`, REQ-044) was not run. This retest did verify loopback HTTP API behavior, not browser UI behavior.
- No live generic or Douyin URL was fetched by design. Outbound restrictions are supported by source audit and endpoint behavior, not network packet capture.
- Disk/memory circuit-breaker thresholds and a real pathological parser process remain unexecuted. Timeout and no-progress termination branches were directly controlled and exercised.

## Residual Risk

- P2 Compose/PostgreSQL configuration remains incorrect and unverified at runtime.
- Container operation, actual PostgreSQL constraints/migrations, and frontend TypeScript build are outstanding validation gaps.
- PDF preview link isolation, responsive UI workflows, and browser-only acceptance behaviors remain unverified.
- The test harness used synthetic artifacts and controlled parser doubles. It does not substitute for broader adversarial parser-resource testing.

## Decision

This is **not final black-box acceptance**. No P0/P1 failure remains from the seven original reported regressions under the independently executed isolated retests. Acceptance remains blocked for any scope requiring `REQ-045` Compose/PostgreSQL compliance because of the P2 defect and unavailable Docker validation.

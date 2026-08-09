# Independent Port Lifecycle Retest

Timestamp: 2026-07-29T16:48:36 local run identifier
Role: Fresh independent TEST engineer
Workspace: `E:\源知库`
Runtime isolation: `E:\源知库\tests\runtime\independent-port-lifecycle-retest-20260729T164836`

## Scope And Independence

This retest read frozen requirements, acceptance mapping, API contract, recovery operations, test plan, dependency guidance, current application behavior, current committed tests, and relevant independent testing reports. It did not read `reports/development/`, Git history/messages, previous agent outputs, or the normal `E:\源知库\data` directory. It did not modify source, committed tests, or previous reports, and did not commit.

All new verifier scripts, results, temporary test directories, caches, SQLite data roots, and test payloads are under the runtime isolation root. The normal data directory was never accessed or deleted.

P0/P1/P2/P3 labels use operational severity because frozen requirements do not define the labels.

## Findings

### P0

No P0 defect was reproduced in this retest.

### P1: Live PostgreSQL source-to-target backup/restore remains unverified and release-blocking

`REQ-041` and `REQ-045` require a PostgreSQL production path and restore to a separate empty target. A physical PostgreSQL logical backup and restore was not possible in the available environment:

- `POSTGRES_TEST_URL` was unset.
- `POSTGRES_RESTORE_TEST_URL` was unset.
- `docker`, Docker Compose, and `psql` were unavailable.

The full suite skipped exactly two tests that require separate disposable source and empty-target PostgreSQL URLs. No substitute static or mock result is presented as physical PostgreSQL restore acceptance.

**Disposition: P1 release blocker.** Supply disposable PostgreSQL source and separate initially empty target URLs, then execute a real complete backup, migration/restore, and post-restore verification without SQLite fallback.

### P2

No P2 defect was reproduced in the port lifecycle, external UTF-8 multipart, default SQLite, explicit unreachable PostgreSQL fail-closed, or committed unit-suite scope.

### P3

No P3 defect was reproduced in the exercised scope.

## External Runtime Evidence

The primary port checks were executed through the real `scripts/start-windows.ps1` launcher and actual Windows processes. They did not use TestClient or source-review as the primary acceptance method.

| Check | Result |
| --- | --- |
| Explicit initial free `P=52898` with isolated `-DataRoot` | PASS. Launcher exited 0, one listener was present at `127.0.0.1:52898`, `GET /api/v1/health` returned `status=ok`, and the health data root matched the isolated root. `state/port.json` was UTF-8 `{"port": 52898}`. |
| Stop created service, relaunch same root without `-Port` | PASS. The verifier stopped only listener PID `38752`; no-port launcher reused P and the exact `port.json` bytes did not change. |
| Explicit free `Q=62619`, then no-port relaunch | PASS. Q intentionally replaced P as the persisted preference; launcher health/listener checks passed; no-port relaunch reused Q with unchanged exact `port.json` bytes. |
| Saved Q occupied by controlled unrelated loopback listener | PASS. Launcher exited 1 with `Saved local port 62619 is occupied by another process; the saved port preference was not changed.` There was only the verifier-owned unrelated listener, no secondary service, and Q `port.json` bytes/value were unchanged. The controlled listener was then closed. |
| Same-root active instance behavior | PASS. A second launcher invocation exited 0 and retained exactly one listener with the same PID and unchanged port-file bytes. |
| Final cleanup | PASS. No listener remained on P, Q, or the separate multipart service port. |

Raw structured result: `tests/runtime/independent-port-lifecycle-retest-20260729T164836/port-lifecycle-results.json` contains 24 passed steps and zero failed steps.

## UTF-8 Multipart Evidence

A real service started via `scripts/start-windows.ps1` received a standards-compliant Node 24 `fetch` plus `FormData` multipart upload. It returned `201`; a subsequent API read verified unchanged Chinese title, author, notes, and tags:

- title: `中文标题，UTF-8`
- author: `作者甲`
- notes: `备注：中文不应改变。`
- tags: `UTF-8`, `中文标签`

This is external HTTP behavior and not TestClient evidence. The service was then stopped by the verified listener PID.

A separate raw `curl.exe` invocation from the Git Bash shell returned `422` for Chinese multipart field values. This is recorded as a client/shell encoding comparison only: the standard Node UTF-8 sender passed, while the raw curl shell invocation was rejected. No packet capture was taken, so this observation is not classified as a backend failure.

Raw structured result: `tests/runtime/independent-port-lifecycle-retest-20260729T164836/multipart-results.json`.

## Isolated Test Commands And Results

All pytest commands used `PYTHONPATH`, runtime directories, pytest base temporary roots, and pytest cache directories under this retest root.

| Command | Result |
| --- | --- |
| `PYTHONPATH=E:/源知库/backend YUANZHIKU_TEST_RUNTIME=E:/源知库/tests/runtime/independent-port-lifecycle-retest-20260729T164836/suite-runtime E:/源知库/.venv/Scripts/python.exe -m pytest -q E:/源知库/tests/runtime/independent-port-lifecycle-retest-20260729T164836/test_independent_database_selection.py E:/源知库/tests/unit/test_api.py E:/源知库/tests/unit/test_defect_fixes.py E:/源知库/tests/unit/test_database_url_selection.py E:/源知库/tests/unit/test_postgres_repository.py --basetemp=E:/源知库/tests/runtime/independent-port-lifecycle-retest-20260729T164836/pytest-focused-tmp -o cache_dir=E:/源知库/tests/runtime/independent-port-lifecycle-retest-20260729T164836/pytest-focused-cache` | PASS: `60 passed, 2 skipped` in 258.06s. |
| `PYTHONPATH=E:/源知库/backend YUANZHIKU_TEST_RUNTIME=E:/源知库/tests/runtime/independent-port-lifecycle-retest-20260729T164836/full-suite-runtime E:/源知库/.venv/Scripts/python.exe -m pytest -q E:/源知库/tests/unit --basetemp=E:/源知库/tests/runtime/independent-port-lifecycle-retest-20260729T164836/pytest-full-tmp -o cache_dir=E:/源知库/tests/runtime/independent-port-lifecycle-retest-20260729T164836/pytest-full-cache` | PASS: `57 passed, 2 skipped` in 252.99s. |

The two skips are the PostgreSQL physical integration tests gated by absent `POSTGRES_TEST_URL` and `POSTGRES_RESTORE_TEST_URL`.

## Database Regression Evidence

The independently created isolated database check passed in the focused run:

- Unset `YUANZHIKU_DATABASE_URL` selected SQLite and created only the isolated `state/knowledge.db`.
- Explicit unreachable `postgresql+psycopg://invalid:invalid@127.0.0.1:1/invalid` failed with an actionable PostgreSQL error and created no SQLite database.
- Explicit unsupported MySQL URL failed configuration validation and created no SQLite database.

These establish local default/fail-closed selection behavior. They do not establish live PostgreSQL connectivity, migration semantics, backup, or restore.

## Acceptance And Release Status

**Port lifecycle acceptance: PASS.**

**UTF-8 multipart metadata acceptance: PASS with a standards-compliant UTF-8 sender.** The curl.exe observation is a shell/client encoding limitation, not an application defect.

**Overall release status: BLOCKED / NOT RELEASE-READY.** The live PostgreSQL source-to-separate-empty-target backup/restore requirement is absent from the environment and remains unverified. This is a P1 release blocker, not a passing result.

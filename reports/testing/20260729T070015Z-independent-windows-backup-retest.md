# Independent Windows Backup Retest

Timestamp: 2026-07-29T07:00:15Z
Role: Fresh independent TEST engineer
Workspace: E:\源知库 (actual workspace path withheld from report body)
Runtime isolation: E:\源知库\tests\runtime\independent-windows-backup-retest-20260729T064142Z (actual workspace path withheld from report body)

## Scope And Restrictions

Read frozen requirements, recovery guidance, current source/tests, the named prior independent retest, and relevant infrastructure reports. Did not read reports/development, Git commits or logs, or the normal application data directory. Did not modify application source, committed tests, or another tester's report, and did not commit.

All new verifier code, generated archives, SQLite databases, copied unit-test inputs, pytest base directories, and pytest caches are below the isolated runtime root. The exact physical workspace and runtime paths are provided in the caller-facing report link and command evidence, rather than duplicated here.

## Findings

### P0

No P0 defect was reproduced.

### P1: PostgreSQL logical-backup restore catalog path safety

No P1 defect was reproduced in the local restore-validation flow.

An independently written verifier built complete logical PostgreSQL-backup payloads and passed them through `TransferService._restore_archive()`. It used a recording PostgreSQL repository only to observe target construction, initialization/migration entry, and backup-row insertion; therefore this is an executable local ordering and validation check, not a live PostgreSQL restore.

- 86 unsafe `backups.archive_name` cases were rejected: forward/backslash separators, drive-relative and drive-qualified forms, parent traversal, UNC, extended/device syntax, every C0 control character, every C1 control character, device names, device names with further extensions, and trailing dot/space forms.
- For every rejected case, the initially absent target root remained absent. The recording PostgreSQL repository was neither constructed nor initialized, and no backup-row write was reached.
- A basename from a genuine normal complete backup created by the service was accepted in a complete logical-backup catalog and reached the expected target initialization/catalog insertion flow.

This removes the previously reproduced local P1 condition. It does not prove a physical PostgreSQL migration or database write, which remains covered by the live integration blocker below.

### P2: Portable export/reimport under a long Windows root

No P2 defect was reproduced.

A real local SQLite donor/recipient application flow created a source, parsed representation/evidence, artifact, complete backup, and portable export. The recipient data root was extended so the prior-style stage filename was 285 characters, exceeding the legacy 260-character boundary. The current possible stage filename was 211 characters and the artifact destination was 243 characters. Reimport succeeded.

- Business source, representation/evidence chain, and hash-verified artifact were restored.
- Recipient logical export records exactly matched the donor's export records.
- The portable archive contained no SQLite snapshot or `backups` catalog, and a pre-existing donor catalog marker was absent from all archive member bytes.
- Recipient `backups` catalog was empty.
- Successful reimport left no `.part` files anywhere below the recipient root.
- A manifest-valid archive with a record/artifact hash mismatch was rejected without artifact, logical-record, or `.part` residue. A malformed archive was also rejected without staging residue.

This removes the previously reproduced local P2 condition for the exercised Windows path shape and SQLite portable flow.

### P3

No P3 defect was reproduced.

### Release-Blocking Unresolved Validation

The mandatory live PostgreSQL source-to-separate-empty-target backup/restore remains unresolved and is release-blocking. It was not represented as a pass.

At environment inspection time, `POSTGRES_TEST_URL` and `POSTGRES_RESTORE_TEST_URL` were both unset. `docker` and `psql` were unavailable, consistent with the relevant infrastructure readiness report. Per scope, no Docker installation, host configuration, or endpoint discovery was attempted. Without separately supplied disposable source and empty target PostgreSQL URLs, it was not safe or possible to run a physical PostgreSQL backup, migration, restore, and post-restore catalog query.

## Commands And Results

All Python/pytest commands used `PYTHONPATH` and runtime/cache locations below the isolated runtime root.

| Command | Result |
| --- | --- |
| `PYTHONPATH="E:/source/workspace/backend" "E:/source/workspace/.venv/Scripts/python.exe" "E:/source/workspace/tests/runtime/independent-windows-backup-retest-20260729T064142Z/independent_verifier.py"` | Exit 0. 86 unsafe catalog cases rejected before target setup; valid generated complete-backup name accepted; long-root portable reimport, catalog exclusion, cleanup, default SQLite, unreachable PostgreSQL fail-closed, and unsupported URL fail-closed checks passed. Structured results: `independent-verifier-results.json`. |
| `PYTHONPATH="E:/source/workspace/backend" YUANZHIKU_TEST_RUNTIME="E:/source/workspace/tests/runtime/independent-windows-backup-retest-20260729T064142Z/suite-runtime" "E:/source/workspace/.venv/Scripts/python.exe" -m pytest -q "E:/source/workspace/tests/runtime/independent-windows-backup-retest-20260729T064142Z/suite/tests/unit/test_database_url_selection.py" "E:/source/workspace/tests/runtime/independent-windows-backup-retest-20260729T064142Z/suite/tests/unit/test_postgres_repository.py" --basetemp="E:/source/workspace/tests/runtime/independent-windows-backup-retest-20260729T064142Z/pytest-focused-tmp" -o cache_dir="E:/source/workspace/tests/runtime/independent-windows-backup-retest-20260729T064142Z/pytest-focused-cache"` | Exit 0: `35 passed, 2 skipped` in 121.62s. The two skips require both live PostgreSQL endpoint variables. |
| `PYTHONPATH="E:/source/workspace/backend" YUANZHIKU_TEST_RUNTIME="E:/source/workspace/tests/runtime/independent-windows-backup-retest-20260729T064142Z/suite-runtime" "E:/source/workspace/.venv/Scripts/python.exe" -m pytest -q "E:/source/workspace/tests/runtime/independent-windows-backup-retest-20260729T064142Z/suite/tests/unit" --basetemp="E:/source/workspace/tests/runtime/independent-windows-backup-retest-20260729T064142Z/pytest-full-tmp" -o cache_dir="E:/source/workspace/tests/runtime/independent-windows-backup-retest-20260729T064142Z/pytest-full-cache"` | Exit 0: `55 passed, 2 skipped` in 244.60s. The same two PostgreSQL integration tests were skipped for absent endpoint variables. |

The copied test inputs were made before execution. Only their runtime-location references were adjusted in the isolated copy so all data creation stayed under the isolated runtime root; project test sources were not altered.

## Database Selection Check

The independent verifier confirmed that an unset `YUANZHIKU_DATABASE_URL` creates and selects SQLite under its isolated root. An explicitly unreachable `postgresql+psycopg` URL raises rather than creating a SQLite database. An explicit unsupported MySQL URL raises configuration error rather than creating SQLite. These are local configuration/runtime checks, not PostgreSQL connectivity validation.

## Acceptance Gate

**BLOCKED / NOT RELEASE-READY.**

No active P0-P3 defect was reproduced in the completed local checks. The prior P1 archive-name safety and P2 long-path reimport failures are locally resolved by executable verification and a passing full unit suite. Release acceptance remains blocked until an actual disposable PostgreSQL source URL and a separate, initially empty PostgreSQL target URL are supplied and a physical complete logical backup/restore is executed and queried. That run must verify migrations, source data, artifacts, restored catalog records, and the new data-root constraint without SQLite fallback.

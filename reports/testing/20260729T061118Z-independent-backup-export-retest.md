# Independent Backup/Export Retest

Timestamp: 2026-07-29T06:11:18Z
Role: Independent TEST / verification
Workspace: `E:\源知库`
Runtime isolation: `E:\源知库\tests\runtime\independent-backup-export-retest-20260729T055141Z`

## Scope and Restrictions

Read frozen `docs/requirements.md`, `docs/test-plan.md`, `docs/operations-and-recovery.md`, current source, current tests, the named prior independent testing report, and relevant infrastructure report. Did not read `reports/development/*`, Git commit messages/logs, or `E:\源知库\data`; did not alter application source or existing project tests; did not commit. All new scripts, generated archives, databases, copied test modules, and test data are below the isolated runtime root above.

## Findings

### P1: Complete PostgreSQL logical-backup catalog validation still accepts Windows-invalid archive names before target mutation

The prior P1 is only partially fixed. `TransferService._backup_records()` now invokes `_validate_backup_catalog()` before `PostgresRepository` construction, `initialize()`, `target.create()`, artifact extraction, or catalog insertion. The validator correctly rejects the prior null, wrong type, blank, nested/absolute slash path, digest, state, timezone, and duplicate cases before a missing target root is created.

However, its `archive_name` validation at `backend/app/services/transfers.py:317-323` only rejects `/`, `\\`, `.`/`..`, and non-`.zip` values. It accepts a NUL-containing name (`bad\x00.zip`) and a drive-qualified Windows name (`C:.zip`). Both are invalid file names on the Windows deployment target; `PureWindowsPath(r"E:\isolated\backups") / "C:.zip"` yields `C:.zip` with drive `C:`, so it is not a safe child archive path.

Independent executable evidence:

- Each malformed archive had a complete `records.json` and SHA-verified manifest.
- For both names, `_restore_archive(..., target_root=<missing path>, target_database_url="postgresql://target")` did not raise validation error.
- Recording fake PostgreSQL `initialize()` count was `1`; the previously missing target root existed after return.
- Therefore untrusted catalog data can reach PostgreSQL target initialization and create/modify the target root, violating the requested validation-before-mutation condition.

Required correction: use an explicit portable archive-name grammar rather than path substring checks. At minimum, reject all control characters (including NUL), `:`, Windows reserved device names, trailing dot/space, any path separator, absolute/drive/UNC syntax, and anything other than a single safe filename ending `.zip`. Add project tests that assert no target repository initialization and no target-root creation for those values.

### P2: Portable reimport fails under a long but user-selectable Windows data root

`TransferService.reimport()` writes a temporary staging file named `reimport-<64-char-sha256>-<32-char-uuid>.part` at `backend/app/services/transfers.py:470-473`. On this Windows host, a synthetically selected isolated target root produced a 312-character staging path. The recipient had an existing `staging` directory, but `stage.open("xb")` raised `FileNotFoundError [Errno 2]` due to the effective Windows path-length limit.

This independently explains the sole full-suite regression failure and is reproducible outside the copied-test layout. It affects reimport rather than backup-catalog exclusion, but is relevant to REQ-041 portable reimport. Use a bounded staging filename or a short per-operation staging directory, and add a Windows long-root test.

### Fixed: SQLite portable export no longer carries the operational `backups` catalog

The second prior P1 is fixed in current behavior. `TransferService._build_archive()` now adds `state/knowledge.db` only when the backend is SQLite *and* `archive_type == "backup"` (`backend/app/services/transfers.py:90-96`).

The isolated SQLite run created a source, artifact, and pre-existing `backups` catalog marker, then created both complete backup and portable export:

- Complete backup contained `state/knowledge.db`; its logical records and queried SQLite snapshot each contained the prior catalog row. This preserves the required complete operational-backup distinction.
- Portable export ZIP members were exactly `records.json`, one content-addressed artifact, and `manifest.json`. No SQLite/database member or `state/knowledge.db` was present.
- `records.json`, manifest entries, and raw bytes of every ZIP member contained no `backups` table or prior catalog marker.
- Archive verification succeeded. Reimport to a separate isolated SQLite root succeeded, restored the source and verified artifact, and left the recipient `backups` catalog empty.

## Automated and Local Results

| Command | Result |
| --- | --- |
| `PYTHONPATH="E:/源知库/backend" "E:/源知库/.venv/Scripts/python.exe" "E:/源知库/tests/runtime/independent-backup-export-retest-20260729T055141Z/independent_verifier.py"` | Exit `1`: 23 catalog cases passed, including null/type/blank/path separator/digest/state/timestamp/duplicate rejection before target mutation; valid complete catalog accepted; two Windows-invalid archive-name cases failed as P1. SQLite complete backup and portable export/reimport checks passed. Results: `independent-verifier-results.json`. |
| `PYTHONPATH="E:/源知库/backend" "E:/源知库/.venv/Scripts/python.exe" "E:/源知库/tests/runtime/independent-backup-export-retest-20260729T055141Z/configuration_sanity.py"` | Exit `0`: default backend `sqlite`, default `state/knowledge.db` exists; unreachable `postgresql+psycopg` raises `RuntimeError` with no SQLite database created; unsupported `mysql://` raises `DatabaseUrlConfigurationError` with no SQLite database created. |
| `PYTHONPATH="E:/源知库/backend" "E:/源知库/.venv/Scripts/python.exe" -m pytest -q "E:/源知库/tests/runtime/independent-backup-export-retest-20260729T055141Z/unit" --basetemp="E:/源知库/tests/runtime/independent-backup-export-retest-20260729T055141Z/pytest-full-tmp" -o cache_dir="E:/源知库/tests/runtime/independent-backup-export-retest-20260729T055141Z/pytest-cache-full"` | `37 passed, 2 skipped, 1 failed` in 204.35s. The one failure is the independently reproduced P2 long-path staging `FileNotFoundError`; skips require live PostgreSQL source and restore URLs. |
| `PYTHONPATH="E:/源知库/backend" "E:/源知库/.venv/Scripts/python.exe" "E:/源知库/tests/runtime/independent-backup-export-retest-20260729T055141Z/long_path_reimport.py"` | Exit `0`; recipient staging directory exists, calculated temporary path length is `312`, and reimport raises `FileNotFoundError`. |

A prior copied focused command produced `25 passed, 2 skipped, 1 failed` in 73.57s for the same staging filename issue. It is superseded by the full run and direct reproducer above.

## Live PostgreSQL Integration Blocker

A real PostgreSQL source-to-separate-empty-target backup/restore was not run and is not a pass.

- `POSTGRES_TEST_URL` is unset.
- `POSTGRES_RESTORE_TEST_URL` is unset.
- `psql` is unavailable.
- `docker` is unavailable; the permitted infrastructure readiness report records no Docker Desktop or usable Compose environment.

No endpoint or credentials were available for a safe separate source/target attempt. Therefore physical PostgreSQL connection, PostgreSQL migration, complete logical backup creation, restore into a separate empty target database, and post-restore catalog query remain unresolved release-blocking validation.

## Acceptance Gate

**BLOCKED / FAIL.**

The portable SQLite export/catalog P1 is fixed and verified locally. Release acceptance remains blocked by the P1 incomplete catalog archive-name validation, the P2 long-root reimport failure, the one failing full local regression, and missing mandatory live PostgreSQL source-to-empty-target verification. No P0 defect was evidenced.

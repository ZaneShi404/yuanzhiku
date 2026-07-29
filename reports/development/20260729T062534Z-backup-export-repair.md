# Backup and Export Repair

Timestamp: 2026-07-29T06:25:34Z
Role: Independent development repair
Workspace: `E:\源知库`
Runtime isolation: `E:\源知库\tests\runtime\development-repair-20260729T062534Z`

## Scope

Read frozen requirements and recovery/test documentation, current source and tests, and `reports/testing/20260729T061118Z-independent-backup-export-retest.md`. Did not read `reports/development/*` or Git history/messages. Did not read or modify `E:\源知库\data`, existing independent testing reports, or unrelated worktree files.

## Repairs

- Added strict portable Windows-safe `backups.archive_name` validation for logical backup catalog rows. The validator permits current generated archive names and rejects separators, drive/colon syntax, control characters, reserved device basenames and extension variants, trailing dot/space forms, traversal, and non-`.zip` values.
- Moved logical backup record validation ahead of target path resolution, PostgreSQL repository construction/initialization, target-root creation, migrations, artifact extraction, and database writes.
- Centralized short UUID `.part` staging paths in `ArtifactStore` and used them for reimport while retaining exclusive create, SHA-256 verification, atomic `os.replace`, and cleanup behavior.
- Added isolated-runtime support to unit fixtures and regression coverage for malformed catalog names with an absent sentinel target, long-root portable reimport, and temporary-stage cleanup after a hash failure.

## Commands and Results

| Command | Result |
| --- | --- |
| `PYTHONPATH="E:/源知库/backend" YUANZHIKU_TEST_RUNTIME="E:/源知库/tests/runtime/development-repair-20260729T062534Z" "E:/源知库/.venv/Scripts/python.exe" -m compileall -q "E:/源知库/backend/app"` | Exit 0. |
| `PYTHONPATH="E:/源知库/backend" YUANZHIKU_TEST_RUNTIME="E:/源知库/tests/runtime/development-repair-20260729T062534Z" "E:/源知库/.venv/Scripts/python.exe" -m pytest -q "E:/源知库/tests/unit/test_postgres_repository.py" --basetemp="E:/源知库/tests/runtime/development-repair-20260729T062534Z/pytest-focused-tmp" -o cache_dir="E:/源知库/tests/runtime/development-repair-20260729T062534Z/pytest-focused-cache"` | Exit 0: 27 passed, 2 skipped in 117.25s. |
| `PYTHONPATH="E:/源知库/backend" YUANZHIKU_TEST_RUNTIME="E:/源知库/tests/runtime/development-repair-20260729T062534Z" "E:/源知库/.venv/Scripts/python.exe" -m pytest -q "E:/源知库/tests/unit" --basetemp="E:/源知库/tests/runtime/development-repair-20260729T062534Z/pytest-full-tmp" -o cache_dir="E:/源知库/tests/runtime/development-repair-20260729T062534Z/pytest-full-cache"` | Exit 0: 55 passed, 2 skipped in 258.84s. |

## Blockers

- `POSTGRES_TEST_URL` and `POSTGRES_RESTORE_TEST_URL` were unset. The two PostgreSQL integration tests remained conditionally skipped; no live source-to-separate-empty-target PostgreSQL backup/restore was run.

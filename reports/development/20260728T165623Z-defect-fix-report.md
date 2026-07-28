# Defect Fix Report - 20260728T165623Z

## Scope And Status

This is a developer repair and self-test report only. It is not an independent test report and makes no independent-test or user-acceptance claim.

The repair work was based on `reports/testing/20260728T225152Z-independent-test-report.md`. All application test data used for this repair was under `tests/runtime/devfix-*`; `E:\源知库\data` was not read, written, or modified.

## Independent Baseline Reproduction

The seven reported P1 paths were rerun against a temporary `git archive HEAD` copy under `tests/runtime/devfix-baseline-repro-20260728T225152Z` before verification of the repair. The temporary copy produced the following defects and was removed after recording the result:

| Finding | Baseline reproduction result | Requirement |
| --- | --- | --- |
| P1-1 | Injected logical-record failure left `orphan_artifacts: 1`. | `REQ-011`, `REQ-012` |
| P1-2 | Two backups frozen to one second used the same name; one success record remained while backup files were `0`. | `REQ-040` |
| P1-3 | Two-page PDF produced one evidence record with `page: 1` and a 41-character full-extraction range. | `REQ-020`, `REQ-021`, `REQ-023` |
| P1-4 | A natural-key external-card conflict raised `IntegrityError` after `artifact_delta: 1`. | `REQ-041` |
| P1-5 | Purging one of two soft-deleted sources sharing an artifact raised `IntegrityError`. | `REQ-012`, `REQ-034` |
| P1-6 | Configured parser limits were not read by execution and the job completed `succeeded`. | `REQ-033` |
| P1-7 | A generic URL with userinfo exported the embedded credential. | `REQ-030`, `REQ-041` |

Commands executed against that temporary baseline:

```text
E:\源知库\.venv\Scripts\python.exe tests/runtime/devfix-baseline-repro-20260728T225152Z/reproduce_baseline_subset.py
E:\源知库\.venv\Scripts\python.exe tests/runtime/devfix-baseline-repro-20260728T225152Z/reproduce_baseline_lifecycle_jobs.py
E:\源知库\.venv\Scripts\python.exe tests/runtime/devfix-baseline-repro-20260728T225152Z/reproduce_baseline_locator.py
```

## Repairs And Developer Coverage

| Finding | Repair location | Focused developer regression coverage | Requirement |
| --- | --- | --- | --- |
| P1-1 ingest orphan | `backend/app/services/imports.py`, `backend/app/adapters/storage.py`, `backend/app/adapters/sqlite.py` | `test_ingest_failure_compensates_only_new_artifact`; injects repository failure and confirms a newly written artifact is removed, while a pre-existing deduplicated artifact remains. | `REQ-011`, `REQ-012` |
| P1-2 backup collision/retention inconsistency | `backend/app/services/transfers.py`, `backend/app/adapters/sqlite.py` | `test_backups_have_unique_archives_and_retention_never_advertises_missing`, `test_incomplete_pruning_record_is_reconciled_before_retention`, `test_retention_delete_failure_keeps_archive_and_success_record`; exercises same-second names, interrupted pruning recovery, and unlink failure. | `REQ-040`, `REQ-042` |
| P1-3 inaccurate locators | `backend/app/adapters/parsers.py`, `backend/app/services/documents.py` | `test_pdf_evidence_uses_each_page_ordinal`, `test_docx_evidence_uses_each_paragraph_ordinal`; uses synthetic two-page PDF and two-paragraph DOCX fixtures. | `REQ-020`, `REQ-021`, `REQ-023` |
| P1-4 reimport 500/orphan | `backend/app/services/transfers.py`, `backend/app/main.py`, `docs/api-contract.md` | `test_reimport_unique_card_conflict_returns_409_before_artifact_copy`; verifies `409`, conflict detail, and unchanged artifact set. | `REQ-041` |
| P1-5 shared artifact purge FK failure | `backend/app/adapters/sqlite.py` | `test_purge_shared_soft_deleted_artifact_waits_for_last_reference`; first purge succeeds and retains the artifact, final purge removes it. | `REQ-012`, `REQ-034` |
| P1-6 ignored parser circuit breakers | `backend/app/services/jobs.py`, `backend/app/adapters/sqlite.py` | `test_parser_circuit_breaker_and_configured_retry_are_observable`, `test_parser_timeout_circuit_breaker_terminates_without_waiting`; verifies persisted limits reach execution, a breaker yields failed/incomplete state, and a controlled hanging child is terminated without a long wait. | `REQ-032`, `REQ-033` |
| P1-7 URL credential export | `backend/app/services/external_cards.py`, `backend/app/adapters/sqlite.py`, `docs/api-contract.md` | `test_external_userinfo_rejected_and_legacy_export_redacts`; rejects new userinfo URLs with `422` and redacts legacy userinfo for API/export. Static scan found no backend URL-fetch execution path. | `REQ-030`, `REQ-031`, `REQ-041` |

The reimport repair preflights primary and natural uniqueness keys before artifact copying. It also compensates only artifacts copied by the failing reimport and only when no logical reference remains. Ingest uses the same newly-created-only and unreferenced-only compensation rule.

Backup archives now reserve a UUID-suffixed name with exclusive file creation. Success records are created only after archive verification, pruning is coordinated with record state, and incomplete pruning/discard records are reconciled on the next backup operation.

PDF and DOCX parsing now passes page/paragraph segments to evidence persistence. If a parser cannot provide native segments, the fallback locator explicitly uses `unknown` rather than a false first page or paragraph claim.

## Safe P2 Work Included

- `max_retry_attempts` is read while creating new jobs in `backend/app/adapters/sqlite.py`; coverage: `test_parser_circuit_breaker_and_configured_retry_are_observable` (`REQ-032`).
- Metadata revision snapshots and selectable relevance/updated/title search sorting are implemented in `backend/app/adapters/sqlite.py`, `backend/app/services/search.py`, and `backend/app/main.py`; coverage: `test_metadata_revisions_and_search_sort` (`REQ-024`).
- PostgreSQL/Compose documentation and configuration are explicitly fail-closed while the behavior-complete PostgreSQL repository remains unavailable. `backend/app/adapters/postgres.py` raises rather than silently using SQLite; `docs/dependency-installation.md` and `docs/operations-and-recovery.md` do not claim unexecuted Docker runtime support (`REQ-045`).

## Commands Actually Run

| Command | Outcome |
| --- | --- |
| `PYTHONPATH=E:/源知库/backend E:/源知库/.venv/Scripts/python.exe -m pytest E:/源知库/tests/unit/test_defect_fixes.py -q` | Passed after the final backup-cleanup compensation regression: `14 passed in 85.00s`. |
| `PYTHONPATH=E:/源知库/backend E:/源知库/.venv/Scripts/python.exe -m pytest E:/源知库/tests/unit/test_defect_fixes.py E:/源知库/tests/unit/test_api.py -q` | Final complete backend regression passed: `20 passed in 128.81s`. |
| `PYTHONPATH=E:/源知库/backend E:/源知库/.venv/Scripts/python.exe -m compileall -q E:/源知库/backend/app` | Passed. |
| OpenAPI creation smoke test using an isolated `tests/runtime/devfix-smoke-*` root | Passed: `openapi-smoke: passed`. |
| `git diff --check` | Passed; no whitespace errors. |
| Static backend outbound-client scan | No backend fetch call was found. `httpx` remains lockfile-only and loopback CORS origins are expected literals. |

## Blocked Or Not Claimed

- Frontend lint and build are blocked because `frontend/node_modules` is absent: both `npm run lint` and `npm run build` fail before compilation with `tsc` not found. Dependencies were not installed during this repair.
- Docker/Compose PostgreSQL runtime validation is blocked because `docker` is not installed. The project intentionally fails closed for a PostgreSQL URL; this report does not claim a functioning PostgreSQL deployment.
- Browser/UI acceptance and actual external network behavior were not run. No generic URL fetch is implemented or invoked by these tests.
- This report is developer self-test evidence only. Independent retest and acceptance remain outstanding.

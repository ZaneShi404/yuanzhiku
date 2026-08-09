# Independent Test Report - 20260728T225152Z

## Scope And Independence

- Tester role: independent testing agent.
- I did **not** read `E:\源知库\reports\development\`, developer chat, or developer conclusions.
- Evidence sources: current workspace, frozen documents under `docs/`, current Git state, source inspection, and commands/tests executed in this run.
- No production/source files were modified; no commits were made.
- Isolated run root: `E:\源知库\tests\runtime\test-20260728T225152Z`.
- All application data roots used by this run were descendants of that path. `E:\源知库\data` was not used or changed.

## Environment

- Windows 10.0.26200 x64, Git Bash.
- Python 3.13.0; project virtual environment `E:\源知库\.venv`.
- `pytest 8.3.5`; FastAPI, pypdf, and python-docx imports succeeded.
- Disk free space before tests: 569 GB on `E:`.
- Docker/Compose unavailable: `docker: command not found`.
- Browser automation was unavailable to this testing agent; GUI acceptance was not executed.
- Frozen documents do not define P0/P1/P2/P3 meanings. The labels below use standard operational impact: P1 is material data-integrity, evidence-integrity, recovery, or security failure; P2 is material functional/contract incompleteness; P3 is an architecture/maintainability defect.

## Findings

### P1 - Ingest Failure Leaves an Unreferenced Artifact

`REQ-011` and `REQ-012` require content-store/dedup failure cleanup. `T-ING-NEG-003` injected a repository failure immediately after `ImportService.paste()` wrote the artifact. It left `artifacts\\17\\17890bb8ba65da9d9dd434d2019133046d212cba2bbcdc0fcab3e9cd28f2a106` with no source or content version.

- Reproduction: run `tests/runtime/test-20260728T225152Z/focused_independent_test.py`, result `T-ING-NEG-003`.
- Cause: [imports.py](E:\源知库\backend\app\services\imports.py:27) writes the immutable artifact before [imports.py](E:\源知库\backend\app\services\imports.py:28) creates the logical records, without compensating deletion on failure. File-import flow has the same ordering at [imports.py](E:\源知库\backend\app\services\imports.py:55).
- Impact: failed imports consume persistent storage and preserve content that is not manageable through lifecycle APIs.

### P1 - Same-Second Backups Collide and Can Leave a Successful Record Without an Archive

`REQ-040` requires successful, verified daily backups. `T-BACK-NEG-001-R` froze time and issued two backup calls: both returned `201`, both used `backup-20300102T030405Z.zip`. After retention pruning, inspection found one `backups` record marked `succeeded` for that archive name and an empty backups directory.

- Reproduction: `tests/runtime/test-20260728T225152Z/focused_independent_test.py` result `T-BACK-NEG-001-R`; persisted-state command output in `tests/runtime/test-20260728T225152Z/backup-collision-integrity-output.txt` and `collision-integrity/state/knowledge.db`.
- Cause: [transfers.py](E:\源知库\backend\app\services\transfers.py:91) names archives with second precision, then [transfers.py](E:\源知库\backend\app\services\transfers.py:103) treats same-day records as stale and deletes the shared archive at [transfers.py](E:\源知库\backend\app\services\transfers.py:114).
- Impact: a reported successful backup can be unavailable for restore. This is reproducible for manual/API backups made in the same second.

### P1 - Evidence Locators Misidentify Multi-Page PDF and Multi-Paragraph DOCX Evidence

`REQ-020`, `REQ-021`, and `REQ-023` require locators that accurately identify evidence. `T-LOC-001` imported a synthetic two-page PDF and a synthetic DOCX with two paragraphs. In each case the only evidence excerpt/character range covered all extracted text, but the locator identified only PDF page `1` or DOCX paragraph ordinal `1`.

- Reproduction: `tests/runtime/test-20260728T225152Z/locator_test.py`; exact responses are in `locator-results.json`.
- PDF result: text was `first page evidence\n\nsecond page evidence`; locator was `{"type":"pdf_page_char_range","page":1,"char_range":[0,41]}`.
- DOCX result: text was `first paragraph evidence\n\nsecond paragraph evidence`; locator was `{"type":"docx_structure_char_range","structure":"body","paragraph_ordinal":1,"char_range":[0,51]}`.
- Cause: [documents.py](E:\源知库\backend\app\services\documents.py:23) through [documents.py](E:\源知库\backend\app\services\documents.py:25) manufacture a page/paragraph-1 locator for the whole extraction, and [documents.py](E:\源知库\backend\app\services\documents.py:41) through [documents.py](E:\源知库\backend\app\services\documents.py:49) persist exactly one evidence record.
- Impact: a citation can direct a user to the wrong page/paragraph while claiming an immutable evidence chain.

### P1 - Reimport Can Return 500 After Writing an Orphan Artifact

`REQ-041` requires reimport conflicts to be rejected and reported without overwrite. `T-BACK-NEG-003` imported an export that contained an external-card URL already present under a different ID. The request returned `500 Internal Server Error`, copied an artifact under the target data root, and left zero sources.

- Reproduction: `tests/runtime/test-20260728T225152Z/clean_focused_test.py`, `clean-focused-results.json`, key `reimport_unique_url_conflict`: `status: 500`, one `artifact_files_after_failure`, `source_count: 0`.
- Cause: [transfers.py](E:\源知库\backend\app\services\transfers.py:248) through [transfers.py](E:\源知库\backend\app\services\transfers.py:266) copy pending artifacts before logical-record insertion. The primary-key-only precheck does not cover `external_cards`' `UNIQUE(card_type, url)` constraint in [sqlite.py](E:\源知库\backend\app\adapters\sqlite.py:89) through [sqlite.py](E:\源知库\backend\app\adapters\sqlite.py:92); the later insert failure is uncaught at [transfers.py](E:\源知库\backend\app\services\transfers.py:269) through [transfers.py](E:\源知库\backend\app\services\transfers.py:276).
- Impact: reimport violates its conflict-reporting contract and leaves unreferenced content after a failed transfer.

### P1 - Purging One of Two Soft-Deleted Sources Sharing an Artifact Crashes

`REQ-012` and `REQ-034` require shared-artifact lifecycle handling. `T-LIFE-NEG-001` created two sources sharing one artifact, parsed both, soft-deleted both, then purged one. The purge raised `sqlite3.IntegrityError: FOREIGN KEY constraint failed` instead of returning the lifecycle response.

- Reproduction: `tests/runtime/test-20260728T225152Z/targeted_repros.py`; stack trace at `targeted-repros-output.txt:159-162` identifies [sqlite.py](E:\源知库\backend\app\adapters\sqlite.py:521).
- Cause: [sqlite.py](E:\源知库\backend\app\adapters\sqlite.py:515) counts only active sources, then tries to delete the artifact although the other soft-deleted source still has a `content_versions.artifact_sha256` foreign-key reference.
- Impact: a valid permanent-delete workflow fails when shared artifacts are referenced only by recoverable soft-deleted sources; database transaction rolls back and the user gets an internal server error.

### P1 - Parser Safety Circuit Breakers Are Persisted but Not Enforced

`REQ-033` requires configurable parser circuit breakers for timeout, disk, memory, and no-progress conditions, defaulting to 24 hours. The settings API persists `parser_timeout_seconds` and `parser_no_progress_seconds`, but no execution path reads them after persistence.

- Static evidence: definitions/defaults in [models.py](E:\源知库\backend\app\domain\models.py:129) through [models.py](E:\源知库\backend\app\domain\models.py:133) and [sqlite.py](E:\源知库\backend\app\adapters\sqlite.py:133) through [sqlite.py](E:\源知库\backend\app\adapters\sqlite.py:142); `rg` found no consumer outside these declarations/defaults.
- Execution evidence: parser execution synchronously calls `parse_local(...)` at [jobs.py](E:\源知库\backend\app\services\jobs.py:58), with no timeout/no-progress/memory watchdog in [jobs.py](E:\源知库\backend\app\services\jobs.py:53) through [jobs.py](E:\源知库\backend\app\services\jobs.py:76).
- Impact: a malicious or pathological local document parser can block the sole worker indefinitely, contrary to the stated safety control. A deliberately hanging parser was not executed because that would leave an uncontrolled test worker.

### P1 - Export Includes Credentials Embedded in a Generic External URL

`REQ-041` requires exports to exclude credentials. `T-SEC-EXT-001` created the valid generic URL `https://user:synthetic-secret@example.test/path`, exported the library, and found `synthetic-secret` in `records.json`.

- Reproduction: `tests/runtime/test-20260728T225152Z/clean_focused_test.py`, `clean-focused-results.json`, key `credential_url_export`: `credential_present_in_records: true`.
- Cause: generic URL validation accepts any HTTP(S) netloc at [external_cards.py](E:\源知库\backend\app\services\external_cards.py:17) through [external_cards.py](E:\源知库\backend\app\services\external_cards.py:28); export serializes external card records wholesale at [transfers.py](E:\源知库\backend\app\services\transfers.py:71) through [transfers.py](E:\源知库\backend\app\services\transfers.py:74).
- Impact: a portable export can disclose URL userinfo credentials despite the explicit exclusion requirement.

### P2 - Configured `max_retry_attempts` Has No Effect on New Jobs

`REQ-032` requires finite configurable retries. `T-JOB-CONFIG-001` PUT `max_retry_attempts: 0`, then created a parse job with its artifact removed. The job still had `max_attempts: 2` and transitioned to `retry_wait` after its first failure.

- Reproduction: `tests/runtime/test-20260728T225152Z/clean_focused_test.py`, `clean-focused-results.json`, key `max_retry_setting_effect`.
- Cause: [sqlite.py](E:\源知库\backend\app\adapters\sqlite.py:368) through [sqlite.py](E:\源知库\backend\app\adapters\sqlite.py:377) hard-code job `max_attempts` to `2`; [jobs.py](E:\源知库\backend\app\services\jobs.py:47) uses that field and never reads the setting.
- Impact: UI/API configuration gives a false assurance and cannot tune retry behavior.

### P2 - PostgreSQL/Compose Runtime Is Not Implemented

`REQ-045` requires a production PostgreSQL adapter/migrations and Compose deployment. Static review found that `ApplicationServices` always constructs `SqliteRepository` at [main.py](E:\源知库\backend\app\main.py:53), all domain services are typed to/import the SQLite adapter, and [postgres.py](E:\源知库\backend\app\adapters\postgres.py:12) only lists migration files. The PostgreSQL migration is materially incomplete relative to SQLite (for example, no `search_chunks`, `citations`, `knowledge`, `knowledge_evidence`, `external_cards`, `topics`, `topic_sources`, `job_attempts`, `audit_events`, or `backups`).

- Compose static review: [docker-compose.yml](E:\源知库\docker-compose.yml:44) defines PostgreSQL and loopback mappings, but API/worker are not configured to use it.
- Impact: Compose cannot meet the required PostgreSQL persistence/constraint behavior.
- Container validation is blocked, not passed: `docker --version` and `docker compose version` returned `docker: command not found`.

### P2 - Metadata Revision History Is Absent

The testing scope requires metadata revisions. The SQLite schema contains mutable source fields but no metadata-revision table, and [sqlite.py](E:\源知库\backend\app\adapters\sqlite.py:222) through [sqlite.py](E:\源知库\backend\app\adapters\sqlite.py:230) overwrites them in place. `T-SEARCH-001` confirmed an update succeeds but provides no historical revision record.

- Impact: source metadata provenance/history cannot be reviewed or restored.

### P2 - Search Does Not Expose Required Sort Selection

`REQ-024` requires sorting by relevance, import/update, and title. The documented `/api/v1/search` endpoint accepts filters but no sort parameter at [main.py](E:\源知库\backend\app\main.py:317) through [main.py](E:\源知库\backend\app\main.py:324). [search.py](E:\源知库\backend\app\services\search.py:52) supplies one fixed relevance/update/title ordering.

- Impact: callers cannot choose the required import/update or title sort modes.

### P3 - Services Depend Directly on Concrete Adapters Rather Than Ports

`REQ-004` requires storage, parsing, and database dependencies behind ports/adapters. Static dependency review found direct imports of `SqliteRepository`, `ArtifactStore`, and `parse_local` in [imports.py](E:\源知库\backend\app\services\imports.py:9) through [imports.py](E:\源知库\backend\app\services\imports.py:10), [jobs.py](E:\源知库\backend\app\services\jobs.py:8) through [jobs.py](E:\源知库\backend\app\services\jobs.py:10), and other services. The only declared storage port, [storage.py](E:\源知库\backend\app\ports\storage.py:8), is not the repository/parser abstraction required by these services.

- Impact: the declared PostgreSQL/local parsing boundaries cannot be substituted without changing business services, consistent with the PostgreSQL gap above.

## Executed Coverage And Results

| Test ID / command | Requirements | Result |
| --- | --- | --- |
| `T-API-001` | REQ-001, REQ-043 | PASS. `/api/v1/health`, capabilities, and 38 OpenAPI paths returned from isolated TestClient. |
| `T-ING-001`, `T-ING-002` | REQ-011, REQ-012, REQ-014, REQ-020..023 | PASS for normal Markdown paste: SHA-256 dedup across two sources, no original path in source response, complete native representation, evidence/citation chain. |
| `T-ING-NEG-001-R`, `T-ING-NEG-002-R` | REQ-010 | PASS. Exactly 10 MiB paste accepted; 10 MiB + 1 byte rejected `422`. Direct 2 GiB+1 and mocked low-space preflight rejected. |
| `T-PARSE-001` | REQ-010, REQ-014, REQ-021 | PASS for MD/TXT/DOCX native/local parsing and expected broken-PDF failed/incomplete behavior. `T-LOC-001` found the separate locator P1 above. |
| `T-KNOW-001`, `T-DOC-001` | REQ-022 | PASS. Unsupported fact publication was rejected `422`; evidence-backed fact and unverified items published correctly; manual representation retained parent and citation showed `human_revised: true`. |
| `T-EXT-001` and static outbound-client review | REQ-030, REQ-031 | PASS for external URL validation and literal HTTPS Douyin validation. Static review found no backend outbound HTTP client; this is static evidence, not packet capture. |
| `T-JOB-001-R`, `T-JOB-NEG-001-R` | REQ-032 | PASS after draining the low-priority startup backup: queued cancel -> cancelled, retry -> queued, rerun -> succeeded; missing artifact transitioned retry_wait -> failed on second attempt. |
| `T-LIFE-001` | REQ-012, REQ-034, REQ-042 | PASS for purge while another active source references the shared artifact. The all-soft-deleted case is the separate P1 lifecycle failure. |
| `T-BACK-001`, `T-BACK-NEG-002` | REQ-040..042 | PASS for ordinary backup, restore only to new root, confirmation-required export, normal idempotent reimport, and tampered-archive rejection. Same-second and uniqueness-conflict paths fail as reported. |
| `T-RUNTIME-001` and HTTP command | REQ-002, REQ-003 | PASS. Uvicorn listener was `127.0.0.1:8781`; second process using the same isolated root failed with `RuntimeError: 该数据根已有运行中的源知库实例`; port choice persisted. |
| `T-SEC-001` | REQ-003, REQ-042 | PASS. Operational log contained stable route/status but not request content or data-root path. |
| `PYTHONPATH=... python -m pytest tests/unit/test_api.py -q` | Existing automated tests | PASS: `6 passed in 37.90s`. |

## Skipped Or Blocked

- GUI smoke/acceptance (`T-UI-001`, REQ-044): not executed because browser automation is unavailable to this agent. No UI acceptance conclusion is implied.
- Docker Compose/PostgreSQL runtime and migration execution (`T-COMP-001`, REQ-045): blocked because Docker is absent. Static Compose review only.
- Frontend TypeScript build/lint: blocked because `frontend/node_modules` is absent. `npx tsc` reported that the TypeScript compiler was not installed; dependencies were not installed because this independent test run must avoid workspace modifications outside its runtime/report outputs.
- OCR/scanned PDF behavior: no copyright-safe scanned fixture generator/library was installed. Broken PDF behavior was tested; actual awaiting-OCR flow remains unexecuted.
- No live external URL/Douyin request was made, by design; only validation/static no-client review was performed.

## Residual Risk

- Automatic startup backup behavior was exercised, but 30 distinct-day retention was not time-traveled across 30 dates.
- PDF isolated preview, disabled embedded links, and browser navigation protections remain GUI-unverified.
- Actual PostgreSQL constraints, API/worker concurrency, Redis readiness, and Compose loopback publication remain unexecuted because the container runtime is absent.
- No long-running malicious parser was executed; circuit-breaker absence is established by static execution-path review, not a deliberate hang.

## Decision

This report makes **no final user-acceptance decision**. It records independent test evidence, defects, coverage, and blocked validation for handoff to a separate development/retest cycle.

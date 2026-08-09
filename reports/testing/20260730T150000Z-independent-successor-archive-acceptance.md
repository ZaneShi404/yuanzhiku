# Independent Successor Archive Acceptance

## Decision

**ACCEPT** for archive-local acceptance only.

Candidate run ID: `20260730T145000Z-replay-contract-remediated`.

Manifest SHA-256: `1b03170cec6e9db53df1c8f1ad1a8966becc1f110bb45b61fa8edc3cca22cd8d`.

No archive-local defect was found. The candidate remains **V1 Candidate / BLOCKED**. Physical PostgreSQL migration/restore, Docker Compose topology, and Edge/Chrome black-box gates remain blocked and were not exercised or passed by this acceptance.

## Scope Restrictions

- Candidate directory and matching ZIP were read-only throughout.
- The ordinary data area was not read, enumerated, or modified.
- No dependencies were installed and no configuration was changed.
- Archive-contained testing ran only from a fresh isolated copy, using the existing project virtual environment with bytecode generation disabled and pytest cache disabled.

## Results

- Copied archive regression: 15 passed, 0 failed, 0 skipped, 1 expected duplicate-member fixture warning.
- Candidate-contained verifier: 4 successful invocations, covering the directory and ZIP before and after replay; each verified 112 manifest entries.
- Directory/ZIP equality: 114 physical members in each form, equal by member path, size, and content hash; this includes 112 manifest entries and two manifest files.
- Prohibited-content inspection: 0 prohibited database, nested ZIP, bytecode, cache, or disallowed runtime members.
- T1 reconciliation: 3 of 3 entries matched on source run ID, source path, and purpose across allowlist, source inventory, and evidence register.
- Register/ledger closure: passed, including `DEF-ARCH-010`.
- Predecessor reference: passed for `20260730T135500Z-archive-contract-remediated` with manifest SHA-256 `55e6cf2ebb9bf743e9830b64bca5402df5d4246478d98af0e36f4baf75d4424e`.
- Integrity stability: manifest hash was unchanged before and after replay.

## ARCH-REV Dispositions

| Review ID | Disposition |
| --- | --- |
| ARCH-REV-004 | Resolved: archive-contained regression replay passed. |
| ARCH-REV-005 | Resolved: all T1 provenance records reconciled. |
| ARCH-REV-006 | Resolved: archive-contained regression replay passed. |
| ARCH-REV-007 | Resolved: archive-contained regression replay passed. |
| ARCH-REV-008 | Resolved: archive-contained regression replay passed. |
| ARCH-REV-009 | Resolved: predecessor register and generated predecessor reference reconciled. |
| ARCH-REV-010 | Resolved: policy and README specify the existing virtual-environment replay contract, and the independent replay passed. |

# Independent Normalized Archive Acceptance Rejection

## Decision

**REJECT** for archive-local acceptance and version-summary correctness.

Candidate run ID: `20260730T231357Z-normalized-reports`.

Manifest SHA-256: `b5bcdbd6cfad51dc9babd428571bc751f706f382df3cd9eb3ccb494ac03f9655`.

## Confirmed Checks

- The matching ZIP passed the candidate-contained verifier before and after the isolated replay.
- The isolated archive regression replay completed with 18 passed tests and one expected duplicate-member fixture warning.
- The sealed manifest hash did not change during replay.
- The ordinary data area was not accessed; no dependencies were installed.

## Findings

| ID | Severity | Finding | Required successor action |
| --- | --- | --- | --- |
| DEF-ARCH-011 | P1 | The candidate directory contained an unmanifested bytecode member while the matching ZIP did not, so the two published forms were not equivalent. | Seal the directory before publication so ordinary verification or replay cannot create cache members; reject unsealed v2 directory outputs during acceptance. |
| DEF-ARCH-012 | P2 | The `v1.0.0` version summary omitted this rejected candidate from its declared complete candidate chain. | Bind the summary chain to a frozen snapshot register and require every registered candidate through the current worktree state to appear exactly once. |

## Scope

This is not a product release decision. Physical PostgreSQL migration/restore, Docker Compose topology, and Edge/Chrome black-box gates remain `blocked`.

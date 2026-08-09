# Independent ACL Candidate Acceptance Rejection

## Decision

**REJECTED** for `archive_local` scope.

The published directory does not satisfy its declared member set and is not equivalent to the matching ZIP. The archive immutability gate is blocked for `REQ-046`.

## Review Basis

- The candidate directory includes one member not declared by its manifest.
- The matching ZIP verifies and does not include that extra member.
- The directory ACL retains owner-level full control, and member creation occurred after publication.

## Scope

This rejection concerns archive-local acceptance only.

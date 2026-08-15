# Instance Lock File Growth Repair - 20260815T031800Z

## Scope And Status

This is a developer repair and self-test report only. It is not an independent test report and makes no independent-test or user-acceptance claim.

The defect was observed on the live data root during Windows launcher verification: `data/state/instance.lock` grew by one byte on every `InstanceLock.acquire()` call (11 bytes after 11 acquisitions).

## Defect

`InstanceLock.acquire` in `backend/app/core/config.py` opened the lock file in append mode (`"a+b"`), then ran `self.handle.seek(0)` followed by `if self.handle.tell() == 0:`. `tell()` immediately after `seek(0)` is always `0`, so the "file is empty" guard was always true and `b"0"` was written on every acquisition. Because append mode forces all writes to end-of-file regardless of the seek position, each acquisition appended one byte and the lock file grew unboundedly.

The defect was harmless to correctness (nothing reads the lock-file content; the byte range locked by `msvcrt.locking` was unaffected) but caused unbounded file growth.

## Repair

`backend/app/core/config.py`, `InstanceLock.acquire` (Windows branch only):

- Measure file size with `seek(0, os.SEEK_END)` + `tell()` so the guard detects an actually-empty file.
- Write `b"0"` only when the file is empty.
- Always `seek(0)` before `msvcrt.locking(...)` so the locked byte range stays at offset 0.

The non-Windows `fcntl.flock` branch does not position-dependent write and was not changed. `release()` already seeks to 0 before unlocking and was not changed.

## Developer Regression Coverage

`tests/unit/test_defect_fixes.py::test_instance_lock_acquisition_never_grows_lock_file`: acquires and releases an `InstanceLock` on a fresh path five times, asserting the lock file stays exactly one byte during every acquisition and its final content is `b"0"`. The test fails against the pre-repair implementation (file size grows to 5).

## Commands Actually Run

| Command | Outcome |
| --- | --- |
| `PYTHONPATH=backend ./.venv/Scripts/python.exe -m pytest tests/unit/test_defect_fixes.py::test_instance_lock_acquisition_never_grows_lock_file -q` | Passed: `1 passed in 0.89s`. |
| `PYTHONPATH=backend ./.venv/Scripts/python.exe -m pytest tests/unit/test_defect_fixes.py -q` | Passed: `21 passed in 109.54s`. |

## Notes

- The pre-existing `data/state/instance.lock` of the running instance retains its accumulated bytes; with the repair it no longer grows. The file was not modified while held by the running instance.
- Found during verification of the desktop-shortcut entry point (`源知库.lnk`, `启动源知库.cmd`, `scripts/install-desktop-shortcut.ps1`); unrelated to that feature's behavior.

"""Minimal operational event log: no request bodies, content, paths, tokens or secrets."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path


class OperationalLog:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)

    def write(self, event: str, result: str) -> None:
        stamp = datetime.now(UTC)
        target = self.directory / f"{stamp.date().isoformat()}.jsonl"
        record = {"timestamp": stamp.isoformat(), "event": event, "result": result}
        with target.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, separators=(",", ":")) + "\n")

    def prune(self, days: int = 30) -> None:
        threshold = datetime.now(UTC).date() - timedelta(days=days)
        for path in self.directory.glob("*.jsonl"):
            try:
                if datetime.fromisoformat(path.stem).date() < threshold:
                    path.unlink()
            except ValueError:
                continue

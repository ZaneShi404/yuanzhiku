"""Dedicated Docker worker entry point; no HTTP listener and no instance lock."""

from __future__ import annotations

import os
import time

from app.main import ApplicationServices
from app.core.config import data_paths


def main() -> None:
    services = ApplicationServices(data_paths())
    while True:
        services.jobs.run_once()
        time.sleep(1)


if __name__ == "__main__":
    main()

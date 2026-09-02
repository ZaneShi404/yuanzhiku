"""Local access boundary (hardening plan Task 2)."""

from app.security.local_access import (
    LocalAccessSettings,
    install_local_access_middleware,
)

__all__ = ["LocalAccessSettings", "install_local_access_middleware"]

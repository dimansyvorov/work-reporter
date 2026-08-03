from __future__ import annotations


class CollectError(RuntimeError):
    """Recoverable collection/API failure (safe to raise from worker threads)."""

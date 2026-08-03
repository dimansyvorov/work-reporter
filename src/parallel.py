from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")
R = TypeVar("R")
C = TypeVar("C")


def map_parallel(
    items: list[T],
    fn: Callable[[T], R],
    *,
    max_workers: int = 6,
    on_progress: Callable[[int, int], None] | None = None,
    progress_every: int = 5,
) -> list[R]:
    """
    Run fn over items with a bounded thread pool.
    Preserves input order in the returned list.
    """
    if not items:
        return []
    if len(items) == 1:
        if on_progress:
            on_progress(1, 1)
        return [fn(items[0])]

    workers = max(1, min(max_workers, len(items)))
    results: list[R | None] = [None] * len(items)
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fn, item): idx for idx, item in enumerate(items)}
        for fut in as_completed(futures):
            idx = futures[fut]
            results[idx] = fut.result()
            done += 1
            if on_progress and (
                done == 1 or done == len(items) or done % max(1, progress_every) == 0
            ):
                on_progress(done, len(items))
    return results  # type: ignore[return-value]


def map_parallel_with_client(
    items: list[T],
    client_factory: Callable[[], C],
    fn: Callable[[C, T], R],
    *,
    max_workers: int = 6,
    on_progress: Callable[[int, int], None] | None = None,
    progress_every: int = 5,
) -> list[R]:
    """
    Like map_parallel, but each worker thread gets its own client instance
    (requests.Session is not thread-safe).
    """
    local = threading.local()

    def get_client() -> C:
        client = getattr(local, "client", None)
        if client is None:
            client = client_factory()
            local.client = client
        return client

    def wrapped(item: T) -> R:
        return fn(get_client(), item)

    return map_parallel(
        items,
        wrapped,
        max_workers=max_workers,
        on_progress=on_progress,
        progress_every=progress_every,
    )

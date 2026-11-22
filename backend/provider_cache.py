import asyncio
from collections.abc import Awaitable, Callable
from functools import partial
from time import monotonic

from fastapi import HTTPException


CACHE_TTL_SECONDS = 600
OUTBOUND_LIMIT = 3
SLOT_WAIT_SECONDS = 1


class ProviderCache:
    def __init__(self):
        self._entries: dict[str, tuple[float, dict]] = {}
        self._in_flight: dict[str, asyncio.Task[dict]] = {}
        self._slots = asyncio.Semaphore(OUTBOUND_LIMIT)

    async def get_or_load(self, key: str, load: Callable[[], Awaitable[dict]]) -> dict:
        entry = self._entries.get(key)
        if entry is not None and monotonic() < entry[0]:
            return entry[1]

        task = self._in_flight.get(key)
        if task is None:
            self._entries.pop(key, None)
            task = asyncio.create_task(self._load_and_cache(key, load))
            self._in_flight[key] = task
            task.add_done_callback(partial(self._finish_load, key))

        # A cancelled client must leave the shared fetch running for other callers.
        return await asyncio.shield(task)

    async def _load_and_cache(self, key: str, load: Callable[[], Awaitable[dict]]) -> dict:
        try:
            async with asyncio.timeout(SLOT_WAIT_SECONDS):
                await self._slots.acquire()
        except TimeoutError:
            raise HTTPException(
                status_code=503,
                detail="Air quality service is busy. Please try again.",
                headers={"Retry-After": str(SLOT_WAIT_SECONDS)},
            ) from None

        try:
            result = await load()
        finally:
            self._slots.release()

        self._entries[key] = (monotonic() + CACHE_TTL_SECONDS, result)
        return result

    def _finish_load(self, key: str, task: asyncio.Task[dict]) -> None:
        self._in_flight.pop(key)
        # Retrieve errors even when every caller has cancelled its wait.
        if not task.cancelled():
            task.exception()

    async def close(self) -> None:
        tasks = tuple(self._in_flight.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

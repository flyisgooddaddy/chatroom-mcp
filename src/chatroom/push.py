"""Push notifications: HTTP callback to agent when @mentioned."""
from __future__ import annotations
import asyncio
from typing import Any
import httpx


async def notify(callback_url: str, payload: dict[str, Any],
                 timeout: float = 5.0, retries: int = 1) -> bool:
    """POST payload to callback_url. Returns True on 2xx, False otherwise.

    Retries up to `retries` times on network errors.
    """
    for attempt in range(retries + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
                r = await client.post(callback_url, json=payload)
            return 200 <= r.status_code < 300
        except (httpx.HTTPError, asyncio.TimeoutError):
            if attempt >= retries:
                return False
            await asyncio.sleep(0.2 * (attempt + 1))
    return False


def notify_fire_and_forget(callback_url: str, payload: dict[str, Any]) -> asyncio.Task:
    """Schedule a notify() and return the task (caller can ignore)."""
    return asyncio.create_task(notify(callback_url, payload))

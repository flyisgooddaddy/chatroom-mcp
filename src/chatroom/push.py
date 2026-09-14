"""Push notifications (P1 stub). Real impl in P4."""
from __future__ import annotations
import asyncio
from typing import Any
import httpx


async def notify(callback_url: str, payload: dict[str, Any], timeout: float = 5.0) -> bool:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(callback_url, json=payload)
        return 200 <= r.status_code < 300
    except (httpx.HTTPError, asyncio.TimeoutError):
        return False

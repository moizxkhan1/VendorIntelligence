"""Playwright browser singleton.

Lazy-initialized on first call to `get_browser()`. Reused across fetcher calls
within an app process so we don't pay launch cost (~1s) per page. The FastAPI
lifespan handler calls `shutdown_browser()` to clean up.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Browser, Playwright

_lock = asyncio.Lock()
_browser: "Browser | None" = None
_playwright: "Playwright | None" = None


async def get_browser() -> "Browser":
    global _browser, _playwright
    async with _lock:
        if _browser is None:
            from playwright.async_api import async_playwright

            _playwright = await async_playwright().start()
            _browser = await _playwright.chromium.launch(headless=True)
        return _browser


async def shutdown_browser() -> None:
    global _browser, _playwright
    async with _lock:
        if _browser is not None:
            await _browser.close()
            _browser = None
        if _playwright is not None:
            await _playwright.stop()
            _playwright = None

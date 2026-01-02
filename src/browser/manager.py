from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from contextlib import asynccontextmanager
from typing import AsyncGenerator
import logging

from ..core.config import settings

logger = logging.getLogger(__name__)


class BrowserManager:
    """Manages Playwright browser instances for web automation."""

    def __init__(
        self,
        headless: bool | None = None,
        timeout: int | None = None,
    ):
        self.headless = headless if headless is not None else settings.browser_headless
        self.timeout = timeout or settings.browser_timeout
        self._playwright = None
        self._browser: Browser | None = None

    async def start(self) -> None:
        """Start the browser instance."""
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self.headless,
        )
        logger.info(f"Browser started (headless={self.headless})")

    async def stop(self) -> None:
        """Stop the browser instance."""
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        logger.info("Browser stopped")

    @property
    def browser(self) -> Browser:
        if not self._browser:
            raise RuntimeError("Browser not started. Call start() first.")
        return self._browser

    @asynccontextmanager
    async def new_context(
        self,
        storage_state: dict | None = None,
        **kwargs,
    ) -> AsyncGenerator[BrowserContext, None]:
        """Create a new browser context with optional saved state."""
        context = await self.browser.new_context(
            storage_state=storage_state,
            viewport={"width": 1920, "height": 1080},
            **kwargs,
        )
        context.set_default_timeout(self.timeout)
        try:
            yield context
        finally:
            await context.close()

    @asynccontextmanager
    async def new_page(
        self,
        context: BrowserContext | None = None,
        storage_state: dict | None = None,
    ) -> AsyncGenerator[Page, None]:
        """Create a new page, optionally in an existing context."""
        if context:
            page = await context.new_page()
            try:
                yield page
            finally:
                await page.close()
        else:
            async with self.new_context(storage_state=storage_state) as ctx:
                page = await ctx.new_page()
                try:
                    yield page
                finally:
                    await page.close()

    async def save_storage_state(self, context: BrowserContext) -> dict:
        """Save cookies and local storage for session persistence."""
        return await context.storage_state()


# Singleton instance
_browser_manager: BrowserManager | None = None


async def get_browser_manager() -> BrowserManager:
    """Get or create browser manager singleton."""
    global _browser_manager
    if _browser_manager is None:
        _browser_manager = BrowserManager()
        await _browser_manager.start()
    return _browser_manager

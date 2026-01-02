from playwright.async_api import Page
from typing import Any
import logging

logger = logging.getLogger(__name__)


async def navigate(page: Page, url: str, wait_until: str = "domcontentloaded") -> None:
    """Navigate to a URL."""
    logger.debug(f"Navigating to: {url}")
    await page.goto(url, wait_until=wait_until)


async def click(page: Page, selector: str, wait_after: int = 0) -> None:
    """Click an element."""
    logger.debug(f"Clicking: {selector}")
    await page.click(selector)
    if wait_after:
        await page.wait_for_timeout(wait_after)


async def fill(page: Page, selector: str, value: str) -> None:
    """Fill a form field."""
    logger.debug(f"Filling: {selector}")
    await page.fill(selector, value)


async def get_text(page: Page, selector: str) -> str:
    """Get text content of an element."""
    element = await page.query_selector(selector)
    if element:
        return await element.text_content() or ""
    return ""


async def get_all_text(page: Page, selector: str) -> list[str]:
    """Get text content of all matching elements."""
    elements = await page.query_selector_all(selector)
    return [await el.text_content() or "" for el in elements]


async def get_attribute(page: Page, selector: str, attribute: str) -> str | None:
    """Get an attribute from an element."""
    element = await page.query_selector(selector)
    if element:
        return await element.get_attribute(attribute)
    return None


async def wait_for_selector(
    page: Page, selector: str, state: str = "visible", timeout: int = 30000
) -> None:
    """Wait for an element to appear."""
    await page.wait_for_selector(selector, state=state, timeout=timeout)


async def extract_table_data(page: Page, table_selector: str) -> list[dict[str, Any]]:
    """Extract data from an HTML table."""
    rows = await page.query_selector_all(f"{table_selector} tr")
    if not rows:
        return []

    # Get headers
    headers_el = await rows[0].query_selector_all("th, td")
    headers = [await h.text_content() or f"col_{i}" for i, h in enumerate(headers_el)]

    # Get data rows
    data = []
    for row in rows[1:]:
        cells = await row.query_selector_all("td")
        row_data = {
            headers[i]: await cell.text_content() or ""
            for i, cell in enumerate(cells)
            if i < len(headers)
        }
        data.append(row_data)

    return data


async def scroll_to_bottom(page: Page, step: int = 500, delay: int = 100) -> None:
    """Scroll to the bottom of the page."""
    previous_height = 0
    while True:
        current_height = await page.evaluate("document.body.scrollHeight")
        if current_height == previous_height:
            break
        previous_height = current_height
        await page.evaluate(f"window.scrollBy(0, {step})")
        await page.wait_for_timeout(delay)


async def take_screenshot(page: Page, path: str) -> None:
    """Take a screenshot of the current page."""
    await page.screenshot(path=path, full_page=True)


async def get_page_content(page: Page) -> str:
    """Get the full HTML content of the page."""
    return await page.content()


async def login_with_credentials(
    page: Page,
    login_url: str,
    username_selector: str,
    password_selector: str,
    submit_selector: str,
    username: str,
    password: str,
    success_indicator: str | None = None,
) -> bool:
    """Perform login with username and password."""
    try:
        await navigate(page, login_url)
        await fill(page, username_selector, username)
        await fill(page, password_selector, password)
        await click(page, submit_selector)

        if success_indicator:
            await wait_for_selector(page, success_indicator, timeout=10000)

        logger.info("Login successful")
        return True
    except Exception as e:
        logger.error(f"Login failed: {e}")
        return False

"""Zero-LLM Fast Execution Engine module for Nexus-Scraper MCP.

Executes sub-second high-speed scraping using Playwright and pre-cached CSS Selectors,
enforcing required field integrity and regular expression validation.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from playwright.async_api import Browser, BrowserContext, Page, async_playwright


class FastEngineError(Exception):
    """Base exception for fast execution engine errors."""


class SelectorMismatchError(FastEngineError):
    """Raised when one or more required fields fail extraction or validation."""

    def __init__(self, message: str, broken_fields: List[str], target_url: str) -> None:
        super().__init__(message)
        self.broken_fields = broken_fields
        self.target_url = target_url


class PageLoadError(FastEngineError):
    """Raised when target page fails to load within the timeout period."""


class PlaywrightRunner:
    """Headless Playwright runner optimized for low-latency zero-LLM scraping."""

    DEFAULT_UA = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )

    def __init__(
        self,
        headless: bool = True,
        timeout_ms: int = 15000,
        user_agent: Optional[str] = None,
    ) -> None:
        """Initialize Playwright runner configuration."""
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.user_agent = user_agent or self.DEFAULT_UA
        self._pw = None
        self._browser: Optional[Browser] = None

    async def start(self) -> None:
        """Initialize Playwright and launch Chromium browser instance."""
        if not self._browser:
            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(
                headless=self.headless,
                args=["--disable-blink-features=AutomationControlled"],
            )

    async def close(self) -> None:
        """Close browser instance and terminate Playwright process."""
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._pw:
            await self._pw.stop()
            self._pw = None

    async def __aenter__(self) -> PlaywrightRunner:
        """Async context manager entry."""
        await self.start()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.close()

    async def _extract_value(
        self, page: Page, selector: str, extract_type: str, attr_name: Optional[str]
    ) -> Optional[str]:
        """Extract raw text, attribute, or HTML from a matched element."""
        try:
            element = await page.query_selector(selector)
            if not element:
                return None

            if extract_type == "attribute" and attr_name:
                val = await element.get_attribute(attr_name)
                return val.strip() if val else None
            elif extract_type == "html":
                val = await element.inner_html()
                return val.strip() if val else None
            else:
                val = await element.inner_text()
                return val.strip() if val else None
        except Exception:
            return None

    async def _extract_field(
        self, page: Page, field: Dict[str, Any]
    ) -> Tuple[Optional[str], bool]:
        """Extract a single field value with fallback cascade and regex check."""
        field_name = field["field_name"]
        primary_sel = field["css_selector"]
        fallbacks: List[str] = field.get("fallback_selectors", [])
        extract_type = field.get("extract_type", "text")
        attr_name = field.get("attribute_name")
        val_regex_str = field.get("validation_regex")

        val_regex = re.compile(val_regex_str) if val_regex_str else None

        candidates = [primary_sel] + [s for s in fallbacks if s]

        for sel in candidates:
            raw_val = await self._extract_value(page, sel, extract_type, attr_name)
            if raw_val is not None and raw_val != "":
                if val_regex and not val_regex.search(raw_val):
                    continue
                return raw_val, True

        return None, False

    async def execute_rule(
        self, target_url: str, rule: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute extraction using pre-cached CSS selectors on target URL.

        Args:
            target_url: URL of the target webpage.
            rule: Scraping rule dictionary containing wait_selector and fields.

        Returns:
            Dictionary containing extracted data and telemetry metrics.

        Raises:
            PageLoadError: If navigation fails or times out.
            SelectorMismatchError: If any required field cannot be extracted.
        """
        if not self._browser:
            await self.start()

        start_time = time.perf_counter()
        extracted_data: Dict[str, Any] = {}
        broken_fields: List[str] = []

        context: BrowserContext = await self._browser.new_context(
            user_agent=self.user_agent,
            viewport={"width": 1280, "height": 800},
        )
        page: Page = await context.new_page()

        try:
            strategy = rule.get("page_load_strategy", "domcontentloaded")
            await page.goto(
                target_url,
                wait_until=strategy,
                timeout=self.timeout_ms,
            )

            wait_sel = rule.get("wait_selector")
            if wait_sel:
                try:
                    await page.wait_for_selector(wait_sel, timeout=5000)
                except Exception:
                    pass  # Non-fatal; continue to evaluate field selectors

            fields = rule.get("fields", [])
            for field in fields:
                val, success = await self._extract_field(page, field)
                is_required = field.get("is_required", True)

                if success and val is not None:
                    extracted_data[field["field_name"]] = val
                else:
                    extracted_data[field["field_name"]] = None
                    if is_required:
                        broken_fields.append(field["field_name"])

            if broken_fields:
                raise SelectorMismatchError(
                    message=f"Required fields extraction failed: {broken_fields}",
                    broken_fields=broken_fields,
                    target_url=target_url,
                )

            duration_ms = int((time.perf_counter() - start_time) * 1000)
            return {
                "success": True,
                "data": extracted_data,
                "duration_ms": duration_ms,
                "token_cost": 0,
                "rule_id": rule.get("id"),
                "version": rule.get("version", 1),
            }

        except SelectorMismatchError:
            raise
        except Exception as exc:
            raise PageLoadError(f"Failed to navigate {target_url}: {exc}") from exc
        finally:
            await page.close()
            await context.close()

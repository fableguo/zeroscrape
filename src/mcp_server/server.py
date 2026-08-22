"""FastMCP Server module for Nexus-Scraper MCP (Agent-Driven Architecture).

Exposes MCP tools for OpenClaw/Hermes AI agents with Zero-LLM fast path routing,
compact DOM compression, selector syntax validation, and memory inspection.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional
from bs4 import BeautifulSoup
from mcp.server.fastmcp import FastMCP

from ..engine_explore.dom_cleaner import DOMCleaner
from ..engine_fast.playwright_runner import (
    PageLoadError,
    PlaywrightRunner,
    SelectorMismatchError,
)
from ..registry.db_client import DatabaseClient
from ..registry.memory_router import MemoryRouter
from .schemas import (
    FieldRuleDTO,
    InspectRuleResponse,
    SaveRuleResponse,
    ScrapeResponse,
    ScrapingRuleDTO,
)

# Initialize FastMCP Server without extra keyword arguments to ensure cross-version compatibility
mcp = FastMCP("Nexus-Scraper MCP")

db_client = DatabaseClient()
memory_router = MemoryRouter(db_client)
dom_cleaner = DOMCleaner()


# Playwright-only CSS pseudo-classes that BeautifulSoup's soupsieve rejects but
# the Playwright execution engine actually supports (e.g. `:has()`, `:text-is()`).
_PW_PSEUDO_NAMES = (
    r"text-is|text-matches|text|nth-match|has|"
    r"below|above|right-of|left-of|near|focus-within"
)


def _strip_pw_pseudo_classes(selector: str) -> str:
    """Remove Playwright-only pseudo-classes with balanced parentheses.

    soupsieve does not understand `:has()`, `:text-is()`, etc., and they may
    nest (e.g. `:has(> span:text-is("漲跌"))`). A naive `[^)]*` match breaks on
    the first inner `)`, leaving a dangling `)`. We walk the string tracking
    parenthesis depth and delete each `:name(` ... matching `)` span.
    """
    names = _PW_PSEUDO_NAMES
    result = []
    i = 0
    n = len(selector)
    while i < n:
        if (
            selector[i] == ":"
            and i + 1 < n
            and selector[i + 1] not in "(#.[>~+* \t\n"
        ):
            rest = selector[i + 1:]
            m = re.match(names, rest)
            if m:
                j = i + 1 + len(m.group(0))
                while j < n and selector[j] in " \t":
                    j += 1
                if j < n and selector[j] == "(":
                    depth = 0
                    k = j
                    while k < n:
                        if selector[k] == "(":
                            depth += 1
                        elif selector[k] == ")":
                            depth -= 1
                            if depth == 0:
                                break
                        k += 1
                    i = k + 1 if k < n else n
                    continue
        result.append(selector[i])
        i += 1
    return "".join(result)


def _validate_selector_syntax(selector: str) -> bool:
    """Dry-run test to ensure a CSS selector is syntactically valid.

    Uses BeautifulSoup for standard CSS, but tolerates Playwright-only
    pseudo-classes (e.g. `:has()`, `:text-is()`) that the real execution
    engine (Playwright) understands but soupsieve does not. We strip those
    constructs and re-test the remaining structural selector so valid
    Playwright selectors are not falsely rejected.
    """
    if not selector or not selector.strip():
        return False
    try:
        mock_soup = BeautifulSoup("<div><span></span></div>", "html.parser")
        mock_soup.select_one(selector)
        return True
    except Exception:
        pass
    # Strip Playwright-only pseudo-classes, then re-test the residual structure.
    normalized = _strip_pw_pseudo_classes(selector).replace(">>>", " ")
    try:
        mock_soup = BeautifulSoup("<div><span></span></div>", "html.parser")
        mock_soup.select_one(normalized)
        return True
    except Exception:
        return False


async def _fetch_page_html(url: str) -> str:
    """Fetch raw HTML of a target page using Playwright."""
    async with PlaywrightRunner() as runner:
        if not runner._browser:
            await runner.start()
        context = await runner._browser.new_context(user_agent=runner.user_agent)
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            return await page.content()
        finally:
            await page.close()
            await context.close()


@mcp.tool()
async def scrape_url(
    url: str,
    force_explore: bool = False,
    max_dom_size: Optional[int] = None,
) -> str:
    """Scrape structured data using Zero-Token fast execution or request Agent compilation.

    Args:
        url: Full URL of the target webpage.
        force_explore: Force DOM capture for selector re-evaluation.
        max_dom_size: Optional custom character limit for returned cleaned DOM.

    Returns:
        JSON serialized ScrapeResponse.
    """
    start_ts = time.perf_counter()
    rule = None if force_explore else await memory_router.match_rule(url)

    # 1. Fast Path: Rule Cache Hit (Zero Token)
    if rule and not force_explore:
        try:
            async with PlaywrightRunner() as runner:
                res = await runner.execute_rule(url, rule)

            duration_ms = int((time.perf_counter() - start_ts) * 1000)
            await db_client.log_execution(
                rule_id=rule["id"],
                target_url=url,
                engine_mode="FAST",
                success=True,
                duration_ms=duration_ms,
                token_cost=0,
            )

            resp = ScrapeResponse(
                status="SUCCESS",
                target_url=url,
                engine_mode="FAST",
                data=res["data"],
                duration_ms=duration_ms,
                rule_id=rule["id"],
                rule_version=rule.get("version", 1),
            )
            return resp.model_dump_json(indent=2)

        except SelectorMismatchError as err:
            # Trigger Self-Healing: Return compressed DOM for Agent analysis
            await memory_router.invalidate_rule(rule["id"], reason="DEGRADED")
            raw_html = await _fetch_page_html(url)
            cleaned = dom_cleaner.clean(raw_html, max_dom_size=max_dom_size)

            resp = ScrapeResponse(
                status="NEED_REPAIR",
                target_url=url,
                engine_mode="NONE",
                cleaned_dom=cleaned,
                broken_fields=err.broken_fields,
                rule_id=rule["id"],
                rule_version=rule.get("version", 1),
                message="Cached selectors broke on current page structure. Please analyze cleaned_dom and call save_rule to update.",
                duration_ms=int((time.perf_counter() - start_ts) * 1000),
            )
            return resp.model_dump_json(indent=2)

    # 2. Miss: Cold-Start Exploration
    raw_html = await _fetch_page_html(url)
    cleaned = dom_cleaner.clean(raw_html, max_dom_size=max_dom_size)

    resp = ScrapeResponse(
        status="NEED_EXPLORATION",
        target_url=url,
        engine_mode="NONE",
        cleaned_dom=cleaned,
        message="No cached selectors for this URL pattern. Please analyze cleaned_dom and call save_rule to register.",
        duration_ms=int((time.perf_counter() - start_ts) * 1000),
    )
    return resp.model_dump_json(indent=2)


@mcp.tool()
async def save_rule(
    domain: str,
    url_pattern: str,
    title: str,
    fields: List[Dict[str, Any]],
    rule_id: Optional[str] = None,
    page_load_strategy: str = "domcontentloaded",
    wait_selector: Optional[str] = None,
) -> str:
    """Save or update pre-compiled CSS selectors into the SQLite memory registry.

    Args:
        domain: Domain of the website (e.g., 'news.ycombinator.com').
        url_pattern: Regex pattern matching target URLs.
        title: Description of the scraping rule.
        fields: List of field selector dictionaries (field_name, css_selector, etc.).
        rule_id: Unique rule ID. If existing, version will increment.
        page_load_strategy: Playwright wait strategy ('load', 'domcontentloaded', 'networkidle').
        wait_selector: Optional selector to wait for before extracting.

    Returns:
        JSON serialized SaveRuleResponse.
    """
    # Dry-run selector syntax validation
    invalid_selectors = []
    for f in fields:
        sel = f.get("css_selector", "")
        if not _validate_selector_syntax(sel):
            invalid_selectors.append(f"Primary selector '{sel}' on field '{f.get('field_name')}'")
        for fb in f.get("fallback_selectors", []):
            if not _validate_selector_syntax(fb):
                invalid_selectors.append(f"Fallback selector '{fb}' on field '{f.get('field_name')}'")

    if invalid_selectors:
        return json.dumps({
            "success": False,
            "error": f"Invalid CSS selector syntax detected: {', '.join(invalid_selectors)}"
        }, indent=2)

    clean_domain = memory_router.extract_domain(f"https://{domain}" if not domain.startswith("http") else domain)
    derived_id = rule_id or f"rule_{clean_domain.replace('.', '_')}"

    existing = await db_client.get_rule_by_id(derived_id)
    version = (existing.get("version", 1) + 1) if existing else 1

    rule_data: Dict[str, Any] = {
        "id": derived_id,
        "domain": clean_domain,
        "url_pattern": url_pattern,
        "title": title,
        "version": version,
        "status": "ACTIVE",
        "page_load_strategy": page_load_strategy,
        "wait_selector": wait_selector,
        "fields": fields,
    }

    await db_client.save_rule(rule_data)

    resp = SaveRuleResponse(
        success=True,
        rule_id=derived_id,
        version=version,
        message=f"Rule '{derived_id}' (v{version}) registered successfully in SQLite registry.",
        validated_fields=[f.get("field_name") for f in fields],
    )
    return resp.model_dump_json(indent=2)


@mcp.tool()
async def inspect_rule(identifier: str) -> str:
    """Inspect pre-compiled CSS selectors by Rule ID or Domain.

    Args:
        identifier: Exact Rule ID (e.g. 'rule_example_com') or Domain name / URL.

    Returns:
        JSON serialized InspectRuleResponse.
    """
    rules: List[Dict[str, Any]] = []

    # 1. Try finding by direct rule_id
    direct_rule = await db_client.get_rule_by_id(identifier.strip())
    if direct_rule:
        rules.append(direct_rule)
    else:
        # 2. Try querying by domain
        domain = memory_router.extract_domain(identifier)
        rules = await db_client.get_rules_by_domain(domain)

    rule_dtos = [ScrapingRuleDTO.model_validate(r) for r in rules]
    response = InspectRuleResponse(
        query=identifier,
        total_rules=len(rule_dtos),
        rules=rule_dtos,
    )
    return response.model_dump_json(indent=2)

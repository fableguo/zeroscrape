"""Memory Router module for Nexus-Scraper MCP.

Performs sub-millisecond URL pattern matching against cached and persistent
scraping rules in SQLite to determine whether a fast-path execution is possible.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from .db_client import DatabaseClient


@lru_cache(maxsize=512)
def get_compiled_regex(pattern: str) -> Optional[re.Pattern[str]]:
    """Compile and cache regular expressions for fast URL matching."""
    try:
        return re.compile(pattern)
    except re.error:
        return None


class MemoryRouter:
    """Routes target URLs to pre-compiled scraping rules in the registry."""

    def __init__(self, db_client: Optional[DatabaseClient] = None) -> None:
        """Initialize router with a database client instance."""
        self.db_client = db_client or DatabaseClient()

    @staticmethod
    def extract_domain(url: str) -> str:
        """Extract clean network location (domain) from a target URL."""
        parsed = urlparse(url.strip())
        domain = parsed.netloc.lower()
        if ":" in domain:
            domain = domain.split(":", 1)[0]
        return domain

    async def match_rule(self, target_url: str) -> Optional[Dict[str, Any]]:
        """Match target URL against active rules in the registry.

        Args:
            target_url: Full URL to match against scraping patterns.

        Returns:
            Matching rule dictionary with field selectors, or None if missed.
        """
        domain = self.extract_domain(target_url)
        if not domain:
            return None

        rules = await self.db_client.get_rules_by_domain(domain)
        if not rules:
            return None

        # Prioritize exact or regex URL matching
        for rule in rules:
            if rule.get("status") != "ACTIVE":
                continue

            pattern_str = rule.get("url_pattern", "")
            if not pattern_str:
                continue

            regex = get_compiled_regex(pattern_str)
            if regex and regex.search(target_url):
                return rule

        return None

    async def register_rule(self, rule_data: Dict[str, Any]) -> None:
        """Persist a new or updated scraping rule to the registry."""
        if "domain" not in rule_data:
            rule_data["domain"] = self.extract_domain(rule_data["url_pattern"])
        await self.db_client.save_rule(rule_data)

    async def invalidate_rule(self, rule_id: str, reason: str = "BROKEN") -> None:
        """Mark a rule as degraded or broken when selectors fail."""
        await self.db_client.update_rule_status(rule_id, status=reason)

    async def activate_rule(self, rule_id: str) -> None:
        """Restore a rule status to ACTIVE after successful repair."""
        await self.db_client.update_rule_status(rule_id, status="ACTIVE")

"""DOM Compression and Cleaning module for Nexus-Scraper MCP.

Compresses raw HTML by 85-95% by stripping noisy tags, inline styles,
tracking scripts, and irrelevant attributes while preserving semantic anchor
attributes essential for LLM CSS selector deduction.
"""

from __future__ import annotations

import os
import re
from typing import Optional, Set
from bs4 import BeautifulSoup, Comment, Tag

DEFAULT_MAX_DOM_SIZE = int(os.getenv("NEXUS_MAX_DOM_SIZE", "30000"))


class DOMCleaner:
    """Cleans and compresses HTML documents for LLM token optimization."""

    NOISE_TAGS: Set[str] = {
        "script",
        "style",
        "svg",
        "noscript",
        "iframe",
        "link",
        "meta",
        "canvas",
        "video",
        "audio",
        "track",
        "map",
        "object",
        "embed",
        "template",
        "portal",
        "picture",
        "footer",
        "nav",
    }

    PRESERVED_ATTR_KEYS: Set[str] = {
        "id",
        "class",
        "name",
        "role",
        "href",
        "src",
        "alt",
        "title",
        "type",
        "itemprop",
        "aria-label",
    }

    def __init__(self, max_length_chars: Optional[int] = None) -> None:
        """Initialize DOM cleaner with maximum character limit."""
        self.max_length_chars = max_length_chars or DEFAULT_MAX_DOM_SIZE

    def _is_preserved_attr(self, attr_name: str) -> bool:
        """Check if an attribute key should be retained for selector deduction."""
        attr_lower = attr_name.lower()
        if attr_lower in self.PRESERVED_ATTR_KEYS:
            return True
        if attr_lower.startswith("data-") or attr_lower.startswith("aria-"):
            return True
        return False

    def _clean_attributes(self, tag: Tag) -> None:
        """Filter out noisy attributes like inline styles and event handlers."""
        attrs_to_remove = []
        for key in list(tag.attrs.keys()):
            if not self._is_preserved_attr(key):
                attrs_to_remove.append(key)
            else:
                val = tag[key]
                if isinstance(val, list):
                    tag[key] = [c for c in val if len(c) < 64]
                elif isinstance(val, str) and (val.startswith("data:") or len(val) > 200):
                    attrs_to_remove.append(key)

        for key in attrs_to_remove:
            del tag[key]

    def clean(
        self,
        raw_html: str,
        target_selector: Optional[str] = None,
        max_dom_size: Optional[int] = None,
    ) -> str:
        """Clean and compress raw HTML into a compact DOM representation.

        Args:
            raw_html: Full raw HTML source of the target page.
            target_selector: Optional selector to narrow down to a sub-tree.
            max_dom_size: Override maximum character length for this call.

        Returns:
            Minified, semantic HTML string optimized for LLM token ingestion.
        """
        if not raw_html or not raw_html.strip():
            return ""

        char_limit = max_dom_size or self.max_length_chars
        soup = BeautifulSoup(raw_html, "html.parser")

        # Strip HTML comments
        for comment in soup.find_all(text=lambda text: isinstance(text, Comment)):
            comment.extract()

        # Strip noise elements
        for element in soup.find_all(list(self.NOISE_TAGS)):
            element.decompose()

        # Scope down to target sub-tree or main content if available
        root_node: Optional[Tag] = None
        if target_selector:
            root_node = soup.select_one(target_selector)

        if not root_node:
            root_node = (
                soup.find("main")
                or soup.find("article")
                or soup.find("body")
                or soup
            )

        # Sanitize attributes on all remaining tags
        for tag in root_node.find_all(True):
            self._clean_attributes(tag)
        if isinstance(root_node, Tag):
            self._clean_attributes(root_node)

        # Convert to string and collapse redundant whitespaces
        cleaned_html = str(root_node)
        cleaned_html = re.sub(r"\s+", " ", cleaned_html)
        cleaned_html = re.sub(r">\s+<", "><", cleaned_html).strip()

        # Enforce maximum character boundary
        if len(cleaned_html) > char_limit:
            cleaned_html = (
                cleaned_html[:char_limit]
                + "\n<!-- [TRUNCATED DUE TO MAX_DOM_SIZE LIMIT] -->"
            )

        return cleaned_html

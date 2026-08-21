"""Pydantic schemas and validation models for Nexus-Scraper MCP.

Defines structured request and response models for Agent-Driven MCP endpoints,
supporting Zero-LLM fast path execution, validation, and exploration signals.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class FieldRuleDTO(BaseModel):
    """Data Transfer Object representing a single field extraction rule."""

    field_name: str = Field(..., description="Name of the data field (e.g. 'price', 'title').")
    css_selector: str = Field(..., description="Primary CSS selector.")
    fallback_selectors: List[str] = Field(
        default_factory=list,
        description="Fallback selectors to try if primary fails.",
    )
    extract_type: Literal["text", "attribute", "html"] = Field(
        default="text",
        description="Data extraction method.",
    )
    attribute_name: Optional[str] = Field(
        default=None,
        description="Target attribute name if extract_type is 'attribute' (e.g. 'href').",
    )
    is_required: bool = Field(
        default=True,
        description="If True, failure to extract this field triggers a repair interrupt.",
    )
    validation_regex: Optional[str] = Field(
        default=None,
        description="Optional regex to validate data integrity.",
    )


class ScrapingRuleDTO(BaseModel):
    """Data Transfer Object representing a complete scraping rule."""

    id: str
    domain: str
    url_pattern: str
    title: str
    version: int = 1
    status: Literal["ACTIVE", "DEGRADED", "BROKEN", "EXPLORING"] = "ACTIVE"
    page_load_strategy: Literal["load", "domcontentloaded", "networkidle"] = "domcontentloaded"
    wait_selector: Optional[str] = None
    fields: List[FieldRuleDTO] = Field(default_factory=list)


class SaveRuleRequest(BaseModel):
    """Input payload for the save_rule MCP tool called by the Agent."""

    rule_id: Optional[str] = Field(default=None, description="Unique rule ID. Auto-generated if omitted.")
    domain: str = Field(..., description="Domain name (e.g. 'finance.yahoo.com').")
    url_pattern: str = Field(..., description="Regex pattern matching target URLs.")
    title: str = Field(..., description="Human-readable rule title.")
    page_load_strategy: Literal["load", "domcontentloaded", "networkidle"] = "domcontentloaded"
    wait_selector: Optional[str] = Field(default=None, description="Guard selector to wait for before extracting.")
    fields: List[FieldRuleDTO] = Field(..., description="List of field selector extraction rules.")


class SaveRuleResponse(BaseModel):
    """Response payload returned when an Agent saves or updates a rule."""

    success: bool
    rule_id: str
    version: int
    message: str
    validated_fields: Optional[List[str]] = None


class ScrapeResponse(BaseModel):
    """Unified response payload returned by the scrape_url MCP tool."""

    status: Literal["SUCCESS", "NEED_EXPLORATION", "NEED_REPAIR", "ERROR"] = Field(
        ...,
        description="Execution status indicating data success or need for Agent exploration.",
    )
    target_url: str
    engine_mode: Literal["FAST", "NONE"] = "FAST"
    data: Optional[Dict[str, Any]] = Field(default=None, description="Extracted key-value dictionary.")
    cleaned_dom: Optional[str] = Field(
        default=None,
        description="Compressed HTML DOM provided when exploration or repair is needed.",
    )
    broken_fields: Optional[List[str]] = Field(
        default=None,
        description="List of fields that failed extraction when status is NEED_REPAIR.",
    )
    message: Optional[str] = None
    duration_ms: int = Field(default=0, description="Execution duration in milliseconds.")
    rule_id: Optional[str] = None
    rule_version: Optional[int] = None


class InspectRuleResponse(BaseModel):
    """Response payload containing matching rules from the registry."""

    query: str
    total_rules: int
    rules: List[ScrapingRuleDTO]


# Prevent Pydantic V2 warnings on forward references
FieldRuleDTO.model_rebuild()
ScrapingRuleDTO.model_rebuild()
SaveRuleRequest.model_rebuild()
SaveRuleResponse.model_rebuild()
ScrapeResponse.model_rebuild()
InspectRuleResponse.model_rebuild()

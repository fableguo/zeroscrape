"""Database client module for Nexus-Scraper MCP.

Manages SQLite storage, connection pooling, WAL mode optimizations,
and CRUD operations for scraping rules and execution logs.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional

import aiosqlite

# Prioritize NEXUS_DB_PATH environment variable over project-relative default path
DEFAULT_DB_PATH = Path(
    os.getenv("NEXUS_DB_PATH")
    or (Path(__file__).resolve().parents[2] / "nexus_memory.db")
).resolve()

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;

CREATE TABLE IF NOT EXISTS scraping_rules (
    id TEXT PRIMARY KEY,
    domain TEXT NOT NULL,
    url_pattern TEXT NOT NULL,
    title TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    page_load_strategy TEXT DEFAULT 'domcontentloaded',
    wait_selector TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_rules_domain ON scraping_rules (domain);
CREATE INDEX IF NOT EXISTS idx_rules_status ON scraping_rules (status);

CREATE TABLE IF NOT EXISTS field_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id TEXT NOT NULL,
    field_name TEXT NOT NULL,
    css_selector TEXT NOT NULL,
    fallback_selectors TEXT,
    extract_type TEXT NOT NULL DEFAULT 'text',
    attribute_name TEXT,
    is_required BOOLEAN NOT NULL DEFAULT 1,
    validation_regex TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (rule_id) REFERENCES scraping_rules(id) ON DELETE CASCADE,
    UNIQUE(rule_id, field_name)
);

CREATE INDEX IF NOT EXISTS idx_field_rules_rule_id ON field_rules (rule_id);

CREATE TABLE IF NOT EXISTS execution_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id TEXT NOT NULL,
    target_url TEXT NOT NULL,
    engine_mode TEXT NOT NULL,
    success BOOLEAN NOT NULL,
    status_code INTEGER,
    error_message TEXT,
    token_cost INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (rule_id) REFERENCES scraping_rules(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_logs_rule_id ON execution_logs (rule_id);
CREATE INDEX IF NOT EXISTS idx_logs_created_at ON execution_logs (created_at);
"""


class DatabaseClient:
    """Asynchronous SQLite client managing memory registry and logs."""

    def __init__(self, db_path: Optional[str | Path] = None) -> None:
        """Initialize database client with target file path."""
        self.db_path = str(db_path or DEFAULT_DB_PATH)

    @asynccontextmanager
    async def get_connection(self) -> AsyncGenerator[aiosqlite.Connection, None]:
        """Context manager yielding a configured aiosqlite connection."""
        conn = await aiosqlite.connect(self.db_path)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys = ON;")
        await conn.execute("PRAGMA busy_timeout = 5000;")
        try:
            yield conn
        finally:
            await conn.close()

    async def init_db(self) -> None:
        """Initialize database tables and indexes if not existing."""
        async with self.get_connection() as conn:
            await conn.executescript(SCHEMA_SQL)
            await conn.commit()

    async def get_rule_by_id(self, rule_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a complete scraping rule along with all field selectors by ID."""
        query_rule = "SELECT * FROM scraping_rules WHERE id = ?;"
        query_fields = "SELECT * FROM field_rules WHERE rule_id = ?;"

        async with self.get_connection() as conn:
            async with conn.execute(query_rule, (rule_id,)) as cursor:
                rule_row = await cursor.fetchone()
                if not rule_row:
                    return None

            rule_dict = dict(rule_row)

            async with conn.execute(query_fields, (rule_id,)) as cursor:
                field_rows = await cursor.fetchall()
                fields = []
                for f in field_rows:
                    f_dict = dict(f)
                    fallbacks = f_dict.get("fallback_selectors")
                    f_dict["fallback_selectors"] = json.loads(fallbacks) if fallbacks else []
                    f_dict["is_required"] = bool(f_dict["is_required"])
                    fields.append(f_dict)
                rule_dict["fields"] = fields

            return rule_dict

    async def get_rules_by_domain(self, domain: str) -> List[Dict[str, Any]]:
        """Fetch all active scraping rules for a specific domain."""
        query = "SELECT id FROM scraping_rules WHERE domain = ? AND status = 'ACTIVE';"
        rules: List[Dict[str, Any]] = []

        async with self.get_connection() as conn:
            async with conn.execute(query, (domain,)) as cursor:
                rows = await cursor.fetchall()
                rule_ids = [row["id"] for row in rows]

        for r_id in rule_ids:
            rule = await self.get_rule_by_id(r_id)
            if rule:
                rules.append(rule)

        return rules

    async def save_rule(self, rule_data: Dict[str, Any]) -> None:
        """Upsert a scraping rule and its associated field selectors."""
        rule_upsert = """
        INSERT INTO scraping_rules (
            id, domain, url_pattern, title, version, status,
            page_load_strategy, wait_selector, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(id) DO UPDATE SET
            domain = excluded.domain,
            url_pattern = excluded.url_pattern,
            title = excluded.title,
            version = excluded.version,
            status = excluded.status,
            page_load_strategy = excluded.page_load_strategy,
            wait_selector = excluded.wait_selector,
            updated_at = CURRENT_TIMESTAMP;
        """
        field_upsert = """
        INSERT INTO field_rules (
            rule_id, field_name, css_selector, fallback_selectors,
            extract_type, attribute_name, is_required, validation_regex, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(rule_id, field_name) DO UPDATE SET
            css_selector = excluded.css_selector,
            fallback_selectors = excluded.fallback_selectors,
            extract_type = excluded.extract_type,
            attribute_name = excluded.attribute_name,
            is_required = excluded.is_required,
            validation_regex = excluded.validation_regex,
            updated_at = CURRENT_TIMESTAMP;
        """

        async with self.get_connection() as conn:
            await conn.execute(
                rule_upsert,
                (
                    rule_data["id"],
                    rule_data["domain"],
                    rule_data["url_pattern"],
                    rule_data["title"],
                    rule_data.get("version", 1),
                    rule_data.get("status", "ACTIVE"),
                    rule_data.get("page_load_strategy", "domcontentloaded"),
                    rule_data.get("wait_selector"),
                ),
            )

            for field in rule_data.get("fields", []):
                fallbacks = json.dumps(field.get("fallback_selectors", []))
                await conn.execute(
                    field_upsert,
                    (
                        rule_data["id"],
                        field["field_name"],
                        field["css_selector"],
                        fallbacks,
                        field.get("extract_type", "text"),
                        field.get("attribute_name"),
                        1 if field.get("is_required", True) else 0,
                        field.get("validation_regex"),
                    ),
                )
            await conn.commit()

    async def update_rule_status(self, rule_id: str, status: str) -> None:
        """Update lifecycle status of a rule (ACTIVE, DEGRADED, BROKEN, EXPLORING)."""
        query = "UPDATE scraping_rules SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?;"
        async with self.get_connection() as conn:
            await conn.execute(query, (status, rule_id))
            await conn.commit()

    async def log_execution(
        self,
        rule_id: str,
        target_url: str,
        engine_mode: str,
        success: bool,
        duration_ms: int,
        token_cost: int = 0,
        status_code: Optional[int] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """Record an execution telemetry log entry."""
        query = """
        INSERT INTO execution_logs (
            rule_id, target_url, engine_mode, success, status_code,
            error_message, token_cost, duration_ms
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
        """
        async with self.get_connection() as conn:
            await conn.execute(
                query,
                (
                    rule_id,
                    target_url,
                    engine_mode,
                    1 if success else 0,
                    status_code,
                    error_message,
                    token_cost,
                    duration_ms,
                ),
            )
            await conn.commit()

"""Nexus-Scraper MCP Application Entrypoint.

Initializes the persistent SQLite skill registry schema and runs the FastMCP server
via stdio transport for seamless Hermes and OpenClaw agent integration.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import sys
from pathlib import Path

# Ensure project root is in sys.path regardless of execution working directory
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

# Ensure logs go to STDERR ONLY so STDOUT remains 100% clean for MCP JSON-RPC protocol
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)],
)
logger = logging.getLogger("nexus-scraper-mcp")

from src.mcp_server.server import db_client, mcp
from src.registry.db_client import DEFAULT_DB_PATH, SCHEMA_SQL


def init_sync() -> None:
    """Initialize database tables synchronously on startup."""
    load_dotenv(PROJECT_ROOT / ".env")
    conn = sqlite3.connect(DEFAULT_DB_PATH)
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    conn.close()


def main() -> None:
    """Main execution function to bootstrap and run FastMCP server."""
    try:
        init_sync()
    except Exception as exc:
        logger.error("Failed to initialize database: %s", exc)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

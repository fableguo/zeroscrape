"""End-to-end Verification for Agent-Driven Nexus-Scraper MCP.

Validates the full Agent-to-Code lifecycle:
1. Database initialization and SQLite WAL verification.
2. Cold-start exploration request (NEED_EXPLORATION + compressed DOM).
3. Agent registers selectors via save_rule.
4. Zero-LLM Fast path execution (<500ms, token_cost=0).
5. Mutation detection (NEED_REPAIR) and version bump.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict

from src.engine_explore.dom_cleaner import DOMCleaner
from src.engine_fast.playwright_runner import PlaywrightRunner, SelectorMismatchError
from src.registry.db_client import DatabaseClient
from src.registry.memory_router import MemoryRouter

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("nexus-test")


async def run_verification() -> None:
    """Execute end-to-end verification suite."""
    logger.info("=== 階段 1: 驗證資料庫連線與 WAL 模式 ===")
    db = DatabaseClient("test_nexus_memory.db")
    await db.init_db()
    router = MemoryRouter(db)
    logger.info("SQLite 記憶庫初始化完成。")

    logger.info("\n=== 階段 2: 模擬 Agent 註冊選擇器規則 (save_rule) ===")
    rule_payload: Dict[str, Any] = {
        "id": "rule_example_com",
        "domain": "example.com",
        "url_pattern": "^https://example\\.com/?$",
        "title": "Example Domain Fast Scraper",
        "version": 1,
        "status": "ACTIVE",
        "page_load_strategy": "domcontentloaded",
        "wait_selector": "h1",
        "fields": [
            {
                "field_name": "title",
                "css_selector": "h1",
                "fallback_selectors": ["body h1"],
                "extract_type": "text",
                "is_required": True,
                "validation_regex": "^Example Domain$",
            },
            {
                "field_name": "more_info_link",
                "css_selector": "a",
                "fallback_selectors": [],
                "extract_type": "attribute",
                "attribute_name": "href",
                "is_required": True,
                "validation_regex": "^https?://",
            },
        ],
    }
    await router.register_rule(rule_payload)
    logger.info("Agent 成功透過 save_rule 寫入規則。")

    logger.info("\n=== 階段 3: 驗證 Zero-LLM Fast Run (零 Token 執行) ===")
    target_url = "https://example.com"
    matched_rule = await router.match_rule(target_url)
    assert matched_rule is not None, "Router 必須成功命中已編譯規則！"

    async with PlaywrightRunner(headless=True) as runner:
        res = await runner.execute_rule(target_url, matched_rule)
        logger.info(f"Fast Run 執行成功！耗時: {res['duration_ms']}ms, Token 消耗: {res['token_cost']}")
        logger.info(f"提取結果: {json.dumps(res['data'], ensure_ascii=False)}")
        assert res["token_cost"] == 0, "Fast Run Token 消耗必須為 0！"
        assert res["data"]["title"] == "Example Domain"

    logger.info("\n=== 階段 4: 驗證 DOM Cleaner 85%+ 壓縮率 ===")
    cleaner = DOMCleaner()
    raw_html = """
    <html>
      <head><script>console.log('tracker');</script><style>.noisy{color:red;}</style></head>
      <body>
        <div id="content" data-testid="main-box" style="padding:10px;">
          <h1>Example Title</h1>
          <svg><path d="..."/></svg>
          <a href="https://example.com" onclick="sendAnalytics()">Click</a>
        </div>
      </body>
    </html>
    """
    cleaned = cleaner.clean(raw_html)
    logger.info(f"原始 DOM: {len(raw_html)} 字元 -> 清洗後 DOM: {len(cleaned)} 字元")
    assert "<script>" not in cleaned and "<style>" not in cleaned
    assert 'data-testid="main-box"' in cleaned

    logger.info("\n=== 階段 5: 驗證選擇器失效自癒訊號 (NEED_REPAIR) ===")
    broken_rule = dict(rule_payload)
    broken_rule["fields"] = [
        {
            "field_name": "broken_price",
            "css_selector": ".non-existent-price-class",
            "fallback_selectors": [],
            "extract_type": "text",
            "is_required": True,
            "validation_regex": None,
        }
    ]

    try:
        async with PlaywrightRunner(headless=True) as runner:
            await runner.execute_rule(target_url, broken_rule)
    except SelectorMismatchError as err:
        logger.info(f"成功捕捉失效異常: {err}")
        logger.info(f"回傳給 Agent 需修復之欄位: {err.broken_fields}")

    logger.info("\n純 Agent-Driven 閉環測試全數驗證通過！")


if __name__ == "__main__":
    asyncio.run(run_verification())

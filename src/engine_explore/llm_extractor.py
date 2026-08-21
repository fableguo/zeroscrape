"""LLM Extraction and Self-Healing module for Nexus-Scraper MCP.

Interacts with LLMs to analyze minified DOM structures, synthesize resilient
CSS selectors, and persist updated rules into the SQLite registry.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx

from ..registry.db_client import DatabaseClient


EXPLORE_SYSTEM_PROMPT = """你是一位頂尖的前端架構師與網頁結構分析專家。
你的任務是分析經清理壓縮後的 HTML DOM 片段，針對目標資料欄位推導出精確且抗改版的 CSS Selectors。

## 選擇器規則：
1. 優先使用穩定語意屬性（id, data-testid, data-qa, aria-label, itemprop, name, 語意標籤）。
2. 嚴禁使用動態隨機雜湊 class。
3. 每個欄位提供 1 組主要 css_selector 及 1~2 組 fallback_selectors。
4. 判斷 extract_type（'text', 'attribute', 'html'）；若為 'attribute' 需給予 attribute_name（如 'href'）。
5. 必須提供 validation_regex 進行資料格式驗證。

## 輸出格式（必須為純 JSON，無任何額外註解）：
{
  "title": "<簡要規則名稱>",
  "wait_selector": "<頁面載入指標選擇器或 null>",
  "fields": [
    {
      "field_name": "<欄位名稱>",
      "css_selector": "<主要 CSS 選擇器>",
      "fallback_selectors": ["<備用選擇器 1>"],
      "extract_type": "text | attribute | html",
      "attribute_name": null,
      "is_required": true,
      "validation_regex": "<正則表達式>"
    }
  ]
}"""


class LLMExtractor:
    """Invokes LLMs to deduce selectors and update the registry."""

    def __init__(
        self,
        db_client: Optional[DatabaseClient] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: str = "gpt-4o-mini",
    ) -> None:
        """Initialize LLM extractor configuration and DB client."""
        self.db_client = db_client or DatabaseClient()
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "default-key")
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        self.model = model

    @staticmethod
    def _clean_json_response(raw_text: str) -> Dict[str, Any]:
        """Strip markdown code fence blocks and parse JSON."""
        cleaned = raw_text.strip()
        match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned)
        if match:
            cleaned = match.group(1)
        return json.loads(cleaned)

    async def _call_llm(
        self, system_prompt: str, user_prompt: str
    ) -> Tuple[Dict[str, Any], int]:
        """Send chat completion request to LLM endpoint."""
        url = f"{self.base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
        }

        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            res_json = resp.json()

        content = res_json["choices"][0]["message"]["content"]
        token_cost = res_json.get("usage", {}).get("total_tokens", 0)
        parsed_data = self._clean_json_response(content)
        return parsed_data, token_cost

    async def explore_new_rule(
        self,
        target_url: str,
        cleaned_dom: str,
        target_fields: List[str],
        rule_id: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], int]:
        """Deduce selectors for a new URL pattern and persist to database."""
        domain = urlparse(target_url).netloc.lower().split(":", 1)[0]
        derived_id = rule_id or f"rule_{domain.replace('.', '_')}_{int(len(target_fields))}"

        user_prompt = (
            f"目標網址: {target_url}\n"
            f"目標擷取欄位清單: {json.dumps(target_fields, ensure_ascii=False)}\n\n"
            f"網頁 DOM 片段:\n```html\n{cleaned_dom}\n```"
        )

        result, token_cost = await self._call_llm(EXPLORE_SYSTEM_PROMPT, user_prompt)

        rule_data = {
            "id": derived_id,
            "domain": domain,
            "url_pattern": f"^{re.escape(target_url.split('?')[0])}",
            "title": result.get("title", f"Scraper rule for {domain}"),
            "version": 1,
            "status": "ACTIVE",
            "page_load_strategy": "domcontentloaded",
            "wait_selector": result.get("wait_selector"),
            "fields": result.get("fields", []),
        }

        await self.db_client.save_rule(rule_data)
        return rule_data, token_cost

    async def repair_broken_rule(
        self,
        target_url: str,
        cleaned_dom: str,
        existing_rule: Dict[str, Any],
        broken_fields: List[str],
    ) -> Tuple[Dict[str, Any], int]:
        """Analyze DOM changes, repair broken selectors, and bump rule version."""
        user_prompt = (
            f"目標網址: {target_url}\n"
            f"目前失效欄位清單: {json.dumps(broken_fields, ensure_ascii=False)}\n"
            f"原先完整規則配置: {json.dumps(existing_rule, ensure_ascii=False)}\n\n"
            f"最新網頁 DOM 片段:\n```html\n{cleaned_dom}\n```\n\n"
            "請針對失效欄位重新推導抗改版選擇器，並回傳修復後的完整規則 JSON。"
        )

        result, token_cost = await self._call_llm(EXPLORE_SYSTEM_PROMPT, user_prompt)

        new_version = existing_rule.get("version", 1) + 1
        repaired_rule = {
            "id": existing_rule["id"],
            "domain": existing_rule["domain"],
            "url_pattern": existing_rule["url_pattern"],
            "title": result.get("title", existing_rule.get("title")),
            "version": new_version,
            "status": "ACTIVE",
            "page_load_strategy": existing_rule.get("page_load_strategy", "domcontentloaded"),
            "wait_selector": result.get("wait_selector", existing_rule.get("wait_selector")),
            "fields": result.get("fields", existing_rule.get("fields", [])),
        }

        await self.db_client.save_rule(repaired_rule)
        return repaired_rule, token_cost

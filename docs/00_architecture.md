# Nexus-Scraper MCP: System Architecture Design (00_architecture.md)

## 1. 系統願景：Agent-Driven 編譯與執行分離架構

Nexus-Scraper MCP 專為對接 OpenClaw / Hermes 等 AI Agent 設計。
在此架構下，**MCP Server 自身完全不依賴任何外部 LLM API、不需配置任何 API Key**，全權由呼叫端的 Agent 擔任編譯大腦：

* **執行期 (Run-time / 零 Token 高速通道)**：
  MCP 根據 SQLite 記憶庫中的 CSS Selectors，直接透過 Playwright 高速抓取並回傳資料。耗時 `< 500ms`，Token 消耗精確為 `0`。
* **探索與修復期 (Compile-time / Agent 主動修復)**：
  當遇到新網站 (Miss) 或網站改版選擇器失效 (Broken) 時，MCP 自動擷取頁面並由 `dom_cleaner` 進行 85%+ 的體積壓縮，將 `cleaned_dom` 回傳給呼叫端 Agent。
  呼叫端 Agent（OpenClaw/Hermes）自行推導新選擇器後，呼叫 `save_rule` 回寫資料庫，完成自我修復閉環。

---

## 2. 系統架構與互動資料流 (Architecture & Interaction Flow)

```text
+-------------------------------------------------------------------------+
|                        OpenClaw / Hermes Agent                          |
|  (擔任編譯器大腦：負責讀取 cleaned_dom，推導選擇器並呼叫 save_rule)     |
+-------------------------------------------------------------------------+
       ^                         |                        |
       | 1. scrape_url(url)      | 2. NEED_EXPLORATION    | 3. save_rule(...)
       |                         |    (含 85% 壓縮 DOM)    |
       v                         v                        v
+-------------------------------------------------------------------------+
|                           Nexus-Scraper MCP                             |
|  (純執行與記憶引擎：100% 零 API Key、零外部 LLM 相依)                    |
|                                                                         |
|  [Module A: MCP Server]   --> [Module B: Registry (SQLite WAL)]        |
|                                         |                               |
|                     +-------------------+-------------------+           |
|                     | (Rule Hit & Valid)| (Rule Miss / Fail)|           |
|                     v                   v                   |           |
|             [Module C: Engine Fast]   [Module D: DOM Cleaner]           |
|             (Playwright Zero-Token)   (HTML Noise Stripper)             |
+-------------------------------------------------------------------------+
```

---

## 3. 三大 MCP Tools 介面定義

1. **`scrape_url(url: str, fields: list[str] | None = None)`**
   * **命中時**：回傳 `status: "SUCCESS"`, `engine_mode: "FAST"`, `data: {...}`, `token_cost: 0`。
   * **未命中/改版失效時**：回傳 `status: "NEED_EXPLORATION" | "NEED_REPAIR"`, `cleaned_dom: "..."`, `broken_fields: [...]`，提示 Agent 自行分析並回寫。
2. **`save_rule(rule_data: dict)`**
   * Agent 將推導出的選擇器回寫至 SQLite 註冊表。
3. **`inspect_rule(domain_or_url: str)`**
   * 檢視目前已編譯儲存的選擇器規則。

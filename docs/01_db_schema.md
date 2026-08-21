# Nexus-Scraper MCP: Database Schema Design (01_db_schema.md)

## 1. 概述與儲存架構策略 (Overview & Strategy)
Nexus-Scraper MCP 採用「編譯與執行分離 (Agent-to-Code)」核心理念。記憶層 (Skill Registry) 負責持久化儲存已驗證的網頁結構、CSS 選擇器 (Selectors)、欄位提取規則與自癒歷史記錄。

* **儲存引擎 (Engine)**：預設採用 **SQLite 3** (支援 WAL 模式及 JSON1 擴充)，具備零維運負擔、輕量化、高效嵌入之特性，亦可無縫橋接至 PostgreSQL。
* **查詢目標 (Performance Goal)**：執行期 (Run-time) 選擇器快取命中查詢延遲 `< 2ms`，達成真正的 Zero-LLM Token 高速提取。

---

## 2. 實體關聯模型 (Entity Relationship Diagram)

```text
+-----------------------+          1:N          +-----------------------+
|    scraping_rules     |---------------------> |      field_rules      |
+-----------------------+                       +-----------------------+
| PK  id (TEXT/UUID)    |                       | PK  id (INTEGER)      |
|     domain (TEXT)     |                       | FK  rule_id (TEXT)    |
|     path_pattern      |                       |     field_name (TEXT) |
|     version (INTEGER) |                       |     css_selector      |
|     status (TEXT)     |                       |     extract_type      |
+-----------------------+                       +-----------------------+
           | 1
           |
           | 1:N
           v
+-----------------------+
|    execution_logs     |
+-----------------------+
| PK  id (INTEGER)      |
| FK  rule_id (TEXT)    |
|     engine_mode       | (FAST / EXPLORE)
|     success (BOOLEAN) |
|     token_cost        |
|     created_at        |
+-----------------------+
```

---

## 3. 資料庫 DDL 定義 (SQLite DDL Specification)

### 3.1 規則主表：`scraping_rules`
儲存網址匹配特徵與規則生命週期狀態。

```sql
CREATE TABLE IF NOT EXISTS scraping_rules (
    id TEXT PRIMARY KEY,                       -- 唯一識別碼 (例如: "twse_stock_day_all", UUID 或 domain_slug)
    domain TEXT NOT NULL,                      -- 目標網域 (例如: "mis.twse.com.tw")
    url_pattern TEXT NOT NULL,                 -- URL 正則表示式或路徑匹配規則
    title TEXT NOT NULL,                       -- 規則名稱 / 提取目標描述
    version INTEGER NOT NULL DEFAULT 1,        -- 規則版本號 (自癒後遞增)
    status TEXT NOT NULL DEFAULT 'ACTIVE',     -- 狀態: ACTIVE (啟用), DEGRADED (降級), BROKEN (失效), EXPLORING (修復中)
    page_load_strategy TEXT DEFAULT 'domcontentloaded', -- Playwright 等待策略 (load, domcontentloaded, networkidle)
    wait_selector TEXT,                        -- 頁面渲染完成之守衛選擇器 (Guard Selector)
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_rules_domain ON scraping_rules (domain);
CREATE INDEX IF NOT EXISTS idx_rules_status ON scraping_rules (status);
```

### 3.2 欄位選擇器表：`field_rules`
定義單一規則下各目標欄位的提取邏輯與降級容錯。

```sql
CREATE TABLE IF NOT EXISTS field_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id TEXT NOT NULL,                     -- 外鍵關聯 scraping_rules(id)
    field_name TEXT NOT NULL,                  -- 欄位名稱 (例如: "stock_code", "price", "title")
    css_selector TEXT NOT NULL,                -- 主要 CSS 選擇器
    fallback_selectors TEXT,                   -- 備用選擇器清單 (以 JSON Array 格式儲存)
    extract_type TEXT NOT NULL DEFAULT 'text', -- 提取模式: 'text', 'attribute', 'html', 'table'
    attribute_name TEXT,                       -- 當 extract_type = 'attribute' 時指定 (如 'href', 'src')
    is_required BOOLEAN NOT NULL DEFAULT 1,    -- 是否為必要欄位 (若遺失則判定為網頁改版)
    validation_regex TEXT,                     -- 欄位資料有效性正則驗證 (例如: "^\d+(\.\d+)?$")
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (rule_id) REFERENCES scraping_rules(id) ON DELETE CASCADE,
    UNIQUE(rule_id, field_name)
);

CREATE INDEX IF NOT EXISTS idx_field_rules_rule_id ON field_rules (rule_id);
```

### 3.3 執行與自癒記錄表：`execution_logs`
記錄每次抓取成敗、耗時與 Token 消耗，作為自我修復決策依據。

```sql
CREATE TABLE IF NOT EXISTS execution_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id TEXT NOT NULL,                     -- 外鍵關聯 scraping_rules(id)
    target_url TEXT NOT NULL,                  -- 實際請求之 URL
    engine_mode TEXT NOT NULL,                 -- 執行引擎: 'FAST' (Zero-LLM) | 'EXPLORE' (LLM 探索修復)
    success BOOLEAN NOT NULL,                  -- 抓取是否成功
    status_code INTEGER,                       -- HTTP 狀態碼
    error_message TEXT,                        -- 錯誤訊息 (若失敗)
    token_cost INTEGER NOT NULL DEFAULT 0,     -- LLM Token 消耗量 (FAST 模式必為 0)
    duration_ms INTEGER NOT NULL,              -- 總耗時 (毫秒)
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (rule_id) REFERENCES scraping_rules(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_logs_rule_id ON execution_logs (rule_id);
CREATE INDEX IF NOT EXISTS idx_logs_created_at ON execution_logs (created_at);
```

---

## 4. 核心 JSON 結構 (Data Transfer Object / Pydantic Interface)

當 `memory_router` 向 `playwright_runner` 傳遞記憶結構時，統一轉換為以下規格之 DTO：

```json
{
  "rule_id": "yahoo_finance_quote",
  "domain": "finance.yahoo.com",
  "url_pattern": "^https://finance\\.yahoo\\.com/quote/([A-Z0-9.-]+)",
  "version": 3,
  "status": "ACTIVE",
  "wait_selector": "section[data-testid='quote-price']",
  "fields": [
    {
      "field_name": "regular_market_price",
      "css_selector": "section[data-testid='quote-price'] fin-streamer[data-field='regularMarketPrice']",
      "fallback_selectors": [
        "span[data-testid='qsp-price']",
        ".livePrice"
      ],
      "extract_type": "text",
      "is_required": true,
      "validation_regex": "^[0-9,]+(\\.[0-9]+)?$"
    },
    {
      "field_name": "market_change_percent",
      "css_selector": "section[data-testid='quote-price'] fin-streamer[data-field='regularMarketChangePercent']",
      "fallback_selectors": [],
      "extract_type": "text",
      "is_required": true,
      "validation_regex": "^[+-]?[0-9,]+(\\.[0-9]+)?%$"
    }
  ]
}
```

---

## 5. 資料庫設定與效能調校 (Database Optimization)

為確保在高併發情境下的查詢效率與資料一致性，連線初始化時必須執行：

```sql
PRAGMA journal_mode = WAL;         -- 開啟 WAL 模式，支援讀寫並行
PRAGMA synchronous = NORMAL;       -- 平衡資料安全與寫入效能
PRAGMA foreign_keys = ON;          -- 強制外鍵約束
PRAGMA busy_timeout = 5000;        -- 鎖定等待上限 5000ms
```

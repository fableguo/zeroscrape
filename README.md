# Nexus-Scraper MCP

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![MCP Standard](https://img.shields.io/badge/MCP-Protocol%202024--11--05-brightgreen)](https://modelcontextprotocol.io)
[![Playwright](https://img.shields.io/badge/Engine-Playwright%20Headless-green)](https://playwright.dev/)

> **Self-Healing Web Scraping Protocol for AI Agents.**  
> Zero LLM token consumption on runtime, sub-second execution, and agent-driven adaptive self-healing.

---

## 💡 The Philosophy: Agent-to-Code Separation

Traditional LLM web scrapers dump massive, noisy HTML into context windows on **every request**, resulting in exorbitant token costs (15k~30k tokens/page) and 5~15s latencies.

**Nexus-Scraper MCP** solves this via **Compile-and-Run Separation**:
1. **Run-time (Zero Token / Sub-second)**: Uses pre-compiled, cached CSS selectors via headless Playwright. Latency is `< 500ms`, and token cost is strictly **0**.
2. **Compile-time / Self-Healing**: When encountering a new page or when target website redesigns break existing selectors, the MCP captures the DOM, compresses it by 85%+ with noise stripping, and emits a `NEED_EXPLORATION` or `NEED_REPAIR` signal. The calling Agent (OpenClaw, Hermes, Claude Desktop) deduces resilient selectors and calls `save_rule` to persist them in SQLite.

```
[AI Agent (e.g. OpenClaw / Hermes / Claude)]
      │
      │ 1. scrape_url(url)
      ▼
[Nexus-Scraper MCP]
      │
      ├─► [Cache Hit] ──────► Playwright (<500ms) ─────► Return Data (Token = 0)
      │
      └─► [Miss / Broken] ──► Strip HTML Noise (85%+) ─► Return {status: NEED_EXPLORATION, dom: "..."}
                                                               │
      ┌────────────────────────────────────────────────────────┘
      ▼
[AI Agent Analyzes DOM] ────► 2. save_rule(selectors) ──► Stored in SQLite WAL Memory
```

---

## 📊 Benchmark & Cost Comparison

| Metric | Traditional LLM Scrapers | Nexus-Scraper MCP (Runtime) |
| :--- | :--- | :--- |
| **Token Cost** | 10,000 ~ 30,000 tokens/req | **0 Tokens** (Cached) |
| **Average Latency** | 5.0s ~ 15.0s | **< 500ms** |
| **Maintenance** | Auto, but expensive | **Agent-assisted closed loop** |
| **External API Key** | Required | **None required (100% Local)** |
| **Protocol** | Custom HTTP / REST | **Native Model Context Protocol (MCP)** |

---

## 🚀 Quickstart

### 1. Installation

```bash
# Clone the repository
git clone https://github.com/your-username/nexus-scraper-mcp.git
cd nexus-scraper-mcp

# Setup virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies and browser binary
pip install -r requirements.txt
playwright install chromium
```

### 2. Run Test Suite

```bash
# Unit & Lifecycle Smoke Test
python test_workflow.py

# Stdio JSON-RPC Client Harness
python test_client.py
```

---

## 🔌 Agent Integration (MCP Configuration)

### Hermes Agent
Add to `~/.hermes/config.yaml` or run:
```bash
hermes mcp add nexus-scraper \
  --command /absolute/path/to/venv/bin/python \
  --args /absolute/path/to/main.py
```

### Claude Desktop
Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS):
```json
{
  "mcpServers": {
    "nexus-scraper": {
      "command": "/absolute/path/to/venv/bin/python",
      "args": ["/absolute/path/to/main.py"]
    }
  }
}
```

---

## 🛠️ MCP Tools Reference

* **`scrape_url(url: str, force_explore: bool = False, max_dom_size: int = 30000)`**  
  Scrapes target webpage. Returns extracted JSON data directly on cache hit, or returns compressed DOM with `NEED_EXPLORATION` / `NEED_REPAIR` signal on cache miss or selector degradation.

* **`save_rule(domain: str, url_pattern: str, title: str, fields: list, ...)`**  
  Registers or updates pre-compiled CSS selectors into the SQLite memory registry with dry-run selector syntax validation.

* **`inspect_rule(identifier: str)`**  
  Queries and inspects compiled scraping rules and selector status by Rule ID or domain name.

---

## ⚙️ Environment Variables

| Variable | Description | Default |
| :--- | :--- | :--- |
| `NEXUS_DB_PATH` | Path to persistent SQLite memory database | `nexus_memory.db` in project root |
| `NEXUS_MAX_DOM_SIZE` | Maximum character limit for returned compressed DOM | `30000` |

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).

"""Robust MCP Stdio Client Test Harness for Nexus-Scraper MCP.

Validates the MCP server via continuous stdio JSON-RPC exchange using
subprocess.Popen without premature stdin EOF disconnections.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

PROJECT_ROOT = Path(__file__).resolve().parent
PYTHON_EXE = sys.executable
MAIN_PY = str(PROJECT_ROOT / "main.py")


class MCPStdioTestClient:
    """Manages an active FastMCP stdio process with robust JSON-RPC communication."""

    def __init__(self) -> None:
        self.proc: Optional[subprocess.Popen[str]] = None
        self._msg_id = 0

    def start(self) -> None:
        """Launch the MCP server process with piped stdio."""
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        self.proc = subprocess.Popen(
            [PYTHON_EXE, MAIN_PY],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
            cwd=str(PROJECT_ROOT),
        )

    def send_request(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Send a JSON-RPC request and wait for the matching response line."""
        if not self.proc or not self.proc.stdin or not self.proc.stdout:
            raise RuntimeError("MCP process is not running.")

        self._msg_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._msg_id,
            "method": method,
            "params": params or {},
        }
        raw_msg = json.dumps(payload) + "\n"
        self.proc.stdin.write(raw_msg)
        self.proc.stdin.flush()

        # Read lines until JSON-RPC response with matching ID is returned
        start_time = time.time()
        while time.time() - start_time < 10.0:
            line = self.proc.stdout.readline()
            if not line:
                if self.proc.poll() is not None:
                    err = self.proc.stderr.read() if self.proc.stderr else ""
                    raise RuntimeError(f"Server exited prematurely (code {self.proc.returncode}): {err}")
                continue
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                if data.get("id") == self._msg_id:
                    return data
            except json.JSONDecodeError:
                continue

        raise TimeoutError(f"Timed out waiting for response to {method}")

    def send_notification(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        if not self.proc or not self.proc.stdin:
            raise RuntimeError("MCP process is not running.")
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
        }
        self.proc.stdin.write(json.dumps(payload) + "\n")
        self.proc.stdin.flush()

    def close(self) -> None:
        """Gracefully terminate the MCP process and close pipes."""
        if self.proc:
            try:
                if self.proc.stdin:
                    self.proc.stdin.close()
                self.proc.terminate()
                self.proc.wait(timeout=2.0)
            except Exception:
                self.proc.kill()
            finally:
                self.proc = None


def run_mcp_harness_test() -> None:
    """Execute complete MCP stdio handshake and tool testing."""
    print("=== 1. 啟動 FastMCP stdio 測試行程 ===")
    client = MCPStdioTestClient()
    client.start()
    print("MCP 伺服器行程已啟動 (PID: %s)" % (client.proc.pid if client.proc else "N/A"))

    try:
        print("\n=== 2. 執行 MCP 初始化協議 (initialize) ===")
        init_res = client.send_request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test-harness", "version": "1.0.0"},
            },
        )
        print("初始化成功回應:", json.dumps(init_res.get("result", {}), indent=2))
        client.send_notification("notifications/initialized")

        print("\n=== 3. 查詢註冊工具清單 (tools/list) ===")
        tools_res = client.send_request("tools/list")
        tools = tools_res.get("result", {}).get("tools", [])
        tool_names = [t.get("name") for t in tools]
        print(f"成功取得工具清單 ({len(tools)} 個工具): {tool_names}")
        assert "scrape_url" in tool_names, "必須包含 scrape_url 工具"
        assert "save_rule" in tool_names, "必須包含 save_rule 工具"
        assert "inspect_rule" in tool_names, "必須包含 inspect_rule 工具"

        print("\n=== 4. 呼叫 inspect_rule 工具 (tools/call) ===")
        call_res = client.send_request(
            "tools/call",
            {"name": "inspect_rule", "arguments": {"identifier": "example.com"}},
        )
        print("工具呼叫結果:", json.dumps(call_res.get("result", {}), indent=2))

        print("\n所有 MCP stdio 通訊協議測試全部通過！")

    finally:
        client.close()
        print("測試行程已安全關閉。")


if __name__ == "__main__":
    run_mcp_harness_test()

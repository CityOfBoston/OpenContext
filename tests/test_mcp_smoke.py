"""MCP Protocol Smoke Test.

Starts local_server.py as a subprocess, sends MCP JSON-RPC requests over HTTP,
and validates that the server responds with proper MCP protocol messages.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
SERVER_SCRIPT = PROJECT_ROOT / "scripts" / "local_server.py"
SERVER_URL = "http://localhost:8000/mcp"
STARTUP_TIMEOUT = 10
# Use example config so tests run without requiring config.yaml (which is gitignored)
TEST_CONFIG = PROJECT_ROOT / "examples" / "boston-opendata" / "config.yaml"


class TestMCPSmoke:
    """Smoke tests that verify the MCP server responds to core protocol requests."""

    process: subprocess.Popen | None = None

    def setup_method(self):
        """Start the local MCP server and wait until it is accepting connections."""
        env = os.environ.copy()
        env["OPENCONTEXT_CONFIG"] = str(TEST_CONFIG)
        self.process = subprocess.Popen(
            [sys.executable, str(SERVER_SCRIPT)],
            cwd=str(PROJECT_ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        deadline = time.monotonic() + STARTUP_TIMEOUT
        while time.monotonic() < deadline:
            try:
                resp = httpx.post(
                    SERVER_URL,
                    json={"jsonrpc": "2.0", "id": 0, "method": "ping"},
                    timeout=2.0,
                )
                if resp.status_code in (200, 400, 404, 500):
                    return
            except httpx.ConnectError:
                pass
            time.sleep(0.5)

        pytest.fail(f"local_server.py did not become ready within {STARTUP_TIMEOUT}s")

    def teardown_method(self):
        """Terminate the server process regardless of test outcome."""
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)

    def _send(self, method: str, params: dict | None = None, req_id: int = 1) -> dict:
        """Send a JSON-RPC request to the server and return the parsed response."""
        payload: dict = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
        }
        if params is not None:
            payload["params"] = params

        resp = httpx.post(SERVER_URL, json=payload, timeout=10.0)
        assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text}"
        return resp.json()

    def test_initialize(self):
        """Server must respond with result.protocolVersion on initialize."""
        data = self._send(
            "initialize",
            params={
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "smoke-test", "version": "0.1.0"},
            },
        )
        assert "result" in data, f"Expected 'result' in response, got: {data}"
        assert "protocolVersion" in data["result"], (
            f"Expected 'protocolVersion' in result, got: {data['result']}"
        )

    def test_tools_list(self):
        """Server must expose at least one tool via tools/list."""
        init_data = self._send(
            "initialize",
            params={
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "smoke-test", "version": "0.1.0"},
            },
            req_id=1,
        )
        assert "result" in init_data

        data = self._send("tools/list", req_id=2)
        assert "result" in data, f"Expected 'result' in response, got: {data}"
        tools = data["result"].get("tools", [])
        assert len(tools) >= 1, (
            f"Expected at least one tool, got {len(tools)}: {data['result']}"
        )

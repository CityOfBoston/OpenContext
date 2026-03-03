# run_local_server.py
"""Run OpenContext MCP server locally for testing (no Lambda needed)."""

import asyncio
import json
import logging
import os
import sys
import time
import uuid
from pathlib import Path

# Add project root to Python path so we can import from core
project_root = Path(__file__).parent.parent.resolve()
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import yaml
from aiohttp import web

from core.logging_utils import configure_json_logging
from core.mcp_server import MCPServer
from core.plugin_manager import PluginManager
from core.validators import get_logging_config

logger = logging.getLogger(__name__)

# Load config (OPENCONTEXT_CONFIG env var for tests; default config.yaml)
_config_path = os.environ.get("OPENCONTEXT_CONFIG", "config.yaml")
with open(_config_path) as f:
    config = yaml.safe_load(f)

# Configure JSON logging - use pretty format for local development
logging_config = get_logging_config(config)
configure_json_logging(
    level=logging_config.get("level", "INFO"),
    pretty=True,  # Pretty-print JSON for better local readability
)

# Global server instance
_plugin_manager = None
_mcp_server = None


async def init_server():
    """Initialize server on startup."""
    global _plugin_manager, _mcp_server

    print("🚀 Initializing OpenContext MCP Server locally...")

    # Initialize Plugin Manager
    _plugin_manager = PluginManager(config)
    await _plugin_manager.load_plugins()

    # Initialize MCP Server
    _mcp_server = MCPServer(_plugin_manager)

    print("✅ Server initialized successfully")
    print(f"Loaded plugins: {list(_plugin_manager.plugins.keys())}")
    print(f"Available tools: {len(_plugin_manager.get_all_tools())}")


async def handle_mcp_request(request):
    """Handle MCP JSON-RPC request."""
    start_time = time.perf_counter()
    try:
        body = await request.text()
        headers = dict(request.headers)

        # Extract session ID from headers for logging
        session_id = headers.get("mcp-session-id") or headers.get("Mcp-Session-Id")

        # Parse JSON to detect method and extract details for logging
        try:
            request_json = json.loads(body)
            method = request_json.get("method", "unknown")
            tool_name = None
            tool_args = None

            if method == "tools/call":
                params = request_json.get("params", {})
                tool_name = params.get("name")
                tool_args = params.get("arguments", {})
        except (json.JSONDecodeError, AttributeError):
            method = "unknown"
            tool_name = None
            tool_args = None

        # Log incoming request details
        logger.info(
            "Incoming MCP request",
            extra={
                "session_id": session_id,
                "method": method,
                "tool_name": tool_name,
                "tool_arguments": tool_args if tool_args else None,
            },
        )

        # Check if this is an initialize request
        is_initialize = method == "initialize"
        session_id_to_return = None

        if is_initialize:
            session_id_to_return = str(uuid.uuid4())
            logger.info(
                f"Initialize request detected, generating session ID: {session_id_to_return}"
            )

        # Use the same handler as Lambda
        response = await _mcp_server.handle_http_request(body, headers)

        # Add session ID to response headers if this was an initialize request
        response_headers = dict(response.get("headers", {}))
        if session_id_to_return:
            response_headers["Mcp-Session-Id"] = session_id_to_return

        # Calculate and log response time
        duration_ms = (time.perf_counter() - start_time) * 1000
        logger.info(
            "MCP request processed",
            extra={
                "session_id": session_id_to_return or session_id,
                "method": method,
                "tool_name": tool_name,
                "duration_ms": round(duration_ms, 2),
                "status_code": response.get("statusCode", 200),
            },
        )

        return web.Response(
            text=response.get("body", "{}"),
            status=response.get("statusCode", 200),
            headers=response_headers,
        )

    except Exception as e:
        duration_ms = (time.perf_counter() - start_time) * 1000
        logger.error(
            f"Error processing MCP request: {e}",
            extra={"duration_ms": round(duration_ms, 2)},
            exc_info=True,
        )
        return web.Response(
            text=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32603, "message": str(e)},
                }
            ),
            status=500,
            headers={"Content-Type": "application/json"},
        )


async def start_server():
    """Start local HTTP server."""
    await init_server()

    app = web.Application()
    app.router.add_post("/mcp", handle_mcp_request)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "localhost", 8000)
    await site.start()

    # Generate server name from config variables
    server_name = None
    if "plugins" in config:
        # Try to get city_name from enabled plugin
        for plugin_name, plugin_config in config["plugins"].items():
            if plugin_config.get("enabled"):
                if "city_name" in plugin_config:
                    city_name = plugin_config["city_name"].lower().replace(" ", "-")
                    server_name = f"{city_name}-opendata"
                    break
                elif "organization" in plugin_config:
                    org_name = plugin_config["organization"].lower().replace(" ", "-")
                    server_name = f"{org_name}-opendata"
                    break

        # Fallback to lambda_name or server_name from config
        if not server_name:
            if "aws" in config and "lambda_name" in config["aws"]:
                lambda_name = config["aws"]["lambda_name"]
                # Remove -mcp suffix if present
                server_name = lambda_name.replace("-mcp", "")
            elif "server_name" in config:
                server_name = (
                    config["server_name"].lower().replace(" ", "-").replace("'", "")
                )

    # Default fallback
    if not server_name:
        server_name = "opencontext-mcp"

    # Get client path (relative to project root)
    client_path = project_root / "client" / "opencontext-client"
    client_path_str = str(client_path.resolve())

    # Construct Claude config JSON
    claude_config = {
        server_name: {"command": client_path_str, "args": ["http://localhost:8000"]}
    }

    print("\n" + "=" * 50)
    print("🌐 Local MCP Server running!")
    print("=" * 50)
    print("URL: http://localhost:8000/mcp")
    print("\n" + "=" * 50)
    print("📋 Claude Desktop Configuration")
    print("=" * 50)
    print("\nAdd this to your Claude Desktop config file:")
    print("(macOS: ~/Library/Application Support/Claude/claude_desktop_config.json)")
    print("\n" + json.dumps({"mcpServers": claude_config}, indent=2))
    print("\n" + "=" * 50)
    print("\nTest with:")
    print("  ./scripts/test_streamable_http.sh")
    print(
        '  or curl -X POST http://localhost:8000/mcp -H \'Content-Type: application/json\' -d \'{"jsonrpc":"2.0","id":1,"method":"ping"}\''
    )
    print("\nPress Ctrl+C to stop")
    print("=" * 50 + "\n")

    # Keep running
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        print("\n👋 Shutting down...")
        await _plugin_manager.shutdown()


if __name__ == "__main__":
    asyncio.run(start_server())

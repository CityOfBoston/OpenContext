"""Comprehensive tests for MCP Server.

These tests verify JSON-RPC protocol handling, request routing,
error handling, and HTTP request processing.
"""

import pytest
import json
from unittest.mock import AsyncMock, MagicMock

from core.mcp_server import MCPServer
from core.plugin_manager import PluginManager
from core.interfaces import ToolResult


class TestInitialize:
    """Test initialize method handling."""

    @pytest.mark.asyncio
    async def test_initialize_returns_correct_response(self):
        """Test that initialize returns correct protocol version and capabilities."""
        plugin_manager = MagicMock(spec=PluginManager)
        server = MCPServer(plugin_manager)

        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {},
        }

        response = await server.handle_request(request)

        assert response is not None
        assert response["jsonrpc"] == "2.0"
        assert response["id"] == 1
        assert "result" in response
        assert response["result"]["protocolVersion"] == "2025-03-26"
        assert "capabilities" in response["result"]
        assert "serverInfo" in response["result"]
        assert response["result"]["serverInfo"]["name"] == "opencontext"
        assert response["result"]["serverInfo"]["version"] == "1.0.0"

    @pytest.mark.asyncio
    async def test_initialize_notification_returns_none(self):
        """Test that initialize notification (no id) returns None."""
        plugin_manager = MagicMock(spec=PluginManager)
        server = MCPServer(plugin_manager)

        request = {
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {},
            # No "id" field - this is a notification
        }

        response = await server.handle_request(request)

        assert response is None


class TestToolsList:
    """Test tools/list method handling."""

    @pytest.mark.asyncio
    async def test_tools_list_returns_all_tools(self):
        """Test that tools/list returns all registered tools."""
        plugin_manager = MagicMock(spec=PluginManager)
        plugin_manager.get_all_tools.return_value = [
            {
                "name": "ckan__search_datasets",
                "description": "Search datasets",
                "inputSchema": {},
            },
            {
                "name": "ckan__get_dataset",
                "description": "Get dataset",
                "inputSchema": {},
            },
        ]
        server = MCPServer(plugin_manager)

        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {},
        }

        response = await server.handle_request(request)

        assert response is not None
        assert response["jsonrpc"] == "2.0"
        assert response["id"] == 1
        assert "result" in response
        assert "tools" in response["result"]
        assert len(response["result"]["tools"]) == 2
        assert response["result"]["tools"][0]["name"] == "ckan__search_datasets"

    @pytest.mark.asyncio
    async def test_tools_list_empty_when_no_tools(self):
        """Test that tools/list returns empty list when no tools registered."""
        plugin_manager = MagicMock(spec=PluginManager)
        plugin_manager.get_all_tools.return_value = []
        server = MCPServer(plugin_manager)

        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {},
        }

        response = await server.handle_request(request)

        assert response is not None
        assert response["result"]["tools"] == []


class TestToolsCall:
    """Test tools/call method handling."""

    @pytest.mark.asyncio
    async def test_tools_call_succeeds_with_valid_tool(self):
        """Test that tools/call succeeds with valid tool."""
        plugin_manager = MagicMock(spec=PluginManager)
        plugin_manager.execute_tool = AsyncMock(
            return_value=ToolResult(
                content=[{"type": "text", "text": "Tool executed successfully"}],
                success=True,
            )
        )
        server = MCPServer(plugin_manager)

        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "ckan__search_datasets",
                "arguments": {"query": "test", "limit": 10},
            },
        }

        response = await server.handle_request(request)

        assert response is not None
        assert response["jsonrpc"] == "2.0"
        assert response["id"] == 1
        assert "result" in response
        assert "content" in response["result"]
        assert len(response["result"]["content"]) > 0
        assert "isError" not in response["result"]
        plugin_manager.execute_tool.assert_called_once_with(
            "ckan__search_datasets",
            {"query": "test", "limit": 10},
        )

    @pytest.mark.asyncio
    async def test_tools_call_returns_error_when_tool_fails(self):
        """Test that tools/call returns error when tool execution fails."""
        plugin_manager = MagicMock(spec=PluginManager)
        plugin_manager.execute_tool = AsyncMock(
            return_value=ToolResult(
                content=[{"type": "text", "text": "Error occurred"}],
                success=False,
                error_message="Tool execution failed",
            )
        )
        server = MCPServer(plugin_manager)

        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "ckan__search_datasets",
                "arguments": {},
            },
        }

        response = await server.handle_request(request)

        assert response is not None
        assert "result" in response
        assert response["result"]["isError"] is True
        assert "error" in response["result"]
        assert response["result"]["error"] == "Tool execution failed"

    @pytest.mark.asyncio
    async def test_tools_call_returns_non_empty_error_when_error_message_is_none(self):
        """Test that tools/call returns a non-empty error when error_message is None."""
        plugin_manager = MagicMock(spec=PluginManager)
        plugin_manager.execute_tool = AsyncMock(
            return_value=ToolResult(
                content=[],
                success=False,
                error_message=None,
            )
        )
        server = MCPServer(plugin_manager)

        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "ckan__search_datasets",
                "arguments": {},
            },
        }

        response = await server.handle_request(request)

        assert response is not None
        assert "result" in response
        assert response["result"]["isError"] is True
        assert "error" in response["result"]
        assert response["result"]["error"]
        assert "unknown" in response["result"]["error"].lower()
        # Error should also be in content so LLM clients (e.g. Claude) receive it
        assert response["result"]["content"]
        assert response["result"]["content"][0]["text"] == "An unknown error occurred"

    @pytest.mark.asyncio
    async def test_tools_call_puts_error_in_content_when_content_empty(self):
        """Test that error message is in content when tool fails with empty content."""
        plugin_manager = MagicMock(spec=PluginManager)
        plugin_manager.execute_tool = AsyncMock(
            return_value=ToolResult(
                content=[],
                success=False,
                error_message="Resource 'xyz' not found (HTTP 404)",
            )
        )
        server = MCPServer(plugin_manager)

        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "ckan__query_data",
                "arguments": {"resource_id": "xyz"},
            },
        }

        response = await server.handle_request(request)

        assert response is not None
        assert response["result"]["isError"] is True
        assert response["result"]["error"] == "Resource 'xyz' not found (HTTP 404)"
        # Content must include error so all clients (Claude, Inspector, curl) receive it
        assert len(response["result"]["content"]) == 1
        assert response["result"]["content"][0]["type"] == "text"
        assert (
            response["result"]["content"][0]["text"]
            == "Resource 'xyz' not found (HTTP 404)"
        )

    @pytest.mark.asyncio
    async def test_tools_call_raises_error_when_tool_name_missing(self):
        """Test that tools/call raises error when tool name is missing."""
        plugin_manager = MagicMock(spec=PluginManager)
        server = MCPServer(plugin_manager)

        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "arguments": {},
                # Missing "name" field
            },
        }

        response = await server.handle_request(request)

        assert response is not None
        assert "error" in response
        assert response["error"]["code"] == -32603
        assert "Tool name is required" in response["error"]["data"]

    @pytest.mark.asyncio
    async def test_tools_call_handles_missing_arguments(self):
        """Test that tools/call handles missing arguments gracefully."""
        plugin_manager = MagicMock(spec=PluginManager)
        plugin_manager.execute_tool = AsyncMock(
            return_value=ToolResult(
                content=[{"type": "text", "text": "Success"}],
                success=True,
            )
        )
        server = MCPServer(plugin_manager)

        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "ckan__search_datasets",
                # Missing "arguments" field
            },
        }

        response = await server.handle_request(request)

        assert response is not None
        # Should use empty dict as default
        plugin_manager.execute_tool.assert_called_once_with(
            "ckan__search_datasets",
            {},
        )


class TestPing:
    """Test ping method handling."""

    @pytest.mark.asyncio
    async def test_ping_returns_ok(self):
        """Test that ping returns ok status."""
        plugin_manager = MagicMock(spec=PluginManager)
        server = MCPServer(plugin_manager)

        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "ping",
            "params": {},
        }

        response = await server.handle_request(request)

        assert response is not None
        assert response["jsonrpc"] == "2.0"
        assert response["id"] == 1
        assert response["result"]["status"] == "ok"


class TestNotifications:
    """Test notification handling."""

    @pytest.mark.asyncio
    async def test_notifications_initialized_returns_none(self):
        """Test that notifications/initialized returns None."""
        plugin_manager = MagicMock(spec=PluginManager)
        server = MCPServer(plugin_manager)

        request = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
            # No "id" field - this is a notification
        }

        response = await server.handle_request(request)

        assert response is None

    @pytest.mark.asyncio
    async def test_unknown_notification_returns_none(self):
        """Test that unknown notification method returns None."""
        plugin_manager = MagicMock(spec=PluginManager)
        server = MCPServer(plugin_manager)

        request = {
            "jsonrpc": "2.0",
            "method": "notifications/unknown",
            "params": {},
            # No "id" field - this is a notification
        }

        response = await server.handle_request(request)

        assert response is None


class TestUnknownMethods:
    """Test handling of unknown methods."""

    @pytest.mark.asyncio
    async def test_unknown_method_raises_error(self):
        """Test that unknown method raises ValueError."""
        plugin_manager = MagicMock(spec=PluginManager)
        server = MCPServer(plugin_manager)

        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "unknown/method",
            "params": {},
        }

        response = await server.handle_request(request)

        assert response is not None
        assert "error" in response
        assert response["error"]["code"] == -32603
        assert "Unknown method" in response["error"]["data"]


class TestErrorHandling:
    """Test error handling."""

    @pytest.mark.asyncio
    async def test_exception_in_handler_returns_error_response(self):
        """Test that exceptions in handler return error response."""
        plugin_manager = MagicMock(spec=PluginManager)
        plugin_manager.get_all_tools.side_effect = Exception("Internal error")
        server = MCPServer(plugin_manager)

        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {},
        }

        response = await server.handle_request(request)

        assert response is not None
        assert "error" in response
        assert response["error"]["code"] == -32603
        assert response["error"]["message"] == "Internal error"
        assert "Internal error" in response["error"]["data"]

    @pytest.mark.asyncio
    async def test_exception_in_notification_returns_none(self):
        """Test that exceptions in notification handlers return None."""
        plugin_manager = MagicMock(spec=PluginManager)
        plugin_manager.get_all_tools.side_effect = Exception("Internal error")
        server = MCPServer(plugin_manager)

        request = {
            "jsonrpc": "2.0",
            "method": "tools/list",
            "params": {},
            # No "id" field - this is a notification
        }

        response = await server.handle_request(request)

        # Notifications should return None even on error
        assert response is None


class TestHTTPRequestHandling:
    """Test HTTP request handling."""

    @pytest.mark.asyncio
    async def test_handle_http_request_with_valid_json(self):
        """Test handling HTTP request with valid JSON."""
        plugin_manager = MagicMock(spec=PluginManager)
        plugin_manager.get_all_tools.return_value = []
        server = MCPServer(plugin_manager)

        request_body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "ping",
                "params": {},
            }
        )

        response = await server.handle_http_request(request_body)

        assert response["statusCode"] == 200
        assert response["headers"]["Content-Type"] == "application/json"
        assert "body" in response
        body = json.loads(response["body"])
        assert body["jsonrpc"] == "2.0"
        assert body["id"] == 1

    @pytest.mark.asyncio
    async def test_handle_http_request_with_invalid_json(self):
        """Test handling HTTP request with invalid JSON."""
        plugin_manager = MagicMock(spec=PluginManager)
        server = MCPServer(plugin_manager)

        request_body = "invalid json {"

        response = await server.handle_http_request(request_body)

        assert response["statusCode"] == 400
        assert response["headers"]["Content-Type"] == "application/json"
        body = json.loads(response["body"])
        assert body["error"]["code"] == -32700
        assert body["error"]["message"] == "Parse error"

    @pytest.mark.asyncio
    async def test_handle_http_request_with_notification(self):
        """Test handling HTTP request with notification (no id)."""
        plugin_manager = MagicMock(spec=PluginManager)
        server = MCPServer(plugin_manager)

        request_body = json.dumps(
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
                # No "id" field
            }
        )

        response = await server.handle_http_request(request_body)

        assert response["statusCode"] == 200
        assert response["body"] == ""  # Empty body for notifications

    @pytest.mark.asyncio
    async def test_handle_http_request_preserves_headers(self):
        """Test that HTTP request handler accepts optional headers."""
        plugin_manager = MagicMock(spec=PluginManager)
        plugin_manager.get_all_tools.return_value = []
        server = MCPServer(plugin_manager)

        request_body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "ping",
                "params": {},
            }
        )

        headers = {"X-Custom-Header": "value"}
        response = await server.handle_http_request(request_body, headers)

        assert response["statusCode"] == 200
        # Headers are not modified by handle_http_request
        # They're just passed through for logging purposes

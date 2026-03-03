"""Example custom plugin for OpenContext.

This is a minimal example showing how to create a custom plugin.
Copy this to custom_plugins/your_plugin_name/plugin.py and customize.
"""

import logging
from typing import Any, Dict, List

import httpx

from core.interfaces import MCPPlugin, PluginType, ToolDefinition, ToolResult

logger = logging.getLogger(__name__)


class ExamplePlugin(MCPPlugin):
    """Example custom plugin."""

    plugin_name = "example_plugin"
    plugin_type = PluginType.CUSTOM_API
    plugin_version = "1.0.0"

    def __init__(self, config: Dict[str, Any]) -> None:
        """Initialize plugin with configuration."""
        super().__init__(config)
        self.api_url = config.get("api_url", "")
        self.api_key = config.get("api_key")
        self.client: httpx.AsyncClient | None = None

    async def initialize(self) -> bool:
        """Initialize plugin and test connection."""
        try:
            headers = {}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"

            self.client = httpx.AsyncClient(
                base_url=self.api_url,
                headers=headers,
                timeout=120,
            )

            # Test connection
            response = await self.client.get("/health")
            response.raise_for_status()

            self._initialized = True
            logger.info("Example plugin initialized successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize example plugin: {e}", exc_info=True)
            return False

    async def shutdown(self) -> None:
        """Shutdown plugin and close HTTP client."""
        if self.client:
            await self.client.aclose()
            self.client = None
        self._initialized = False

    def get_tools(self) -> List[ToolDefinition]:
        """Get list of tools provided by this plugin."""
        return [
            ToolDefinition(
                name="get_item",
                description="Get an item from the API",
                input_schema={
                    "type": "object",
                    "properties": {
                        "item_id": {
                            "type": "string",
                            "description": "Item ID",
                        },
                    },
                    "required": ["item_id"],
                },
            ),
        ]

    async def execute_tool(
        self, tool_name: str, arguments: Dict[str, Any]
    ) -> ToolResult:
        """Execute a tool by name."""
        try:
            if tool_name == "get_item":
                item_id = arguments.get("item_id")
                if not item_id:
                    return ToolResult(
                        content=[],
                        success=False,
                        error_message="item_id is required",
                    )

                response = await self.client.get(f"/items/{item_id}")
                response.raise_for_status()
                data = response.json()

                return ToolResult(
                    content=[
                        {
                            "type": "text",
                            "text": f"Item: {data.get('name', 'Unknown')}\n"
                            f"Description: {data.get('description', 'N/A')}",
                        }
                    ],
                    success=True,
                )

            else:
                return ToolResult(
                    content=[],
                    success=False,
                    error_message=f"Unknown tool: {tool_name}",
                )

        except Exception as e:
            logger.error(f"Error executing tool {tool_name}: {e}", exc_info=True)
            return ToolResult(
                content=[],
                success=False,
                error_message=f"Tool execution failed: {str(e)}",
            )

    async def health_check(self) -> bool:
        """Check if plugin is healthy."""
        try:
            if not self.client:
                return False
            response = await self.client.get("/health")
            return response.status_code == 200
        except Exception:
            return False

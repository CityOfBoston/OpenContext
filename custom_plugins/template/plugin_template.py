"""Plugin Template for OpenContext Custom Plugins

This template shows how to create a custom plugin for OpenContext.
Copy this file to custom_plugins/your_plugin_name/plugin.py and implement
the required methods.

Example:
    cp custom_plugins/template/plugin_template.py custom_plugins/my_api/plugin.py
    # Edit my_api/plugin.py, fill in TODOs
"""

import logging
from typing import Any, Dict, List

from core.interfaces import MCPPlugin, PluginType, ToolDefinition, ToolResult

logger = logging.getLogger(__name__)


class MyCustomPlugin(MCPPlugin):
    """Template for a custom OpenContext plugin.

    This plugin demonstrates the structure and required methods.
    Replace 'MyCustomPlugin' with your plugin name.
    """

    # REQUIRED: Set these class attributes
    plugin_name = "my_custom_plugin"  # TODO: Change to your plugin name
    plugin_type = PluginType.CUSTOM_API  # TODO: Choose appropriate type
    plugin_version = "1.0.0"

    def __init__(self, config: Dict[str, Any]) -> None:
        """Initialize plugin with configuration.

        Args:
            config: Plugin-specific configuration from config.yaml
        """
        super().__init__(config)
        # TODO: Extract and validate configuration values
        # Example:
        # self.api_url = config.get("api_url")
        # self.api_key = config.get("api_key")

    async def initialize(self) -> bool:
        """Initialize the plugin and verify connectivity.

        This method should:
        - Create HTTP clients, database connections, etc.
        - Test connectivity to your data source
        - Validate configuration
        - Set self._initialized = True on success

        Returns:
            True if initialization succeeded, False otherwise

        Raises:
            Exception: If initialization fails critically
        """
        try:
            # TODO: Initialize your plugin here
            # Example:
            # self.client = httpx.AsyncClient(base_url=self.api_url)
            # response = await self.client.get("/health")
            # response.raise_for_status()

            self._initialized = True
            logger.info(f"{self.plugin_name} plugin initialized successfully")
            return True

        except Exception as e:
            logger.error(
                f"Failed to initialize {self.plugin_name} plugin: {e}", exc_info=True
            )
            return False

    async def shutdown(self) -> None:
        """Clean up plugin resources.

        This method should:
        - Close HTTP clients
        - Close database connections
        - Release any other resources
        - Set self._initialized = False
        """
        # TODO: Clean up resources
        # Example:
        # if self.client:
        #     await self.client.aclose()
        #     self.client = None

        self._initialized = False
        logger.info(f"{self.plugin_name} plugin shut down")

    def get_tools(self) -> List[ToolDefinition]:
        """Get list of tools provided by this plugin.

        Tool names should NOT include the plugin prefix (e.g., use "search"
        not "my_custom_plugin.search"). The Plugin Manager will add the prefix.

        Returns:
            List of tool definitions
        """
        return [
            ToolDefinition(
                name="example_tool",  # TODO: Change tool name
                description="Description of what this tool does",  # TODO: Update description
                input_schema={
                    "type": "object",
                    "properties": {
                        "param1": {
                            "type": "string",
                            "description": "Description of param1",
                        },
                        # TODO: Add more parameters as needed
                    },
                    "required": ["param1"],  # TODO: Specify required parameters
                },
            ),
            # TODO: Add more tools as needed
        ]

    async def execute_tool(
        self, tool_name: str, arguments: Dict[str, Any]
    ) -> ToolResult:
        """Execute a tool by name.

        Args:
            tool_name: Name of the tool (without plugin prefix)
            arguments: Tool input arguments

        Returns:
            ToolResult with content, success flag, and optional error message
        """
        try:
            if tool_name == "example_tool":  # TODO: Match your tool name
                # TODO: Implement tool logic
                param1 = arguments.get("param1")

                # Example implementation:
                # result = await self._call_api(param1)
                # formatted_result = self._format_result(result)

                return ToolResult(
                    content=[
                        {
                            "type": "text",
                            "text": "Tool executed successfully",  # TODO: Return actual result
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
        """Check if the plugin is healthy and can reach its data source.

        Returns:
            True if healthy, False otherwise
        """
        try:
            # TODO: Implement health check
            # Example:
            # response = await self.client.get("/health")
            # return response.status_code == 200

            return self._initialized

        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False

    # TODO: Add helper methods as needed
    # Example:
    # async def _call_api(self, param: str) -> Dict[str, Any]:
    #     """Helper method to call your API."""
    #     response = await self.client.get(f"/endpoint/{param}")
    #     return response.json()
    #
    # def _format_result(self, data: Dict[str, Any]) -> str:
    #     """Helper method to format results for display."""
    #     return f"Result: {data}"

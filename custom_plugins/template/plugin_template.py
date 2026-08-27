"""Plugin Template for OpenContext Custom Plugins

This template shows how to create a custom open-data plugin for OpenContext
using the shared :class:`BaseOpenDataPlugin` base class.

Copy this file to ``custom_plugins/your_plugin_name/plugin.py`` and implement
the TODO sections.  The template demonstrates:

* Configuration validation with :class:`BasePluginConfig`
* HTTP client lifecycle via ``_create_http_client``
* Tool dispatch with :class:`ToolHandler` and ``required_args``
* Data-access method stubs from the :class:`DataPlugin` interface
"""

import logging
from typing import Any, Dict, List, Optional

from core.base_plugin import BaseOpenDataPlugin, ToolHandler
from core.config_base import BasePluginConfig
from core.interfaces import PluginType, ToolDefinition, ToolResult

# Optional: add provider-specific fields to the base schema.
#from pydantic import field_validator

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# 1. Configuration schema
# ──────────────────────────────────────────────────────────────────────────────
# Subclass BasePluginConfig to add provider-specific fields (URLs, credentials,
# etc.).  The base class already provides ``enabled``, ``city_name`` and
# ``timeout`` with sensible defaults.

class MyPluginConfig(BasePluginConfig):
    """Configuration schema for your custom open-data plugin.

    Example ``config.yaml`` snippet::

        plugins:
          my_plugin:
            enabled: true
            city_name: "My City"
            base_url: "https://api.example.com"
            api_key: "${MY_API_KEY}"
    """

    base_url: str
    api_key: Optional[str] = None

    # Re-use the shared URL validator for any URL fields:
    # _validate_urls = field_validator("base_url")(BasePluginConfig.validate_url)


# ──────────────────────────────────────────────────────────────────────────────
# 2. Plugin class
# ──────────────────────────────────────────────────────────────────────────────

class MyCustomPlugin(BaseOpenDataPlugin):
    """Template for a custom open-data plugin.

    Replace ``MyCustomPlugin`` with your class name, fill in the TODOs below,
    and remove the methods you don't need.
    """

    # REQUIRED class attributes
    plugin_name = "my_custom_plugin"  # TODO: change to your plugin name
    plugin_type = PluginType.OPEN_DATA
    plugin_version = "1.0.0"

    # REQUIRED: point to your config schema so ``__init__`` validates eagerly.
    config_class = MyPluginConfig

    # BaseOpenDataPlugin.__init__(self, config) already:
    #   - validates ``config`` against ``MyPluginConfig``
    #   - stores the result in ``self.plugin_config``
    #   - initialises ``self._clients`` for HTTP client tracking

    # ──────────────────────────────────────────────────────────────────────────
    # Lifecycle
    # ──────────────────────────────────────────────────────────────────────────

    async def initialize(self) -> bool:
        """Set up connections and verify the data source is reachable.

        Returns:
            ``True`` on success (sets ``self._initialized``).
        """
        try:
            # Build headers from the validated config object
            headers = {}
            if self.plugin_config.api_key:
                headers["Authorization"] = self.plugin_config.api_key

            # Use the shared helper so the client is tracked for shutdown
            self.client = self._create_http_client(
                base_url=self.plugin_config.base_url,
                headers=headers,
                timeout=self.plugin_config.timeout,
            )

            # TODO: replace with a real health / discovery call to your API
            # response = await self.client.get("/health")
            # response.raise_for_status()

            self._initialized = True
            logger.info(
                f"{self.plugin_name} plugin initialised for {self.plugin_config.city_name}"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to initialise {self.plugin_name}: {e}", exc_info=True)
            return False

    # NOTE: ``shutdown()`` is provided by BaseOpenDataPlugin and will close
    # every client created via ``_create_http_client``.  Override only if you
    # need to release *additional* resources (database connections, etc.).

    # ──────────────────────────────────────────────────────────────────────────
    # Tool definitions  (what the MCP server advertises to clients)
    # ──────────────────────────────────────────────────────────────────────────

    def get_tools(self) -> List[ToolDefinition]:
        """Return the list of tools exposed by this plugin.

        Tool names should **NOT** include the plugin prefix — the Plugin Manager
        adds ``plugin_name__`` automatically.
        """
        return [
            ToolDefinition(
                name="search_datasets",
                description=f"Search datasets in {self.plugin_config.city_name}'s open data portal",
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query string",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of results (default: 20)",
                            "default": 20,
                        },
                    },
                    "required": ["query"],
                },
            ),
            # TODO: add more ToolDefinitions as needed
        ]

    # ──────────────────────────────────────────────────────────────────────────
    # Tool handlers  (how each tool is executed)
    # ──────────────────────────────────────────────────────────────────────────

    def tool_handlers(self) -> Dict[str, ToolHandler]:
        """Map tool name (without prefix) to a :class:`ToolHandler`.

        ``required_args`` is a tuple of argument names that must be present **and**
        truthy before the handler is invoked.  Missing args are automatically
        rejected with a friendly error message — no need to write that
        boiler-plate in every handler.
        """
        return {
            "search_datasets": ToolHandler(
                handler=self._tool_search_datasets,
                # "query" must be provided and non-empty
                required_args=("query",),
            ),
            # TODO: register additional handlers
        }

    async def _tool_search_datasets(self, arguments: Dict[str, Any]) -> ToolResult:
        """Handler for the ``search_datasets`` tool."""
        query = arguments["query"]
        limit = arguments.get("limit", 20)
        datasets = await self.search_datasets(query, limit)
        return ToolResult(
            content=[
                {
                    "type": "text",
                    "text": self._format_search_results(datasets),
                }
            ],
            success=True,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Health check
    # ──────────────────────────────────────────────────────────────────────────

    async def health_check(self) -> bool:
        """Check if the plugin can reach its data source."""
        try:
            # TODO: replace with a real probe
            # response = await self.client.get("/health")
            # return response.status_code == 200
            return self._initialized
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False

    # ──────────────────────────────────────────────────────────────────────────
    # DataPlugin abstract methods — implement these to fulfil the interface.
    # BaseOpenDataPlugin provides helpers such as ``format_records`` and
    # ``build_where_clause`` to reduce boiler-plate.
    # ──────────────────────────────────────────────────────────────────────────

    async def search_datasets(
        self, query: str, limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Search for datasets matching ``query``."""
        # TODO: implement API call
        raise NotImplementedError("TODO: implement search_datasets")

    async def get_dataset(self, dataset_id: str) -> Dict[str, Any]:
        """Get metadata for a specific dataset."""
        # TODO: implement API call
        raise NotImplementedError("TODO: implement get_dataset")

    async def query_data(
        self,
        resource_id: str,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Query records from a resource."""
        # TODO: implement API call
        raise NotImplementedError("TODO: implement query_data")

    # ──────────────────────────────────────────────────────────────────────────
    # Formatting helpers (private)
    # ──────────────────────────────────────────────────────────────────────────

    def _format_search_results(self, datasets: List[Dict[str, Any]]) -> str:
        """Format a list of datasets for display.

        Uses ``BaseOpenDataPlugin.format_records`` for consistent styling.
        """
        if not datasets:
            return f"No datasets found in {self.plugin_config.city_name}'s open data portal."

        # TODO: replace with provider-specific formatting
        return self.format_records(
            datasets,
            max_display=5,
            header=f"Found {len(datasets)} dataset(s):",
        )

"""Core interfaces and data models for OpenContext plugins.

This module defines the abstract base classes and data models that all plugins
must implement. The core framework is universal and never modified by governments.
"""

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class PluginType(str, Enum):
    """Types of plugins supported by OpenContext."""

    OPEN_DATA = "open_data"
    CUSTOM_API = "custom_api"
    DATABASE = "database"
    ANALYTICS = "analytics"


class ToolDefinition(BaseModel):
    """Definition of an MCP tool provided by a plugin."""

    name: str = Field(..., description="Tool name (without plugin prefix)")
    description: str = Field(..., description="Human-readable tool description")
    input_schema: Dict[str, Any] = Field(
        ..., description="JSON Schema for tool input parameters"
    )


class ToolResult(BaseModel):
    """Result of executing a tool."""

    content: List[Dict[str, Any]] = Field(
        default_factory=list, description="Tool output content"
    )
    success: bool = Field(..., description="Whether the tool execution succeeded")
    error_message: Optional[str] = Field(
        None, description="Error message if execution failed"
    )


class MCPPlugin(ABC):
    """Abstract base class for all OpenContext plugins.

    All plugins must inherit from this class and implement all required methods.
    Plugins are discovered automatically and loaded by the Plugin Manager.
    """

    # Class attributes that must be set by plugin implementations
    plugin_name: str = ""
    plugin_type: PluginType = PluginType.CUSTOM_API
    plugin_version: str = "1.0.0"

    def __init__(self, config: Dict[str, Any]) -> None:
        """Initialize plugin with configuration.

        Args:
            config: Plugin-specific configuration dictionary from config.yaml
        """
        self.config = config
        self._initialized = False

    @abstractmethod
    async def initialize(self) -> bool:
        """Initialize the plugin and verify it can connect to its data source.

        This method should:
        - Create HTTP clients, database connections, etc.
        - Test connectivity to the data source
        - Validate configuration
        - Set self._initialized = True on success

        Returns:
            True if initialization succeeded, False otherwise

        Raises:
            Exception: If initialization fails critically
        """
        pass

    @abstractmethod
    async def shutdown(self) -> None:
        """Clean up plugin resources.

        This method should:
        - Close HTTP clients
        - Close database connections
        - Release any other resources
        - Set self._initialized = False
        """
        pass

    @abstractmethod
    def get_tools(self) -> List[ToolDefinition]:
        """Get list of tools provided by this plugin.

        Tool names should NOT include the plugin prefix (e.g., use "search_datasets"
        not "ckan__search_datasets"). The Plugin Manager will add the prefix automatically
        using double underscores (e.g., "ckan__search_datasets").

        Returns:
            List of tool definitions
        """
        pass

    @abstractmethod
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
        pass

    @abstractmethod
    async def health_check(self) -> bool:
        """Check if the plugin is healthy and can reach its data source.

        Returns:
            True if healthy, False otherwise
        """
        pass

    @property
    def is_initialized(self) -> bool:
        """Check if plugin has been successfully initialized."""
        return self._initialized


class DataPlugin(MCPPlugin):
    """Extended interface for data source plugins.

    This interface provides common data operations that most open data plugins
    will implement. Plugins can inherit from this instead of MCPPlugin directly
    if they provide dataset search and query capabilities.
    """

    @abstractmethod
    async def search_datasets(
        self, query: str, limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Search for datasets matching a query.

        Args:
            query: Search query string
            limit: Maximum number of results to return

        Returns:
            List of dataset metadata dictionaries
        """
        pass

    @abstractmethod
    async def get_dataset(self, dataset_id: str) -> Dict[str, Any]:
        """Get detailed metadata for a specific dataset.

        Args:
            dataset_id: Unique identifier for the dataset

        Returns:
            Dataset metadata dictionary
        """
        pass

    @abstractmethod
    async def query_data(
        self,
        resource_id: str,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Query data from a specific resource/dataset.

        Args:
            resource_id: Unique identifier for the resource
            filters: Optional filters to apply to the query
            limit: Maximum number of records to return

        Returns:
            List of data records
        """
        pass

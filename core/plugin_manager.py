"""Plugin Manager for OpenContext.

Handles plugin discovery, loading, validation, and tool routing.
Enforces the "one fork = one MCP server" rule at runtime.
"""

import importlib
import inspect
import logging
from pathlib import Path
from typing import Any, Dict, List, Tuple

from core.interfaces import MCPPlugin, ToolResult
from core.validators import ConfigurationError, get_enabled_plugin_config

logger = logging.getLogger(__name__)


class PluginManager:
    """Manages plugin discovery, loading, and tool routing.

    The Plugin Manager:
    - Auto-discovers plugins from plugins/ and custom_plugins/
    - Loads only the enabled plugin from config.yaml
    - Validates that exactly ONE plugin is enabled (crashes if multiple)
    - Registers tools with plugin name prefix
    - Routes tool calls to the correct plugin
    """

    def __init__(self, config: Dict[str, Any]) -> None:
        """Initialize Plugin Manager with configuration.

        Args:
            config: Parsed configuration dictionary

        Raises:
            ConfigurationError: If multiple plugins are enabled
        """
        self.config = config
        self.plugins: Dict[str, MCPPlugin] = {}
        self.tools: Dict[
            str, Tuple[str, str]
        ] = {}  # tool_name -> (plugin_name, tool_name)
        self._initialized = False

    def discover_plugins(self) -> List[Tuple[str, Path]]:
        """Discover available plugins in plugins/ and custom_plugins/ directories.

        Returns:
            List of tuples (plugin_name, plugin_directory_path)
        """
        discovered = []
        base_dir = Path(__file__).parent.parent

        # Discover built-in plugins
        plugins_dir = base_dir / "plugins"
        if plugins_dir.exists():
            for plugin_dir in plugins_dir.iterdir():
                if plugin_dir.is_dir() and not plugin_dir.name.startswith("_"):
                    plugin_file = plugin_dir / "plugin.py"
                    if plugin_file.exists():
                        discovered.append((plugin_dir.name, plugin_dir))

        # Discover custom plugins
        custom_plugins_dir = base_dir / "custom_plugins"
        if custom_plugins_dir.exists():
            for plugin_dir in custom_plugins_dir.iterdir():
                if plugin_dir.is_dir() and not plugin_dir.name.startswith("_"):
                    plugin_file = plugin_dir / "plugin.py"
                    if plugin_file.exists():
                        discovered.append((plugin_dir.name, plugin_dir))

        logger.debug(
            f"Discovered {len(discovered)} plugins: {[p[0] for p in discovered]}"
        )
        return discovered

    def _load_plugin_class(self, plugin_name: str, plugin_path: Path) -> type:
        """Load plugin class from a plugin module.

        Args:
            plugin_name: Name of the plugin
            plugin_path: Path to plugin directory

        Returns:
            Plugin class that inherits from MCPPlugin

        Raises:
            ImportError: If plugin cannot be imported
            ValueError: If plugin class not found or invalid
        """
        # Determine module path
        if "plugins" in str(plugin_path):
            module_path = f"plugins.{plugin_name}.plugin"
        elif "custom_plugins" in str(plugin_path):
            module_path = f"custom_plugins.{plugin_name}.plugin"
        else:
            raise ValueError(f"Invalid plugin path: {plugin_path}")

        try:
            module = importlib.import_module(module_path)
        except ImportError as e:
            raise ImportError(f"Failed to import plugin {plugin_name}: {e}")

        # Find class that inherits from MCPPlugin
        plugin_class = None
        for name, obj in inspect.getmembers(module, inspect.isclass):
            if (
                obj != MCPPlugin
                and issubclass(obj, MCPPlugin)
                and obj.__module__ == module.__name__
            ):
                plugin_class = obj
                break

        if plugin_class is None:
            raise ValueError(
                f"Plugin {plugin_name} does not define a class inheriting from MCPPlugin"
            )

        return plugin_class

    async def load_plugins(self) -> None:
        """Load plugins based on configuration.

        CRITICAL: This method validates that exactly ONE plugin is enabled.
        If multiple plugins are enabled, it raises a fatal error that will
        crash the Lambda function.

        Raises:
            ConfigurationError: If zero or multiple plugins are enabled
            ImportError: If plugin cannot be imported
            RuntimeError: If plugin initialization fails
        """
        # Get enabled plugin config (validates count == 1)
        try:
            plugin_name, plugin_config = get_enabled_plugin_config(self.config)
        except ConfigurationError as e:
            # Log error and re-raise to crash Lambda
            logger.error(f"Plugin configuration error: {e}")
            raise

        # Discover all plugins
        discovered = self.discover_plugins()
        discovered_names = [p[0] for p in discovered]

        if plugin_name not in discovered_names:
            raise RuntimeError(
                f"Plugin '{plugin_name}' is enabled in config.yaml but not found.\n"
                f"Available plugins: {', '.join(discovered_names)}\n"
                f"Check that plugins/{plugin_name}/plugin.py exists, or\n"
                f"custom_plugins/{plugin_name}/plugin.py exists."
            )

        # Find plugin path
        plugin_path = next(p[1] for p in discovered if p[0] == plugin_name)

        # Load plugin class
        try:
            plugin_class = self._load_plugin_class(plugin_name, plugin_path)
        except (ImportError, ValueError) as e:
            logger.error(f"Failed to load plugin {plugin_name}: {e}")
            raise RuntimeError(f"Failed to load plugin {plugin_name}: {e}") from e

        # Instantiate plugin
        try:
            plugin_instance = plugin_class(plugin_config)
        except Exception as e:
            logger.error(f"Failed to instantiate plugin {plugin_name}: {e}")
            raise RuntimeError(
                f"Failed to instantiate plugin {plugin_name}: {e}"
            ) from e

        # Initialize plugin
        try:
            initialized = await plugin_instance.initialize()
            if not initialized:
                raise RuntimeError(
                    f"Plugin {plugin_name} initialization returned False"
                )
        except Exception as e:
            logger.error(f"Failed to initialize plugin {plugin_name}: {e}")
            raise RuntimeError(f"Failed to initialize plugin {plugin_name}: {e}") from e

        # Store plugin
        self.plugins[plugin_name] = plugin_instance

        # Register tools
        self._register_tools(plugin_name, plugin_instance)

        logger.info(
            f"Successfully loaded plugin: {plugin_name} "
            f"(type: {plugin_instance.plugin_type}, "
            f"version: {plugin_instance.plugin_version})"
        )

        self._initialized = True

    def _register_tools(self, plugin_name: str, plugin: MCPPlugin) -> None:
        """Register tools from a plugin.

        Args:
            plugin_name: Name of the plugin
            plugin: Plugin instance
        """
        tools = plugin.get_tools()

        for tool_def in tools:
            # Create prefixed tool name: plugin_name__tool_name
            # Using double underscore to avoid conflicts with tool names that contain underscores
            prefixed_name = f"{plugin_name}__{tool_def.name}"

            if prefixed_name in self.tools:
                logger.warning(f"Tool {prefixed_name} already registered, overwriting")

            self.tools[prefixed_name] = (plugin_name, tool_def.name)
            logger.debug(f"Registered tool: {prefixed_name}")

        logger.info(f"Registered {len(tools)} tools from plugin {plugin_name}")

    async def execute_tool(
        self, tool_name: str, arguments: Dict[str, Any]
    ) -> ToolResult:
        """Execute a tool by name.

        Args:
            tool_name: Full tool name (e.g., "ckan__search_datasets")
            arguments: Tool input arguments

        Returns:
            ToolResult with content and success flag

        Raises:
            ValueError: If tool not found
            RuntimeError: If plugin execution fails
        """
        if not self._initialized:
            raise RuntimeError(
                "Plugin Manager not initialized. Call load_plugins() first."
            )

        if tool_name not in self.tools:
            available = ", ".join(sorted(self.tools.keys()))
            raise ValueError(
                f"Tool '{tool_name}' not found. Available tools: {available}"
            )

        plugin_name, actual_tool_name = self.tools[tool_name]
        plugin = self.plugins.get(plugin_name)

        if plugin is None:
            raise RuntimeError(f"Plugin {plugin_name} not loaded")

        try:
            result = await plugin.execute_tool(actual_tool_name, arguments)
            return result
        except Exception as e:
            logger.error(
                f"Error executing tool {tool_name} in plugin {plugin_name}: {e}",
                exc_info=True,
            )
            error_msg = str(e) if str(e) else "Tool execution failed"
            return ToolResult(
                content=[],
                success=False,
                error_message=error_msg,
            )

    def get_all_tools(self) -> List[Dict[str, Any]]:
        """Get all registered tools with their definitions.

        Returns:
            List of tool definitions with prefixed names
        """
        tools = []

        for plugin_name, plugin in self.plugins.items():
            plugin_tools = plugin.get_tools()
            for tool_def in plugin_tools:
                # Use double underscore separator to match _register_tools
                prefixed_name = f"{plugin_name}__{tool_def.name}"
                tools.append(
                    {
                        "name": prefixed_name,
                        "description": tool_def.description,
                        "inputSchema": tool_def.input_schema,
                    }
                )

        return tools

    async def health_check(self) -> Dict[str, bool]:
        """Check health of all loaded plugins.

        Returns:
            Dictionary mapping plugin names to health status
        """
        health = {}

        for plugin_name, plugin in self.plugins.items():
            try:
                health[plugin_name] = await plugin.health_check()
            except Exception as e:
                logger.error(f"Health check failed for {plugin_name}: {e}")
                health[plugin_name] = False

        return health

    async def shutdown(self) -> None:
        """Shutdown all plugins and clean up resources."""
        for plugin_name, plugin in self.plugins.items():
            try:
                await plugin.shutdown()
                logger.info(f"Shutdown plugin: {plugin_name}")
            except Exception as e:
                logger.error(f"Error shutting down plugin {plugin_name}: {e}")

        self.plugins.clear()
        self.tools.clear()
        self._initialized = False

    @property
    def is_initialized(self) -> bool:
        """Check if Plugin Manager has been initialized."""
        return self._initialized

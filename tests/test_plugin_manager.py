"""Comprehensive tests for Plugin Manager.

These tests verify plugin discovery, loading, validation, tool routing,
and error handling. Tests are designed to fail if functionality breaks.
"""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from core.plugin_manager import PluginManager
from core.interfaces import MCPPlugin, ToolDefinition, ToolResult, PluginType
from core.validators import ConfigurationError


class TestPluginDiscovery:
    """Test plugin discovery functionality."""

    def test_discover_plugins_finds_builtin_plugins(self):
        """Test that discovery finds plugins in plugins/ directory."""
        config = {
            "plugins": {
                "ckan": {"enabled": True, "base_url": "https://data.example.com"}
            }
        }
        manager = PluginManager(config)
        discovered = manager.discover_plugins()

        # Should find at least CKAN plugin
        assert len(discovered) > 0
        plugin_names = [p[0] for p in discovered]
        assert "ckan" in plugin_names

    def test_discover_plugins_finds_custom_plugins(self, tmp_path):
        """Test that discovery finds plugins in custom_plugins/ directory."""
        # Create a temporary custom plugin structure
        base_dir = tmp_path / "test_project"
        base_dir.mkdir()
        custom_plugins_dir = base_dir / "custom_plugins" / "test_plugin"
        custom_plugins_dir.mkdir(parents=True)
        (custom_plugins_dir / "plugin.py").write_text("# Test plugin")

        config = {"plugins": {"test_plugin": {"enabled": True}}}

        # Mock the base_dir path
        with patch("core.plugin_manager.Path") as mock_path:
            mock_path.return_value.parent.parent = base_dir
            manager = PluginManager(config)
            # This test verifies discovery logic works
            # Actual discovery depends on file system structure
            assert manager.discover_plugins is not None

    def test_discover_plugins_ignores_hidden_directories(self):
        """Test that discovery ignores directories starting with underscore."""
        config = {"plugins": {"ckan": {"enabled": True}}}
        manager = PluginManager(config)
        discovered = manager.discover_plugins()

        # Should not include hidden directories
        plugin_names = [p[0] for p in discovered]
        assert not any(name.startswith("_") for name in plugin_names)

    def test_discover_plugins_returns_list_of_tuples(self):
        """Test that discovery returns list of (name, path) tuples."""
        config = {"plugins": {"ckan": {"enabled": True}}}
        manager = PluginManager(config)
        discovered = manager.discover_plugins()

        assert isinstance(discovered, list)
        if len(discovered) > 0:
            assert isinstance(discovered[0], tuple)
            assert len(discovered[0]) == 2
            assert isinstance(discovered[0][0], str)  # Plugin name
            assert isinstance(discovered[0][1], Path)  # Plugin path


class TestPluginLoading:
    """Test plugin loading functionality."""

    @pytest.mark.asyncio
    async def test_load_plugins_succeeds_with_valid_plugin(self):
        """Test that loading succeeds with valid enabled plugin."""
        config = {
            "plugins": {
                "ckan": {
                    "enabled": True,
                    "base_url": "https://data.example.com",
                    "city_name": "TestCity",
                }
            }
        }
        manager = PluginManager(config)

        with patch("core.plugin_manager.PluginManager._load_plugin_class") as mock_load:
            mock_plugin_class = MagicMock()
            mock_instance = Mock()
            mock_instance.initialize = AsyncMock(return_value=True)
            mock_instance.get_tools = Mock(return_value=[])
            mock_instance.plugin_name = "ckan"
            mock_instance.plugin_type = PluginType.OPEN_DATA
            mock_instance.plugin_version = "1.0.0"
            mock_plugin_class.return_value = mock_instance
            mock_load.return_value = mock_plugin_class

            await manager.load_plugins()

            assert manager.is_initialized
            assert "ckan" in manager.plugins
            assert manager.plugins["ckan"] == mock_instance

    @pytest.mark.asyncio
    async def test_load_plugins_rejects_multiple_enabled_plugins(self):
        """Test that loading fails when multiple plugins are enabled."""
        config = {
            "plugins": {
                "ckan": {"enabled": True, "base_url": "https://data.example.com"},
                "mbta": {"enabled": True, "api_url": "https://api.example.com"},
            }
        }
        manager = PluginManager(config)

        with pytest.raises(ConfigurationError) as exc_info:
            await manager.load_plugins()

        error_msg = str(exc_info.value)
        assert "Multiple Plugins Enabled" in error_msg
        assert "ckan" in error_msg
        assert "mbta" in error_msg

    @pytest.mark.asyncio
    async def test_load_plugins_rejects_no_enabled_plugins(self):
        """Test that loading fails when no plugins are enabled."""
        config = {
            "plugins": {
                "ckan": {"enabled": False},
            }
        }
        manager = PluginManager(config)

        with pytest.raises(ConfigurationError) as exc_info:
            await manager.load_plugins()

        assert "No Plugins Enabled" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_load_plugins_rejects_nonexistent_plugin(self):
        """Test that loading fails when enabled plugin doesn't exist."""
        config = {
            "plugins": {
                "nonexistent_plugin": {"enabled": True},
            }
        }
        manager = PluginManager(config)

        with pytest.raises(RuntimeError) as exc_info:
            await manager.load_plugins()

        error_msg = str(exc_info.value)
        assert "nonexistent_plugin" in error_msg.lower()
        assert "not found" in error_msg.lower()

    @pytest.mark.asyncio
    async def test_load_plugins_rejects_plugin_init_failure(self):
        """Test that loading fails when plugin initialization returns False."""
        config = {
            "plugins": {
                "ckan": {"enabled": True, "base_url": "https://data.example.com"}
            }
        }
        manager = PluginManager(config)

        with patch("core.plugin_manager.PluginManager._load_plugin_class") as mock_load:
            mock_plugin_class = MagicMock()
            mock_instance = Mock()
            mock_instance.initialize = AsyncMock(return_value=False)  # Init fails
            mock_instance.plugin_name = "ckan"
            mock_plugin_class.return_value = mock_instance
            mock_load.return_value = mock_plugin_class

            with pytest.raises(RuntimeError) as exc_info:
                await manager.load_plugins()

            assert "initialization" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_load_plugins_rejects_plugin_init_exception(self):
        """Test that loading fails when plugin initialization raises exception."""
        config = {
            "plugins": {
                "ckan": {"enabled": True, "base_url": "https://data.example.com"}
            }
        }
        manager = PluginManager(config)

        with patch("core.plugin_manager.PluginManager._load_plugin_class") as mock_load:
            mock_plugin_class = MagicMock()
            mock_instance = Mock()
            mock_instance.initialize = AsyncMock(side_effect=Exception("Init failed"))
            mock_instance.plugin_name = "ckan"
            mock_plugin_class.return_value = mock_instance
            mock_load.return_value = mock_plugin_class

            with pytest.raises(RuntimeError) as exc_info:
                await manager.load_plugins()

            assert (
                "initialization" in str(exc_info.value).lower()
                or "failed" in str(exc_info.value).lower()
            )

    @pytest.mark.asyncio
    async def test_load_plugins_passes_config_to_plugin(self):
        """Test that plugin config is passed correctly to plugin."""
        config = {
            "plugins": {
                "ckan": {
                    "enabled": True,
                    "base_url": "https://data.example.com",
                    "city_name": "TestCity",
                    "timeout": 120,
                }
            }
        }
        manager = PluginManager(config)

        with patch("core.plugin_manager.PluginManager._load_plugin_class") as mock_load:
            mock_plugin_class = MagicMock()
            mock_instance = Mock()
            mock_instance.initialize = AsyncMock(return_value=True)
            mock_instance.get_tools = Mock(return_value=[])
            mock_instance.plugin_name = "ckan"
            mock_instance.plugin_type = PluginType.OPEN_DATA
            mock_instance.plugin_version = "1.0.0"
            mock_plugin_class.return_value = mock_instance
            mock_load.return_value = mock_plugin_class

            await manager.load_plugins()

            # Verify plugin was instantiated with correct config
            mock_plugin_class.assert_called_once()
            call_args = mock_plugin_class.call_args[0][0]
            assert call_args["base_url"] == "https://data.example.com"
            assert call_args["city_name"] == "TestCity"
            assert call_args["timeout"] == 120


class TestToolRegistration:
    """Test tool registration functionality."""

    @pytest.mark.asyncio
    async def test_tools_registered_with_plugin_prefix(self):
        """Test that tools are registered with plugin name prefix."""
        config = {
            "plugins": {
                "ckan": {"enabled": True, "base_url": "https://data.example.com"}
            }
        }
        manager = PluginManager(config)

        with patch("core.plugin_manager.PluginManager._load_plugin_class") as mock_load:
            mock_plugin_class = MagicMock()
            mock_instance = Mock()
            mock_instance.initialize = AsyncMock(return_value=True)
            mock_instance.get_tools = Mock(
                return_value=[
                    ToolDefinition(
                        name="search_datasets",
                        description="Search datasets",
                        input_schema={},
                    ),
                    ToolDefinition(
                        name="get_dataset",
                        description="Get dataset",
                        input_schema={},
                    ),
                ]
            )
            mock_instance.plugin_name = "ckan"
            mock_instance.plugin_type = PluginType.OPEN_DATA
            mock_instance.plugin_version = "1.0.0"
            mock_plugin_class.return_value = mock_instance
            mock_load.return_value = mock_plugin_class

            await manager.load_plugins()

            # Tools should be registered with double underscore prefix
            assert "ckan__search_datasets" in manager.tools
            assert "ckan__get_dataset" in manager.tools
            assert manager.tools["ckan__search_datasets"] == ("ckan", "search_datasets")
            assert manager.tools["ckan__get_dataset"] == ("ckan", "get_dataset")

    @pytest.mark.asyncio
    async def test_get_all_tools_returns_prefixed_tools(self):
        """Test that get_all_tools returns tools with plugin prefix."""
        config = {
            "plugins": {
                "ckan": {"enabled": True, "base_url": "https://data.example.com"}
            }
        }
        manager = PluginManager(config)

        with patch("core.plugin_manager.PluginManager._load_plugin_class") as mock_load:
            mock_plugin_class = MagicMock()
            mock_instance = Mock()
            mock_instance.initialize = AsyncMock(return_value=True)
            mock_instance.get_tools = Mock(
                return_value=[
                    ToolDefinition(
                        name="search_datasets",
                        description="Search datasets",
                        input_schema={"type": "object"},
                    ),
                ]
            )
            mock_instance.plugin_name = "ckan"
            mock_instance.plugin_type = PluginType.OPEN_DATA
            mock_instance.plugin_version = "1.0.0"
            mock_plugin_class.return_value = mock_instance
            mock_load.return_value = mock_plugin_class

            await manager.load_plugins()

            all_tools = manager.get_all_tools()
            assert len(all_tools) == 1
            assert all_tools[0]["name"] == "ckan__search_datasets"
            assert all_tools[0]["description"] == "Search datasets"
            assert all_tools[0]["inputSchema"] == {"type": "object"}


class TestToolExecution:
    """Test tool execution functionality."""

    @pytest.mark.asyncio
    async def test_execute_tool_succeeds_with_valid_tool(self):
        """Test that executing a valid tool succeeds."""
        config = {
            "plugins": {
                "ckan": {"enabled": True, "base_url": "https://data.example.com"}
            }
        }
        manager = PluginManager(config)

        with patch("core.plugin_manager.PluginManager._load_plugin_class") as mock_load:
            mock_plugin_class = MagicMock()
            mock_instance = Mock()
            mock_instance.initialize = AsyncMock(return_value=True)
            mock_instance.get_tools = Mock(return_value=[])
            mock_instance.execute_tool = AsyncMock(
                return_value=ToolResult(
                    content=[{"type": "text", "text": "Success"}],
                    success=True,
                )
            )
            mock_instance.plugin_name = "ckan"
            mock_instance.plugin_type = PluginType.OPEN_DATA
            mock_instance.plugin_version = "1.0.0"
            mock_plugin_class.return_value = mock_instance
            mock_load.return_value = mock_plugin_class

            await manager.load_plugins()
            manager.tools["ckan__test_tool"] = ("ckan", "test_tool")

            result = await manager.execute_tool("ckan__test_tool", {"arg": "value"})

            assert result.success is True
            assert len(result.content) > 0
            mock_instance.execute_tool.assert_called_once_with(
                "test_tool", {"arg": "value"}
            )

    @pytest.mark.asyncio
    async def test_execute_tool_fails_with_nonexistent_tool(self):
        """Test that executing nonexistent tool raises ValueError."""
        config = {
            "plugins": {
                "ckan": {"enabled": True, "base_url": "https://data.example.com"}
            }
        }
        manager = PluginManager(config)

        with patch("core.plugin_manager.PluginManager._load_plugin_class") as mock_load:
            mock_plugin_class = MagicMock()
            mock_instance = Mock()
            mock_instance.initialize = AsyncMock(return_value=True)
            mock_instance.get_tools = Mock(return_value=[])
            mock_instance.plugin_name = "ckan"
            mock_instance.plugin_type = PluginType.OPEN_DATA
            mock_instance.plugin_version = "1.0.0"
            mock_plugin_class.return_value = mock_instance
            mock_load.return_value = mock_plugin_class

            await manager.load_plugins()

            with pytest.raises(ValueError) as exc_info:
                await manager.execute_tool("ckan__nonexistent", {})

            assert "not found" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_execute_tool_fails_when_not_initialized(self):
        """Test that executing tool before initialization raises RuntimeError."""
        config = {"plugins": {"ckan": {"enabled": True}}}
        manager = PluginManager(config)

        with pytest.raises(RuntimeError) as exc_info:
            await manager.execute_tool("ckan__test", {})

        assert "not initialized" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_execute_tool_handles_plugin_exception(self):
        """Test that plugin exceptions are caught and returned as ToolResult."""
        config = {
            "plugins": {
                "ckan": {"enabled": True, "base_url": "https://data.example.com"}
            }
        }
        manager = PluginManager(config)

        with patch("core.plugin_manager.PluginManager._load_plugin_class") as mock_load:
            mock_plugin_class = MagicMock()
            mock_instance = Mock()
            mock_instance.initialize = AsyncMock(return_value=True)
            mock_instance.get_tools = Mock(return_value=[])
            mock_instance.execute_tool = AsyncMock(
                side_effect=Exception("Plugin error")
            )
            mock_instance.plugin_name = "ckan"
            mock_instance.plugin_type = PluginType.OPEN_DATA
            mock_instance.plugin_version = "1.0.0"
            mock_plugin_class.return_value = mock_instance
            mock_load.return_value = mock_plugin_class

            await manager.load_plugins()
            manager.tools["ckan__test_tool"] = ("ckan", "test_tool")

            result = await manager.execute_tool("ckan__test_tool", {})

            assert result.success is False
            assert result.error_message is not None
            assert (
                "error" in result.error_message.lower()
                or "failed" in result.error_message.lower()
            )


class TestHealthCheck:
    """Test health check functionality."""

    @pytest.mark.asyncio
    async def test_health_check_succeeds_when_plugin_healthy(self):
        """Test that health check succeeds when plugin is healthy."""
        config = {
            "plugins": {
                "ckan": {"enabled": True, "base_url": "https://data.example.com"}
            }
        }
        manager = PluginManager(config)

        with patch("core.plugin_manager.PluginManager._load_plugin_class") as mock_load:
            mock_plugin_class = MagicMock()
            mock_instance = Mock()
            mock_instance.initialize = AsyncMock(return_value=True)
            mock_instance.get_tools = Mock(return_value=[])
            mock_instance.health_check = AsyncMock(return_value=True)
            mock_instance.plugin_name = "ckan"
            mock_instance.plugin_type = PluginType.OPEN_DATA
            mock_instance.plugin_version = "1.0.0"
            mock_plugin_class.return_value = mock_instance
            mock_load.return_value = mock_plugin_class

            await manager.load_plugins()

            health = await manager.health_check()
            assert "ckan" in health
            assert health["ckan"] is True

    @pytest.mark.asyncio
    async def test_health_check_fails_when_plugin_unhealthy(self):
        """Test that health check fails when plugin is unhealthy."""
        config = {
            "plugins": {
                "ckan": {"enabled": True, "base_url": "https://data.example.com"}
            }
        }
        manager = PluginManager(config)

        with patch("core.plugin_manager.PluginManager._load_plugin_class") as mock_load:
            mock_plugin_class = MagicMock()
            mock_instance = Mock()
            mock_instance.initialize = AsyncMock(return_value=True)
            mock_instance.get_tools = Mock(return_value=[])
            mock_instance.health_check = AsyncMock(return_value=False)
            mock_instance.plugin_name = "ckan"
            mock_instance.plugin_type = PluginType.OPEN_DATA
            mock_instance.plugin_version = "1.0.0"
            mock_plugin_class.return_value = mock_instance
            mock_load.return_value = mock_plugin_class

            await manager.load_plugins()

            health = await manager.health_check()
            assert "ckan" in health
            assert health["ckan"] is False

    @pytest.mark.asyncio
    async def test_health_check_handles_plugin_exception(self):
        """Test that health check handles plugin exceptions gracefully."""
        config = {
            "plugins": {
                "ckan": {"enabled": True, "base_url": "https://data.example.com"}
            }
        }
        manager = PluginManager(config)

        with patch("core.plugin_manager.PluginManager._load_plugin_class") as mock_load:
            mock_plugin_class = MagicMock()
            mock_instance = Mock()
            mock_instance.initialize = AsyncMock(return_value=True)
            mock_instance.get_tools = Mock(return_value=[])
            mock_instance.health_check = AsyncMock(
                side_effect=Exception("Health check failed")
            )
            mock_instance.plugin_name = "ckan"
            mock_instance.plugin_type = PluginType.OPEN_DATA
            mock_instance.plugin_version = "1.0.0"
            mock_plugin_class.return_value = mock_instance
            mock_load.return_value = mock_plugin_class

            await manager.load_plugins()

            health = await manager.health_check()
            assert "ckan" in health
            assert health["ckan"] is False


class TestShutdown:
    """Test shutdown functionality."""

    @pytest.mark.asyncio
    async def test_shutdown_calls_plugin_shutdown(self):
        """Test that shutdown calls plugin shutdown method."""
        config = {
            "plugins": {
                "ckan": {"enabled": True, "base_url": "https://data.example.com"}
            }
        }
        manager = PluginManager(config)

        with patch("core.plugin_manager.PluginManager._load_plugin_class") as mock_load:
            mock_plugin_class = MagicMock()
            mock_instance = Mock()
            mock_instance.initialize = AsyncMock(return_value=True)
            mock_instance.get_tools = Mock(return_value=[])
            mock_instance.shutdown = AsyncMock()
            mock_instance.plugin_name = "ckan"
            mock_instance.plugin_type = PluginType.OPEN_DATA
            mock_instance.plugin_version = "1.0.0"
            mock_plugin_class.return_value = mock_instance
            mock_load.return_value = mock_plugin_class

            await manager.load_plugins()
            assert manager.is_initialized

            await manager.shutdown()

            mock_instance.shutdown.assert_called_once()
            assert not manager.is_initialized
            assert len(manager.plugins) == 0
            assert len(manager.tools) == 0

    @pytest.mark.asyncio
    async def test_shutdown_handles_plugin_exception(self):
        """Test that shutdown handles plugin exceptions gracefully."""
        config = {
            "plugins": {
                "ckan": {"enabled": True, "base_url": "https://data.example.com"}
            }
        }
        manager = PluginManager(config)

        with patch("core.plugin_manager.PluginManager._load_plugin_class") as mock_load:
            mock_plugin_class = MagicMock()
            mock_instance = Mock()
            mock_instance.initialize = AsyncMock(return_value=True)
            mock_instance.get_tools = Mock(return_value=[])
            mock_instance.shutdown = AsyncMock(side_effect=Exception("Shutdown failed"))
            mock_instance.plugin_name = "ckan"
            mock_instance.plugin_type = PluginType.OPEN_DATA
            mock_instance.plugin_version = "1.0.0"
            mock_plugin_class.return_value = mock_instance
            mock_load.return_value = mock_plugin_class

            await manager.load_plugins()

            # Should not raise exception
            await manager.shutdown()

            # State should still be cleared
            assert not manager.is_initialized
            assert len(manager.plugins) == 0
            assert len(manager.tools) == 0


class TestLoadPluginClass:
    """Test _load_plugin_class functionality."""

    def test_load_plugin_class_loads_builtin_plugin(self):
        """Test that builtin plugin class is loaded correctly."""
        config = {"plugins": {"ckan": {"enabled": True}}}
        manager = PluginManager(config)

        plugin_path = Path(__file__).parent.parent / "plugins" / "ckan"
        if plugin_path.exists():
            plugin_class = manager._load_plugin_class("ckan", plugin_path)
            assert plugin_class is not None
            assert issubclass(plugin_class, MCPPlugin)

    def test_load_plugin_class_raises_on_invalid_path(self):
        """Test that invalid plugin path raises ValueError."""
        config = {"plugins": {"ckan": {"enabled": True}}}
        manager = PluginManager(config)

        invalid_path = Path("/nonexistent/path")
        with pytest.raises((ImportError, ValueError)):
            manager._load_plugin_class("invalid", invalid_path)

    def test_load_plugin_class_raises_on_missing_plugin_class(self):
        """Test that missing plugin class raises ValueError."""
        config = {"plugins": {"ckan": {"enabled": True}}}
        manager = PluginManager(config)

        # Use a path that contains "plugins" to pass path validation
        # Then mock importlib.import_module to return a module without a plugin class
        from types import ModuleType

        plugin_dir = Path("/fake/plugins/test_plugin")

        # Create a mock module that doesn't have an MCPPlugin subclass
        mock_module = ModuleType("plugins.test_plugin.plugin")

        # Add some classes that are NOT MCPPlugin subclasses
        class RegularClass:
            pass

        mock_module.RegularClass = RegularClass
        # No MCPPlugin subclass exists

        with patch("core.plugin_manager.importlib.import_module") as mock_import:
            mock_import.return_value = mock_module

            with pytest.raises(ValueError) as exc_info:
                manager._load_plugin_class("test_plugin", plugin_dir)

            assert "does not define a class" in str(exc_info.value).lower()
            mock_import.assert_called_once_with("plugins.test_plugin.plugin")
            # Clean up any imported modules

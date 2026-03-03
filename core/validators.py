"""Configuration validation functions for OpenContext.

This module provides validation functions to ensure configuration is correct,
with special emphasis on enforcing the "one fork = one MCP server" rule.
"""

import logging
from typing import Any, Dict, List, Tuple

import yaml

logger = logging.getLogger(__name__)


class ConfigurationError(Exception):
    """Raised when configuration validation fails."""

    pass


def validate_plugin_count(config: Dict[str, Any]) -> Tuple[List[str], int]:
    """Validate that exactly ONE plugin is enabled in the configuration.

    This is a critical validation that enforces the "one fork = one MCP server"
    architecture principle.

    Args:
        config: Parsed configuration dictionary

    Returns:
        Tuple of (enabled_plugin_names, count)

    Raises:
        ConfigurationError: If zero or multiple plugins are enabled
    """
    plugins_config = config.get("plugins", {})
    enabled_plugins = []

    for plugin_name, plugin_config in plugins_config.items():
        if isinstance(plugin_config, dict) and plugin_config.get("enabled", False):
            enabled_plugins.append(plugin_name)

    count = len(enabled_plugins)

    if count == 0:
        raise ConfigurationError(
            "❌ Configuration Error: No Plugins Enabled\n\n"
            "You must enable exactly ONE plugin in config.yaml.\n\n"
            "To enable a plugin, set 'enabled: true' for:\n"
            "  • ckan\n"
            "  • A custom plugin in custom_plugins/\n\n"
            "See docs/GETTING_STARTED.md for setup instructions."
        )

    if count > 1:
        plugin_list = "\n".join(f"  • {name}" for name in enabled_plugins)
        raise ConfigurationError(
            f"❌ Configuration Error: Multiple Plugins Enabled\n\n"
            f"You have {count} plugins enabled in config.yaml:\n{plugin_list}\n\n"
            f"OpenContext enforces: One Fork = One MCP Server\n\n"
            f"This keeps deployments:\n"
            f"  ✓ Simple and focused\n"
            f"  ✓ Independently scalable\n"
            f"  ✓ Easy to maintain\n\n"
            f"To deploy multiple MCP servers:\n\n"
            f"  1. Fork this repository again\n"
            f"     Example: opencontext-opendata, opencontext-mbta\n\n"
            f"  2. Configure ONE plugin per fork\n"
            f"     Fork #1: Enable {enabled_plugins[0]} only\n"
            f"     Fork #2: Enable {enabled_plugins[1]} only\n\n"
            f"  3. Deploy each fork separately\n"
            f"     ./scripts/deploy.sh (in each fork)\n\n"
            f"See docs/ARCHITECTURE.md for details."
        )

    return enabled_plugins, count


def validate_config_structure(config: Dict[str, Any]) -> None:
    """Validate basic configuration structure.

    Args:
        config: Parsed configuration dictionary

    Raises:
        ConfigurationError: If structure is invalid
    """
    if not isinstance(config, dict):
        raise ConfigurationError("Configuration must be a YAML dictionary")

    if "plugins" not in config:
        raise ConfigurationError(
            "Configuration missing 'plugins' section. "
            "See config.yaml template for required structure."
        )

    if not isinstance(config.get("plugins"), dict):
        raise ConfigurationError("'plugins' section must be a dictionary")


def load_and_validate_config(config_path: str = "config.yaml") -> Dict[str, Any]:
    """Load and validate configuration from YAML file.

    Args:
        config_path: Path to config.yaml file

    Returns:
        Validated configuration dictionary

    Raises:
        ConfigurationError: If validation fails
        FileNotFoundError: If config file doesn't exist
    """
    try:
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)
    except FileNotFoundError:
        raise FileNotFoundError(
            f"Configuration file not found: {config_path}\n"
            "Create config.yaml based on the template in the repository."
        )
    except yaml.YAMLError as e:
        raise ConfigurationError(f"Invalid YAML in {config_path}: {e}")

    if config is None:
        raise ConfigurationError(f"Configuration file {config_path} is empty")

    # Validate structure
    validate_config_structure(config)

    # Validate plugin count (CRITICAL)
    enabled_plugins, count = validate_plugin_count(config)

    logger.info(
        f"Configuration validated: {count} plugin(s) enabled: {', '.join(enabled_plugins)}"
    )

    return config


def get_logging_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Get logging configuration from config dictionary.

    Args:
        config: Parsed configuration dictionary

    Returns:
        Dictionary with logging configuration (level, format)
    """
    logging_config = config.get("logging", {})
    return {
        "level": logging_config.get("level", "INFO"),
        "format": logging_config.get("format", "json"),
    }


def get_enabled_plugin_config(config: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Get the configuration for the single enabled plugin.

    Args:
        config: Parsed configuration dictionary

    Returns:
        Tuple of (plugin_name, plugin_config)

    Raises:
        ConfigurationError: If validation fails
    """
    enabled_plugins, _ = validate_plugin_count(config)

    if len(enabled_plugins) != 1:
        # This should never happen if validate_plugin_count was called first
        raise ConfigurationError("Internal error: Expected exactly one enabled plugin")

    plugin_name = enabled_plugins[0]
    plugin_config = config["plugins"][plugin_name]

    return plugin_name, plugin_config

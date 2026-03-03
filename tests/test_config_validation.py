"""Comprehensive tests for configuration validation.

These tests verify that configuration validation correctly enforces
the "one fork = one MCP server" rule and catches all invalid configurations.
"""

import pytest
import tempfile
import os
import yaml

from core.validators import (
    ConfigurationError,
    get_enabled_plugin_config,
    load_and_validate_config,
    validate_plugin_count,
    validate_config_structure,
    get_logging_config,
)


class TestValidatePluginCount:
    """Test validate_plugin_count function."""

    def test_single_plugin_enabled_returns_correct_count(self):
        """Test that exactly one enabled plugin returns count=1."""
        config = {
            "plugins": {
                "ckan": {"enabled": True, "base_url": "https://data.example.com"},
                "other_plugin": {"enabled": False},
            }
        }
        enabled, count = validate_plugin_count(config)
        assert count == 1
        assert enabled == ["ckan"]

    def test_no_plugins_enabled_raises_error(self):
        """Test that zero enabled plugins raises ConfigurationError."""
        config = {
            "plugins": {
                "ckan": {"enabled": False},
                "other": {"enabled": False},
            }
        }
        with pytest.raises(ConfigurationError) as exc_info:
            validate_plugin_count(config)

        error_msg = str(exc_info.value)
        assert "No Plugins Enabled" in error_msg
        assert "exactly one plugin" in error_msg.lower()

    def test_multiple_plugins_enabled_raises_error(self):
        """Test that multiple enabled plugins raises ConfigurationError."""
        config = {
            "plugins": {
                "ckan": {"enabled": True, "base_url": "https://data.example.com"},
                "mbta": {"enabled": True, "api_url": "https://api.example.com"},
            }
        }
        with pytest.raises(ConfigurationError) as exc_info:
            validate_plugin_count(config)

        error_msg = str(exc_info.value)
        assert "Multiple Plugins Enabled" in error_msg
        assert "ckan" in error_msg
        assert "mbta" in error_msg
        assert "One Fork = One MCP Server" in error_msg

    def test_three_plugins_enabled_shows_all_in_error(self):
        """Test that three enabled plugins all appear in error message."""
        config = {
            "plugins": {
                "ckan": {"enabled": True},
                "mbta": {"enabled": True},
                "custom": {"enabled": True},
            }
        }
        with pytest.raises(ConfigurationError) as exc_info:
            validate_plugin_count(config)

        error_msg = str(exc_info.value)
        assert "ckan" in error_msg
        assert "mbta" in error_msg
        assert "custom" in error_msg
        assert "3 plugins" in error_msg

    def test_enabled_false_explicitly_not_counted(self):
        """Test that enabled: false is not counted."""
        config = {
            "plugins": {
                "ckan": {"enabled": False},
                "mbta": {"enabled": True, "api_url": "https://api.example.com"},
            }
        }
        enabled, count = validate_plugin_count(config)
        assert count == 1
        assert enabled == ["mbta"]

    def test_missing_enabled_field_treated_as_false(self):
        """Test that missing 'enabled' field is treated as False."""
        config = {
            "plugins": {
                "ckan": {"base_url": "https://data.example.com"},  # No 'enabled' field
                "mbta": {"enabled": True, "api_url": "https://api.example.com"},
            }
        }
        enabled, count = validate_plugin_count(config)
        assert count == 1
        assert enabled == ["mbta"]

    def test_empty_plugins_dict_raises_error(self):
        """Test that empty plugins dict raises error."""
        config = {"plugins": {}}
        with pytest.raises(ConfigurationError) as exc_info:
            validate_plugin_count(config)

        assert "No Plugins Enabled" in str(exc_info.value)

    def test_non_dict_plugin_config_ignored(self):
        """Test that non-dict plugin configs are ignored."""
        config = {
            "plugins": {
                "ckan": {"enabled": True, "base_url": "https://data.example.com"},
                "invalid": "not a dict",  # Should be ignored
            }
        }
        enabled, count = validate_plugin_count(config)
        assert count == 1
        assert enabled == ["ckan"]


class TestValidateConfigStructure:
    """Test validate_config_structure function."""

    def test_valid_config_structure_passes(self):
        """Test that valid config structure passes validation."""
        config = {
            "server_name": "TestServer",
            "plugins": {
                "ckan": {"enabled": True},
            },
        }
        # Should not raise
        validate_config_structure(config)

    def test_non_dict_config_raises_error(self):
        """Test that non-dict config raises ConfigurationError."""
        with pytest.raises(ConfigurationError) as exc_info:
            validate_config_structure("not a dict")

        assert "must be a YAML dictionary" in str(exc_info.value)

    def test_missing_plugins_section_raises_error(self):
        """Test that missing plugins section raises error."""
        config = {"server_name": "TestServer"}
        with pytest.raises(ConfigurationError) as exc_info:
            validate_config_structure(config)

        assert "missing 'plugins' section" in str(exc_info.value)

    def test_plugins_not_dict_raises_error(self):
        """Test that plugins section must be a dict."""
        config = {"plugins": "not a dict"}
        with pytest.raises(ConfigurationError) as exc_info:
            validate_config_structure(config)

        assert "must be a dictionary" in str(exc_info.value)


class TestLoadAndValidateConfig:
    """Test load_and_validate_config function."""

    def test_load_valid_config_succeeds(self):
        """Test loading a valid config file."""
        config_data = {
            "server_name": "TestServer",
            "plugins": {
                "ckan": {
                    "enabled": True,
                    "base_url": "https://data.example.com",
                }
            },
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config_data, f)
            temp_path = f.name

        try:
            config = load_and_validate_config(temp_path)
            assert config["server_name"] == "TestServer"
            assert config["plugins"]["ckan"]["enabled"] is True
        finally:
            os.unlink(temp_path)

    def test_load_nonexistent_file_raises_error(self):
        """Test that loading nonexistent file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_and_validate_config("/nonexistent/path/config.yaml")

    def test_load_invalid_yaml_raises_error(self):
        """Test that invalid YAML raises ConfigurationError."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("invalid: yaml: content: [unclosed")
            temp_path = f.name

        try:
            with pytest.raises(ConfigurationError) as exc_info:
                load_and_validate_config(temp_path)
            assert "Invalid YAML" in str(exc_info.value)
        finally:
            os.unlink(temp_path)

    def test_load_empty_file_raises_error(self):
        """Test that empty file raises ConfigurationError."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("")
            temp_path = f.name

        try:
            with pytest.raises(ConfigurationError) as exc_info:
                load_and_validate_config(temp_path)
            assert "empty" in str(exc_info.value).lower()
        finally:
            os.unlink(temp_path)

    def test_load_config_with_multiple_plugins_raises_error(self):
        """Test that config with multiple enabled plugins fails validation."""
        config_data = {
            "plugins": {
                "ckan": {"enabled": True},
                "mbta": {"enabled": True},
            }
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config_data, f)
            temp_path = f.name

        try:
            with pytest.raises(ConfigurationError) as exc_info:
                load_and_validate_config(temp_path)
            assert "Multiple Plugins Enabled" in str(exc_info.value)
        finally:
            os.unlink(temp_path)

    def test_load_config_with_no_plugins_raises_error(self):
        """Test that config with no enabled plugins fails validation."""
        config_data = {
            "plugins": {
                "ckan": {"enabled": False},
            }
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config_data, f)
            temp_path = f.name

        try:
            with pytest.raises(ConfigurationError) as exc_info:
                load_and_validate_config(temp_path)
            assert "No Plugins Enabled" in str(exc_info.value)
        finally:
            os.unlink(temp_path)


class TestGetEnabledPluginConfig:
    """Test get_enabled_plugin_config function."""

    def test_get_single_enabled_plugin_config(self):
        """Test getting config for single enabled plugin."""
        config = {
            "plugins": {
                "ckan": {
                    "enabled": True,
                    "base_url": "https://data.example.com",
                    "city_name": "TestCity",
                }
            }
        }
        plugin_name, plugin_config = get_enabled_plugin_config(config)

        assert plugin_name == "ckan"
        assert plugin_config["base_url"] == "https://data.example.com"
        assert plugin_config["city_name"] == "TestCity"

    def test_get_enabled_plugin_with_multiple_plugins_raises_error(self):
        """Test that multiple enabled plugins raises error."""
        config = {
            "plugins": {
                "ckan": {"enabled": True},
                "mbta": {"enabled": True},
            }
        }
        with pytest.raises(ConfigurationError) as exc_info:
            get_enabled_plugin_config(config)

        assert "Multiple Plugins Enabled" in str(exc_info.value)

    def test_get_enabled_plugin_with_no_plugins_raises_error(self):
        """Test that no enabled plugins raises error."""
        config = {
            "plugins": {
                "ckan": {"enabled": False},
            }
        }
        with pytest.raises(ConfigurationError) as exc_info:
            get_enabled_plugin_config(config)

        assert "No Plugins Enabled" in str(exc_info.value)

    def test_get_enabled_plugin_preserves_all_config_keys(self):
        """Test that all config keys are preserved."""
        config = {
            "plugins": {
                "ckan": {
                    "enabled": True,
                    "base_url": "https://data.example.com",
                    "portal_url": "https://portal.example.com",
                    "city_name": "TestCity",
                    "timeout": 120,
                    "api_key": "test-key-123",
                }
            }
        }
        plugin_name, plugin_config = get_enabled_plugin_config(config)

        assert plugin_name == "ckan"
        assert len(plugin_config) == 6
        assert plugin_config["base_url"] == "https://data.example.com"
        assert plugin_config["portal_url"] == "https://portal.example.com"
        assert plugin_config["city_name"] == "TestCity"
        assert plugin_config["timeout"] == 120
        assert plugin_config["api_key"] == "test-key-123"
        assert plugin_config["enabled"] is True


class TestGetLoggingConfig:
    """Test get_logging_config function."""

    def test_get_logging_config_with_explicit_values(self):
        """Test getting logging config with explicit values."""
        config = {
            "logging": {
                "level": "DEBUG",
                "format": "pretty",
            }
        }
        logging_config = get_logging_config(config)

        assert logging_config["level"] == "DEBUG"
        assert logging_config["format"] == "pretty"

    def test_get_logging_config_with_defaults(self):
        """Test getting logging config with defaults."""
        config = {}
        logging_config = get_logging_config(config)

        assert logging_config["level"] == "INFO"
        assert logging_config["format"] == "json"

    def test_get_logging_config_with_partial_values(self):
        """Test getting logging config with partial values."""
        config = {
            "logging": {
                "level": "WARNING",
            }
        }
        logging_config = get_logging_config(config)

        assert logging_config["level"] == "WARNING"
        assert logging_config["format"] == "json"  # Default

    def test_get_logging_config_with_empty_logging_section(self):
        """Test getting logging config with empty logging section."""
        config = {"logging": {}}
        logging_config = get_logging_config(config)

        assert logging_config["level"] == "INFO"
        assert logging_config["format"] == "json"

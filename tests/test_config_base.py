"""Tests for the shared base plugin configuration schema."""

import pytest
from pydantic import ValidationError, field_validator

from core.config_base import BasePluginConfig


class TestBasePluginConfig:
    """Test BasePluginConfig core fields and validation."""

    def test_defaults_enabled_false(self):
        config = BasePluginConfig(city_name="TestCity")
        assert config.enabled is False
        assert config.city_name == "TestCity"
        assert config.timeout == 30.0

    def test_city_name_required(self):
        with pytest.raises(ValidationError) as exc_info:
            BasePluginConfig()
        assert "city_name" in str(exc_info.value)

    def test_timeout_min_enforced(self):
        with pytest.raises(ValidationError):
            BasePluginConfig(city_name="TestCity", timeout=0.5)

    def test_timeout_max_enforced(self):
        with pytest.raises(ValidationError):
            BasePluginConfig(city_name="TestCity", timeout=301.0)

    def test_timeout_boundary_min_ok(self):
        config = BasePluginConfig(city_name="TestCity", timeout=1.0)
        assert config.timeout == 1.0

    def test_timeout_boundary_max_ok(self):
        config = BasePluginConfig(city_name="TestCity", timeout=300.0)
        assert config.timeout == 300.0

    def test_extra_field_forbidden(self):
        with pytest.raises(ValidationError) as exc_info:
            BasePluginConfig(city_name="TestCity", bogus_field=123)
        assert "bogus_field" in str(exc_info.value)

    def test_enabled_can_be_set_true(self):
        config = BasePluginConfig(city_name="TestCity", enabled=True)
        assert config.enabled is True


class TestValidateUrl:
    """Test the reusable validate_url classmethod."""

    def test_valid_https_url_strips_trailing_slash(self):
        assert (
            BasePluginConfig.validate_url("https://data.example.com/")
            == "https://data.example.com"
        )

    def test_valid_http_url(self):
        assert (
            BasePluginConfig.validate_url("http://localhost:8080")
            == "http://localhost:8080"
        )

    def test_empty_string_rejected(self):
        with pytest.raises(ValueError, match="empty"):
            BasePluginConfig.validate_url("")

    def test_missing_scheme_rejected(self):
        with pytest.raises(ValueError):
            BasePluginConfig.validate_url("data.example.com")

    def test_missing_netloc_rejected(self):
        with pytest.raises(ValueError):
            BasePluginConfig.validate_url("https://")

    def test_invalid_scheme_rejected(self):
        with pytest.raises(ValueError, match="http or https"):
            BasePluginConfig.validate_url("ftp://data.example.com")

    def test_trailing_slash_preserves_path(self):
        url = BasePluginConfig.validate_url("https://data.example.com/api/")
        assert url == "https://data.example.com/api"


class TestSubclassFieldValidatorReuse:
    """Verify subclasses can reuse validate_url via pydantic field_validator."""

    def test_subclass_url_field_validation_works(self):
        class SubConfig(BasePluginConfig):
            base_url: str
            portal_url: str

            _validate_urls = field_validator("base_url", "portal_url")(
                BasePluginConfig.validate_url
            )

        config = SubConfig(
            city_name="TestCity",
            base_url="https://data.example.com/",
            portal_url="https://portal.example.com",
        )
        assert config.base_url == "https://data.example.com"
        assert config.portal_url == "https://portal.example.com"

    def test_subclass_invalid_url_rejected(self):
        class SubConfig(BasePluginConfig):
            base_url: str

            _validate_urls = field_validator("base_url")(BasePluginConfig.validate_url)

        with pytest.raises(ValidationError):
            SubConfig(city_name="TestCity", base_url="not-a-url")

    def test_subclass_extra_field_still_forbidden(self):
        class SubConfig(BasePluginConfig):
            base_url: str

            _validate_urls = field_validator("base_url")(BasePluginConfig.validate_url)

        with pytest.raises(ValidationError):
            SubConfig(
                city_name="TestCity",
                base_url="https://data.example.com",
                extra="bad",
            )

    def test_subclass_timeout_inherited(self):
        class SubConfig(BasePluginConfig):
            base_url: str

            _validate_urls = field_validator("base_url")(BasePluginConfig.validate_url)

        config = SubConfig(
            city_name="TestCity",
            base_url="https://data.example.com",
            timeout=45.0,
        )
        assert config.timeout == 45.0
        assert config.enabled is False

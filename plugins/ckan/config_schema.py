"""Pydantic configuration schema for CKAN plugin."""

from typing import Optional

from pydantic import Field, field_validator

from core.config_base import BasePluginConfig


class CKANPluginConfig(BasePluginConfig):
    """Configuration schema for CKAN plugin.

    This schema validates CKAN plugin configuration from config.yaml.
    It reuses the shared ``enabled``/``city_name``/``timeout`` fields and
    :meth:`BasePluginConfig.validate_url` from the base config, adding only
    the CKAN-specific ``base_url``/``portal_url``/``api_key`` fields.
    """

    base_url: str = Field(
        ..., description="Base URL of CKAN API (e.g., https://data.yourcity.gov)"
    )
    portal_url: str = Field(
        ..., description="Public portal URL (e.g., https://data.yourcity.gov)"
    )
    api_key: Optional[str] = Field(
        None, description="Optional CKAN API key for authenticated requests"
    )

    # Preserve the historical CKAN default of 120 seconds (the base default is
    # 30.0); widen the bound so existing configs that used 120 still validate.
    timeout: float = Field(
        default=120.0, ge=1.0, le=300.0, description="HTTP request timeout in seconds"
    )

    _validate_urls = field_validator("base_url", "portal_url")(
        BasePluginConfig.validate_url
    )
"""Pydantic configuration schema for Opendatasoft plugin."""

from pydantic import Field, field_validator

from core.config_base import BasePluginConfig


class OpendatasoftPluginConfig(BasePluginConfig):
    """Configuration schema for the Opendatasoft plugin.

    This schema validates Opendatasoft plugin configuration from config.yaml.
    It reuses the shared ``enabled``/``city_name``/``timeout`` fields and
    :meth:`BasePluginConfig.validate_url` from the base config, adding only
    the Opendatasoft-specific ``base_url``/``portal_url``/``api_key`` fields.
    """

    base_url: str = Field(
        ..., description="Portal API base URL (e.g., https://data.longbeach.gov)"
    )
    portal_url: str = Field(
        ..., description="Public portal URL (e.g., https://data.longbeach.gov)"
    )
    api_key: str | None = Field(
        default=None,
        description="Optional Opendatasoft API key (public portals need none)",
    )

    _validate_urls = field_validator("base_url", "portal_url")(
        BasePluginConfig.validate_url
    )

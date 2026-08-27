"""Pydantic configuration schema for Socrata plugin."""

from pydantic import Field, field_validator

from core.config_base import BasePluginConfig


class SocrataPluginConfig(BasePluginConfig):
    """Configuration schema for Socrata plugin.

    This schema validates Socrata plugin configuration from config.yaml.
    It reuses the shared ``enabled``/``city_name``/``timeout`` fields and
    :meth:`BasePluginConfig.validate_url` from the base config, adding only
    the Socrata-specific ``base_url``/``portal_url``/``app_token`` fields.
    """

    base_url: str = Field(
        ..., description="Portal URL (e.g., https://data.cityofboston.gov)"
    )
    portal_url: str = Field(
        ..., description="Public portal URL (e.g., https://data.cityofboston.gov)"
    )
    app_token: str = Field(
        ..., description="Socrata app token (required for SODA3 API)"
    )

    @field_validator("app_token")
    @classmethod
    def validate_app_token(cls, v: str) -> str:
        """Validate that app token is non-empty."""
        if not v or not v.strip():
            raise ValueError(
                "Socrata requires a free app token. Register at https://dev.socrata.com/register"
            )
        return v.strip()

    _validate_urls = field_validator("base_url", "portal_url")(
        BasePluginConfig.validate_url
    )

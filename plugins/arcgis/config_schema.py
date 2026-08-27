"""Pydantic configuration schema for ArcGIS Hub plugin."""


from pydantic import Field, field_validator

from core.config_base import BasePluginConfig


class ArcGISPluginConfig(BasePluginConfig):
    """Configuration schema for ArcGIS Hub plugin.

    This schema validates ArcGIS Hub plugin configuration from config.yaml.
    It reuses the shared ``enabled``/``city_name``/``timeout`` fields and
    :meth:`BasePluginConfig.validate_url` from the base config, adding only
    the ArcGIS-specific ``portal_url``/``token`` fields.
    """

    portal_url: str = Field(
        default="https://hub.arcgis.com",
        description="Base URL of ArcGIS Hub portal (e.g., https://hub.arcgis.com)",
    )
    token: str | None = Field(
        None, description="Optional Bearer token for authenticated requests"
    )
    trusted_service_hosts: list[str] = Field(
        default_factory=list,
        description=(
            "Extra hostnames trusted for Feature Service queries, in addition "
            "to *.arcgis.com and the portal host. Needed when a Hub catalog "
            "references services self-hosted on city domains "
            "(e.g. ['maps2.dcgis.dc.gov']). Entries match the exact host or "
            "any of its subdomains."
        ),
    )

    # Preserve the historical ArcGIS default of 120 seconds (the base default
    # is 30.0); widen the bound so existing configs that used 120 still
    # validate.
    timeout: float = Field(
        default=120.0, ge=1.0, le=300.0, description="HTTP request timeout in seconds"
    )

    _validate_urls = field_validator("portal_url")(BasePluginConfig.validate_url)
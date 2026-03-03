"""Pydantic configuration schema for CKAN plugin."""

from typing import Optional
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CKANPluginConfig(BaseModel):
    """Configuration schema for CKAN plugin.

    This schema validates CKAN plugin configuration from config.yaml.
    """

    enabled: bool = Field(default=False, description="Whether plugin is enabled")
    base_url: str = Field(
        ..., description="Base URL of CKAN API (e.g., https://data.yourcity.gov)"
    )
    portal_url: str = Field(
        ..., description="Public portal URL (e.g., https://data.yourcity.gov)"
    )
    city_name: str = Field(..., description="Name of the city/organization")
    timeout: int = Field(
        default=120, ge=1, le=300, description="HTTP request timeout in seconds"
    )
    api_key: Optional[str] = Field(
        None, description="Optional CKAN API key for authenticated requests"
    )

    @field_validator("base_url", "portal_url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        """Validate that URL is well-formed."""
        if not v:
            raise ValueError("URL cannot be empty")
        try:
            result = urlparse(v)
            if not result.scheme or not result.netloc:
                raise ValueError("URL must include scheme (http/https) and hostname")
            if result.scheme not in ("http", "https"):
                raise ValueError("URL scheme must be http or https")
        except Exception as e:
            raise ValueError(f"Invalid URL format: {e}")
        return v.rstrip("/")

    model_config = ConfigDict(extra="forbid")  # Reject unknown fields

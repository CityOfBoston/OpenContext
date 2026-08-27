"""Base configuration schema for OpenContext plugins.

This module defines a reusable pydantic configuration model and a shared URL
validator that plugin-specific config schemas can build on to avoid
duplicating validation logic across providers.
"""

from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field


class BasePluginConfig(BaseModel):
    """Base configuration schema for open data plugins.

    Subclasses add provider-specific fields (URLs, credentials, etc.) and
    reuse :meth:`validate_url` to validate their URL fields, e.g.::

        from pydantic import field_validator
        from core.config_base import BasePluginConfig

        class MyPluginConfig(BasePluginConfig):
            base_url: str = Field(..., description="API base URL")

            _validate_urls = field_validator("base_url", "portal_url")(
                BasePluginConfig.validate_url
            )
    """

    enabled: bool = Field(default=False, description="Whether plugin is enabled")
    city_name: str = Field(..., description="Name of the city/organization")
    timeout: float = Field(
        default=30.0, ge=1.0, le=300.0, description="HTTP request timeout in seconds"
    )

    model_config = ConfigDict(extra="forbid")

    @staticmethod
    def validate_url(v: str) -> str:
        """Validate that a URL is well-formed with an http/https scheme.

        Args:
            v: Raw URL string to validate.

        Returns:
            The validated URL with any trailing slash stripped.

        Raises:
            ValueError: If the URL is empty, missing a scheme/host, or uses a
                scheme other than http/https.
        """
        if not v:
            raise ValueError("URL cannot be empty")
        try:
            result = urlparse(v)
            if not result.scheme or not result.netloc:
                raise ValueError("URL must include scheme (http/https) and hostname")
            if result.scheme not in ("http", "https"):
                raise ValueError("URL scheme must be http or https")
        except ValueError:
            raise
        except Exception as e:
            raise ValueError(f"Invalid URL format: {e}") from e
        return v.rstrip("/")

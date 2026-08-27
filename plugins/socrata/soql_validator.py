"""SoQL validator for Socrata plugin.

Provides security validation for SoQL queries to prevent SQL injection
and destructive operations. Subclasses :class:`BaseQueryValidator` and
adds Socrata-specific checks in :meth:`extra_checks`.
"""

from typing import Optional, Tuple

from core.query_validator import BaseQueryValidator


class SoQLValidator(BaseQueryValidator):
    """Validates SoQL queries for security before execution."""

    # Kept for backwards compatibility with callers/tests that reference
    # SoQLValidator.MAX_SOQL_LENGTH; the base class uses MAX_QUERY_LENGTH.
    MAX_SOQL_LENGTH: int = BaseQueryValidator.MAX_QUERY_LENGTH

    ALLOWED_PREFIXES: tuple[str, ...] = ("SELECT",)

    @classmethod
    def extra_checks(cls, text: str) -> Optional[str]:
        """Run Socrata-specific validation after the shared base checks pass.

        Blocks multiple statements indicated by a semicolon with content
        after it.

        Args:
            text: The stripped query string that passed the base checks.

        Returns:
            An error message string if validation fails, otherwise None.
        """
        if ";" in text:
            parts = text.split(";", 1)
            if len(parts) > 1 and parts[1].strip():
                return "Multiple statements not allowed"
        return None

    @classmethod
    def validate_query(cls, soql: str) -> Tuple[bool, Optional[str]]:
        """Validate SoQL security. Returns (is_valid, error_message).

        Args:
            soql: SoQL query string to validate

        Returns:
            Tuple of (is_valid: bool, error_message: Optional[str])
            If is_valid is True, error_message is None.
            If is_valid is False, error_message contains the reason.
        """
        return super().validate_query(soql)

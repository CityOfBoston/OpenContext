"""Base query validator for OpenContext plugins.

Provides shared security validation for SQL/SoQL-style queries to prevent
SQL injection and destructive operations. Provider-specific validators can
subclass :class:`BaseQueryValidator` and override :meth:`extra_checks` to
add bespoke rules (e.g. UUID format validation for CKAN, semicolon handling
for SoQL).
"""

import re


class BaseQueryValidator:
    """Validates query strings for security before execution.

    Subclasses typically only need to override :meth:`extra_checks` (and
    optionally extend :attr:`DANGEROUS_PATTERNS` or :attr:`ALLOWED_PREFIXES`).
    """

    MAX_QUERY_LENGTH: int = 50000

    FORBIDDEN_KEYWORDS: list[str] = [
        "INSERT",
        "UPDATE",
        "DELETE",
        "DROP",
        "CREATE",
        "ALTER",
        "GRANT",
        "REVOKE",
        "TRUNCATE",
        "EXECUTE",
        "EXEC",
        "CALL",
        "DECLARE",
        "SET",
    ]

    DANGEROUS_PATTERNS: list[tuple[str, str]] = [
        (r";.*(?:DROP|DELETE|INSERT)", "Multiple statements detected"),
        (r"--.*(?:DROP|DELETE)", "Dangerous comment detected"),
        (r";\s*(?:SELECT|DROP|DELETE|INSERT)", "Multiple statements detected"),
        (r"xp_cmdshell", "Command execution detected"),
        (r"into\s+outfile", "File write detected"),
        (r"pg_sleep", "Sleep function detected"),
    ]

    ALLOWED_PREFIXES: tuple[str, ...] = ("SELECT",)

    @classmethod
    def extra_checks(cls, text: str) -> str | None:
        """Run provider-specific validation after the shared checks pass.

        Args:
            text: The stripped query string that already passed the base
                checks (length, forbidden keywords, prefix, dangerous
                patterns).

        Returns:
            An error message string if validation fails, otherwise None.
        """
        return None

    @classmethod
    def validate_query(cls, text: str) -> tuple[bool, str | None]:
        """Validate a query string for security.

        Args:
            text: Query string to validate.

        Returns:
            Tuple of (is_valid, error_message). When ``is_valid`` is True,
            ``error_message`` is None.
        """
        if not text or not isinstance(text, str):
            return False, "Query must be non-empty string"

        stripped = text.strip()
        if len(stripped) > cls.MAX_QUERY_LENGTH:
            return (
                False,
                f"Query too long (max {cls.MAX_QUERY_LENGTH})",
            )

        forbidden = cls.scan_forbidden_keywords(stripped)
        if forbidden:
            return False, forbidden

        upper = stripped.upper().strip()
        if not upper.startswith(cls.ALLOWED_PREFIXES):
            allowed = " or ".join(cls.ALLOWED_PREFIXES)
            return False, f"Only {allowed} queries allowed"

        for pattern, msg in cls.DANGEROUS_PATTERNS:
            if re.search(pattern, stripped, re.IGNORECASE):
                return False, msg

        extra_error = cls.extra_checks(stripped)
        if extra_error:
            return False, extra_error

        return True, None

    @classmethod
    def scan_forbidden_keywords(cls, text: str) -> str | None:
        """Scan text for forbidden SQL keywords (case-insensitive, word-boundary).

        Useful on its own for WHERE-clause-style inputs that do not need to
        start with SELECT (e.g. ArcGIS Feature Service ``where`` params).

        Args:
            text: Text to scan.

        Returns:
            ``f"Forbidden keyword: {keyword}"`` for the first match found,
            otherwise None.
        """
        if not text:
            return None
        for keyword in cls.FORBIDDEN_KEYWORDS:
            if re.search(rf"\b{keyword}\b", text, re.IGNORECASE):
                return f"Forbidden keyword: {keyword}"
        return None

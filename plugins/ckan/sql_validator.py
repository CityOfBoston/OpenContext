"""SQL validator for CKAN plugin.

Provides security validation for SQL queries to prevent SQL injection
and destructive operations. Subclasses :class:`BaseQueryValidator` and
adds CKAN-specific checks (single-statement enforcement via ``sqlparse``
and double-quoted UUID resource-id validation) in :meth:`extra_checks`.
"""

import re
from typing import Optional

import sqlparse

from core.query_validator import BaseQueryValidator


class SQLValidator(BaseQueryValidator):
    """Validates SQL queries for security before execution."""

    # Kept for backwards compatibility with callers/tests that reference
    # SQLValidator.MAX_SQL_LENGTH; the base class uses MAX_QUERY_LENGTH.
    MAX_SQL_LENGTH: int = BaseQueryValidator.MAX_QUERY_LENGTH

    ALLOWED_PREFIXES: tuple[str, ...] = ("SELECT", "WITH")

    @classmethod
    def extra_checks(cls, text: str) -> Optional[str]:
        """Run CKAN-specific validation after the shared base checks pass.

        Enforces single-statement queries and SELECT-only statement type via
        ``sqlparse`` (CTEs starting with WITH are allowed because the prefix
        check already accepted them), and validates that any double-quoted
        36-character resource id looks like a UUID.

        Args:
            text: The stripped query string that passed the base checks.

        Returns:
            An error message string if validation fails, otherwise None.
        """
        # Validate with sqlparse: single statement, SELECT type.
        try:
            parsed = sqlparse.parse(text)
            if len(parsed) != 1:
                return "Multiple statements not allowed"
            statement_type = parsed[0].get_type()
            # sqlparse returns "SELECT" for SELECT statements and CTEs
            # (WITH ... SELECT). If type is None, it might be a CTE; the prefix
            # check already accepted WITH/SELECT above.
            if statement_type is not None and statement_type != "SELECT":
                return "Only SELECT statements allowed"
        except Exception as e:
            return f"SQL parsing error: {str(e)}"

        # Validate resource IDs that are double-quoted 36-char strings.
        resource_ids = re.findall(r'"([a-f0-9-]{36})"', text, re.IGNORECASE)
        uuid_pattern = r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$"
        for rid in resource_ids:
            if not re.match(uuid_pattern, rid, re.IGNORECASE):
                return f"Invalid UUID format: {rid}"

        return None
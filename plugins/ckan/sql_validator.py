"""SQL validator for CKAN plugin.

Provides security validation for SQL queries to prevent SQL injection
and destructive operations.
"""

import re
from typing import Tuple, Optional

import sqlparse


class SQLValidator:
    """Validates SQL queries for security before execution."""

    MAX_SQL_LENGTH = 50000
    FORBIDDEN_KEYWORDS = [
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

    @staticmethod
    def validate_query(sql: str) -> Tuple[bool, Optional[str]]:
        """Validate SQL security. Returns (is_valid, error_message).

        Args:
            sql: SQL query string to validate

        Returns:
            Tuple of (is_valid: bool, error_message: Optional[str])
            If is_valid is True, error_message is None.
            If is_valid is False, error_message contains the reason.
        """
        # 1. Basic checks
        if not sql or not isinstance(sql, str):
            return False, "SQL must be non-empty string"
        sql = sql.strip()
        if len(sql) > SQLValidator.MAX_SQL_LENGTH:
            return (
                False,
                f"SQL too long (max {SQLValidator.MAX_SQL_LENGTH})",
            )

        # 2. Block forbidden keywords (check before SELECT check to get specific error messages)
        for keyword in SQLValidator.FORBIDDEN_KEYWORDS:
            if re.search(rf"\b{keyword}\b", sql, re.IGNORECASE):
                return False, f"Forbidden keyword: {keyword}"

        # 3. Must start with SELECT or WITH (for CTEs)
        sql_upper = sql.upper().strip()
        if not (sql_upper.startswith("SELECT") or sql_upper.startswith("WITH")):
            return False, "Only SELECT queries allowed"

        # 4. Block dangerous patterns
        patterns = [
            (r";.*(?:DROP|DELETE|INSERT)", "Multiple statements detected"),
            (r"--.*(?:DROP|DELETE)", "Dangerous comment detected"),
            (r"xp_cmdshell", "Command execution detected"),
            (r"into\s+outfile", "File write detected"),
            (r"pg_sleep", "Sleep function detected"),
        ]
        for pattern, msg in patterns:
            if re.search(pattern, sql, re.IGNORECASE):
                return False, msg

        # 5. Validate with sqlparse
        try:
            parsed = sqlparse.parse(sql)
            if len(parsed) != 1:
                return False, "Multiple statements not allowed"
            statement_type = parsed[0].get_type()
            # sqlparse returns "SELECT" for SELECT statements and CTEs (WITH ... SELECT)
            # If type is None, it might be a CTE - we already validated it starts with WITH or SELECT above
            if statement_type is not None and statement_type != "SELECT":
                return False, "Only SELECT statements allowed"
        except Exception as e:
            return False, f"SQL parsing error: {str(e)}"

        # 6. Validate resource IDs are UUIDs
        resource_ids = re.findall(r'"([a-f0-9-]{36})"', sql, re.IGNORECASE)
        uuid_pattern = r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$"
        for rid in resource_ids:
            if not re.match(uuid_pattern, rid, re.IGNORECASE):
                return False, f"Invalid UUID format: {rid}"

        return True, None

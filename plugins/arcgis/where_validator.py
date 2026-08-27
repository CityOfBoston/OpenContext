"""WHERE clause validator for ArcGIS Feature Service queries.

Provides light sanitization of SQL WHERE clauses to prevent
injection of destructive operations. Subclasses
:class:`BaseQueryValidator` to reuse the shared forbidden-keyword
scan (which includes GRANT/REVOKE/DECLARE/SET that this plugin
previously lacked) while keeping the ArcGIS-specific public API
``validate(where) -> str``.
"""

import re

from core.query_validator import BaseQueryValidator

# A single-quoted SQL string literal, with '' as the escaped quote.
_QUOTED_LITERAL = re.compile(r"'(?:[^']|'')*'")


class WhereValidator(BaseQueryValidator):
    """Validates WHERE clause strings for Feature Service queries.

    Unlike :meth:`BaseQueryValidator.validate_query`, the ArcGIS Feature
    Service ``where`` parameter is a WHERE-clause fragment (not a full
    SELECT statement), so the prefix and dangerous-pattern checks do not
    apply. Only the forbidden-keyword scan is reused.
    """

    @classmethod
    def validate(cls, where: str) -> str:
        """Validate and sanitize a WHERE clause string.

        Args:
            where: SQL WHERE clause string

        Returns:
            The original WHERE clause if valid, or ``"1=1"`` if empty/None

        Raises:
            ValueError: If the clause contains forbidden SQL keywords
        """
        if not where:
            return "1=1"

        where = where.strip()
        if not where:
            return "1=1"

        # Scan only the structural SQL, not quoted string literals: values
        # like status = 'SET' or call_type = 'Initial Call' are legitimate
        # data, and keywords are only dangerous outside quotes.
        structural = _QUOTED_LITERAL.sub("''", where)
        forbidden = cls.scan_forbidden_keywords(structural)
        if forbidden:
            raise ValueError(f"{forbidden} in WHERE clause")

        return where
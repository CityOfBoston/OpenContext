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

# One ORDER BY term: a plain field name, optionally followed by ASC or DESC.
_ORDER_TERM = re.compile(
    r"^([A-Za-z_][A-Za-z0-9_]{0,63})(?:\s+(ASC|DESC))?$", re.IGNORECASE
)
# Upper bound on ORDER BY terms; real queries use one or two.
_MAX_ORDER_TERMS = 10


class WhereValidator(BaseQueryValidator):
    """Validates WHERE clause strings for Feature Service queries.

    Unlike :meth:`BaseQueryValidator.validate_query`, the ArcGIS Feature
    Service ``where`` parameter is a WHERE-clause fragment (not a full
    SELECT statement), so the prefix and dangerous-pattern checks do not
    apply. Only the forbidden-keyword scan is reused.

    A WHERE/HAVING fragment never needs a subquery or a set operator on
    services with standardized queries (the ArcGIS Online default), so
    ``SELECT``, ``UNION``, ``INTERSECT`` and ``EXCEPT`` are refused as well.
    """

    FORBIDDEN_KEYWORDS: list[str] = [
        *BaseQueryValidator.FORBIDDEN_KEYWORDS,
        "SELECT",
        "UNION",
        "INTERSECT",
        "EXCEPT",
    ]

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
        # ArcGIS escapes a quote by doubling it. A backslash before a quote
        # would end the literal early on a backend that honours C-style
        # escapes, hiding SQL from the scan below, so refuse it outright.
        if "\\'" in where:
            raise ValueError("Backslash-escaped quote in WHERE clause; use '' instead")
        structural = _QUOTED_LITERAL.sub("''", where)
        forbidden = cls.scan_forbidden_keywords(structural)
        if forbidden:
            raise ValueError(f"{forbidden} in WHERE clause")

        return where

    @staticmethod
    def validate_order_by(order_by: str | None) -> str | None:
        """Validate an ``orderByFields`` value.

        Accepts a comma-separated list of plain field names, each optionally
        followed by ``ASC`` or ``DESC`` (e.g. ``"COUNTY, UNITS DESC"``).
        Anything else (expressions, functions, quotes, comments) is refused,
        so the value cannot carry SQL beyond a sort order.

        Args:
            order_by: The requested sort order, or ``None``/empty for none.

        Returns:
            The normalized sort order (``"A, B DESC"``), or ``None``.

        Raises:
            ValueError: If a term is not a field name with an optional
                direction, or there are too many terms.
        """
        if order_by is None:
            return None
        if not isinstance(order_by, str):
            raise ValueError("order_by must be a string")
        if not order_by.strip():
            return None
        terms = [t.strip() for t in order_by.split(",")]
        if len(terms) > _MAX_ORDER_TERMS:
            raise ValueError(f"order_by accepts at most {_MAX_ORDER_TERMS} fields")
        normalized = []
        for term in terms:
            match = _ORDER_TERM.match(term)
            if not match:
                raise ValueError(
                    f"Invalid order_by term {term[:80]!r}: use a field name, "
                    "optionally followed by ASC or DESC"
                )
            field, direction = match.groups()
            normalized.append(f"{field} {direction.upper()}" if direction else field)
        return ", ".join(normalized)

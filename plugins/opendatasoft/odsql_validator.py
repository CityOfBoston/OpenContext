"""ODSQL clause validator for the Opendatasoft plugin.

Opendatasoft's Explore API v2.1 takes ODSQL fragments (``where``, ``select``,
``order_by``) rather than full SQL statements, so the base
:meth:`BaseQueryValidator.validate_query` prefix and dangerous-pattern checks
do not apply. This module reuses the shared forbidden-keyword scan and adds
the ODSQL-specific detail that string literals may be single *or* double
quoted -- keywords inside literals are legitimate data and must not be
rejected.
"""

import re

from core.query_validator import BaseQueryValidator

# A single- or double-quoted ODSQL string literal, with backslash escapes
# (ODSQL escapes quotes with a backslash, not by doubling). One alternation
# matched in a single left-to-right pass, so an apostrophe inside a
# double-quoted literal cannot open a bogus single-quoted span (and vice
# versa).
_QUOTED_LITERAL = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"")


class ODSQLValidator(BaseQueryValidator):
    """Validates ODSQL clause fragments for security before execution."""

    @classmethod
    def strip_literals(cls, clause: str) -> str:
        """Remove quoted string literals from a clause.

        Both single- and double-quoted literals are replaced with an empty
        literal so that only the structural part of the clause is scanned.

        Args:
            clause: Raw ODSQL clause fragment.

        Returns:
            The clause with all quoted literals blanked out.
        """
        return _QUOTED_LITERAL.sub('""', clause)

    @classmethod
    def validate_clause(cls, clause: str, clause_name: str = "where") -> str:
        """Validate an ODSQL clause fragment.

        Args:
            clause: ODSQL fragment (e.g. ``status = "Open" and year > 2020``).
            clause_name: Name of the clause, used in error messages
                (e.g. ``"where"``, ``"select"``, ``"order_by"``).

        Returns:
            The original clause, stripped of surrounding whitespace. An empty
            or ``None`` clause is returned as an empty string.

        Raises:
            ValueError: If the clause exceeds the maximum length or contains
                forbidden SQL keywords outside of quoted literals.
        """
        if not clause:
            return ""

        clause = clause.strip()
        if not clause:
            return ""

        if len(clause) > cls.MAX_QUERY_LENGTH:
            raise ValueError(
                f"{clause_name} clause too long (max {cls.MAX_QUERY_LENGTH})"
            )

        # Scan only the structural ODSQL, not quoted string literals: values
        # like status = "SET" or name = 'Grant Park' are legitimate data, and
        # keywords are only dangerous outside quotes.
        forbidden = cls.scan_forbidden_keywords(cls.strip_literals(clause))
        if forbidden:
            raise ValueError(f"{forbidden} in {clause_name} clause")

        return clause

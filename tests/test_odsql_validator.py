"""Tests for the ODSQL clause validator used by the Opendatasoft plugin."""

import pytest

from plugins.opendatasoft.odsql_validator import ODSQLValidator


class TestValidClauses:
    """Clauses that must be accepted."""

    @pytest.mark.parametrize(
        "clause",
        [
            'status = "Open"',
            "year > 2020 and month <= 6",
            'search("noise complaint")',
            'neighborhood like "North*"',
            "count(*) as total",
            "date DESC",
            "field_a, field_b, avg(amount) as avg_amount",
            "location is not null",
        ],
    )
    def test_valid_clause_returned_unchanged(self, clause):
        """Legitimate ODSQL fragments pass through unchanged."""
        assert ODSQLValidator.validate_clause(clause) == clause

    def test_empty_clause_returns_empty_string(self):
        """Empty/None clauses are normalized to an empty string."""
        assert ODSQLValidator.validate_clause("") == ""
        assert ODSQLValidator.validate_clause(None) == ""
        assert ODSQLValidator.validate_clause("   ") == ""

    def test_clause_is_stripped(self):
        """Surrounding whitespace is stripped."""
        assert (
            ODSQLValidator.validate_clause('  status = "Open"  ') == 'status = "Open"'
        )

    @pytest.mark.parametrize(
        "clause",
        [
            'status = "SET"',
            'description = "DROP the mic"',
            'category = "Update requested"',
            'search("delete my record")',
            'notes = "EXEC summary"',
        ],
    )
    def test_keywords_inside_double_quoted_literals_allowed(self, clause):
        """Forbidden keywords inside double-quoted literals are data, not SQL."""
        assert ODSQLValidator.validate_clause(clause) == clause

    @pytest.mark.parametrize(
        "clause",
        [
            "status = 'SET'",
            "title = 'DROP TABLE park'",
            "note = 'insert coin'",
        ],
    )
    def test_keywords_inside_single_quoted_literals_allowed(self, clause):
        """Forbidden keywords inside single-quoted literals are allowed too."""
        assert ODSQLValidator.validate_clause(clause) == clause

    def test_escaped_quote_inside_literal_allowed(self):
        """Escaped double quotes do not end the literal prematurely."""
        clause = 'name = "the \\"drop\\" zone"'
        assert ODSQLValidator.validate_clause(clause) == clause


class TestRejectedClauses:
    """Clauses that must be rejected."""

    @pytest.mark.parametrize(
        "clause",
        [
            "status = 1; DROP TABLE users",
            "1=1 delete from records",
            "year > 2020 or insert into x values (1)",
            "field = 1; update t set a = 2",
            "exec xp_cmdshell",
        ],
    )
    def test_forbidden_keywords_rejected(self, clause):
        """Structural forbidden keywords raise ValueError."""
        with pytest.raises(ValueError, match="Forbidden keyword"):
            ODSQLValidator.validate_clause(clause)

    def test_error_message_includes_clause_name(self):
        """The clause name appears in the error message."""
        with pytest.raises(ValueError, match="select clause"):
            ODSQLValidator.validate_clause("drop table x", "select")

    def test_keyword_after_closing_quote_rejected(self):
        """A keyword outside the literal is still caught."""
        with pytest.raises(ValueError, match="Forbidden keyword"):
            ODSQLValidator.validate_clause('status = "Open"; DROP TABLE t')

    def test_clause_too_long_rejected(self):
        """Clauses beyond MAX_QUERY_LENGTH are rejected."""
        clause = "a" * (ODSQLValidator.MAX_QUERY_LENGTH + 1)
        with pytest.raises(ValueError, match="too long"):
            ODSQLValidator.validate_clause(clause)


class TestStripLiterals:
    """Test the literal-stripping helper."""

    def test_strips_both_quote_styles(self):
        """Both single- and double-quoted literals are blanked out."""
        stripped = ODSQLValidator.strip_literals("a = 'DROP' and b = \"DELETE\"")
        assert "DROP" not in stripped
        assert "DELETE" not in stripped
        assert "a =" in stripped and "b =" in stripped


class TestStripLiteralsSinglePass:
    """An apostrophe inside a double-quoted literal must not open a bogus
    single-quoted span that hides structural keywords (review finding)."""

    def test_keyword_after_apostrophe_in_double_quotes_rejected(self):
        with pytest.raises(ValueError, match="Forbidden keyword"):
            ODSQLValidator.validate_clause(
                'x = "a\'" and DROP TABLE t and y = "\'b"', "where"
            )

    def test_apostrophes_inside_double_quotes_still_allowed(self):
        clause = 'name = "O\'Brien" and note = "won\'t DELETE me"'
        assert ODSQLValidator.validate_clause(clause, "where") == clause

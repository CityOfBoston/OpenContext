"""Tests for the shared base query validator."""

import pytest

from core.query_validator import BaseQueryValidator


class TestValidQueries:
    """Test that valid SELECT queries pass validation."""

    def test_simple_select_passes(self):
        is_valid, error = BaseQueryValidator.validate_query(
            'SELECT * FROM "abc-123-def-456-ghi-789-012-345-678-901"'
        )
        assert is_valid is True
        assert error is None

    def test_select_with_where_passes(self):
        is_valid, error = BaseQueryValidator.validate_query(
            "SELECT * FROM \"abc-123\" WHERE status = 'Open'"
        )
        assert is_valid is True
        assert error is None

    def test_select_with_leading_whitespace_passes(self):
        is_valid, error = BaseQueryValidator.validate_query("   SELECT * FROM t   ")
        assert is_valid is True
        assert error is None

    def test_select_with_limit_passes(self):
        is_valid, error = BaseQueryValidator.validate_query("SELECT * FROM t LIMIT 10")
        assert is_valid is True
        assert error is None


class TestInvalidQueries:
    """Test that invalid queries fail validation."""

    def test_empty_string_fails(self):
        is_valid, error = BaseQueryValidator.validate_query("")
        assert is_valid is False
        assert "non-empty" in error

    def test_none_fails(self):
        is_valid, error = BaseQueryValidator.validate_query(None)
        assert is_valid is False
        assert "non-empty" in error

    def test_non_string_fails(self):
        is_valid, error = BaseQueryValidator.validate_query(123)
        assert is_valid is False
        assert "non-empty" in error

    def test_too_long_fails(self):
        long_query = "SELECT * " + "x" * (BaseQueryValidator.MAX_QUERY_LENGTH + 1)
        is_valid, error = BaseQueryValidator.validate_query(long_query)
        assert is_valid is False
        assert "too long" in error

    @pytest.mark.parametrize(
        "keyword",
        [
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
        ],
    )
    def test_forbidden_keywords_caught(self, keyword):
        is_valid, error = BaseQueryValidator.validate_query(
            f"{keyword} something FROM t"
        )
        assert is_valid is False
        assert keyword in error

    def test_forbidden_keyword_case_insensitive(self):
        is_valid, error = BaseQueryValidator.validate_query("delete from t")
        assert is_valid is False
        assert "DELETE" in error

    def test_forbidden_keyword_word_boundary(self):
        # 'DELETED' should not match the 'DELETE' keyword because of \b
        is_valid, _ = BaseQueryValidator.validate_query(
            "SELECT * FROM t WHERE status = 'DELETED'"
        )
        assert is_valid is True

    def test_non_select_prefix_fails(self):
        is_valid, error = BaseQueryValidator.validate_query("WITH x AS (SELECT 1)")
        assert is_valid is False
        assert "SELECT" in error

    def test_dangerous_comment_pattern_rejected(self):
        # Forbidden-keyword scan runs first and catches DROP; the dangerous
        # comment pattern is a secondary backstop. Either is an acceptable
        # rejection reason here.
        is_valid, error = BaseQueryValidator.validate_query(
            "SELECT * FROM t -- DROP TABLE x"
        )
        assert is_valid is False
        assert error is not None

    def test_multiple_statements_pattern_rejected(self):
        is_valid, error = BaseQueryValidator.validate_query(
            "SELECT * FROM t; DROP TABLE x"
        )
        assert is_valid is False
        assert error is not None

    def test_dangerous_pattern_semicolon_select_caught(self):
        # This query has no forbidden keyword but triggers the multiple
        # statements pattern (semicolon followed by SELECT).
        is_valid, error = BaseQueryValidator.validate_query(
            "SELECT * FROM t; SELECT * FROM u"
        )
        assert is_valid is False
        assert "Multiple statements" in error

    def test_xp_cmdshell_pattern_fails(self):
        is_valid, _error = BaseQueryValidator.validate_query(
            "SELECT * FROM t WHERE x = xp_cmdshell('dir')"
        )
        assert is_valid is False

    def test_pg_sleep_pattern_fails(self):
        is_valid, _error = BaseQueryValidator.validate_query(
            "SELECT * FROM t WHERE pg_sleep(1) = 1"
        )
        assert is_valid is False

    def test_into_outfile_pattern_fails(self):
        is_valid, _error = BaseQueryValidator.validate_query(
            "SELECT * FROM t INTO OUTFILE '/tmp/x'"
        )
        assert is_valid is False


class TestScanForbiddenKeywords:
    """Test scan_forbidden_keywords standalone use (ArcGIS where-clause case)."""

    def test_returns_none_for_clean_text(self):
        assert BaseQueryValidator.scan_forbidden_keywords("status = 'Open'") is None

    def test_returns_none_for_empty(self):
        assert BaseQueryValidator.scan_forbidden_keywords("") is None

    def test_detects_drop(self):
        result = BaseQueryValidator.scan_forbidden_keywords("DROP TABLE x")
        assert result is not None
        assert "DROP" in result

    def test_detects_delete_case_insensitive(self):
        result = BaseQueryValidator.scan_forbidden_keywords("delete from t")
        assert result is not None
        assert "DELETE" in result


class TestExtraChecksOverride:
    """Test that subclasses can extend validation via extra_checks."""

    def test_extra_checks_default_passes(self):
        # Base implementation returns None (no extra error)
        assert BaseQueryValidator.extra_checks("SELECT * FROM t") is None

    def test_subclass_extra_checks_invoked(self):
        class StrictValidator(BaseQueryValidator):
            @classmethod
            def extra_checks(cls, text):
                if "FORBIDDEN_TOKEN" in text.upper():
                    return "Custom token not allowed"
                return None

        is_valid, error = StrictValidator.validate_query(
            "SELECT * FROM t WHERE x = 'FORBIDDEN_TOKEN'"
        )
        assert is_valid is False
        assert "Custom token" in error

    def test_subclass_extra_checks_passes_when_clean(self):
        class StrictValidator(BaseQueryValidator):
            @classmethod
            def extra_checks(cls, text):
                if "FORBIDDEN_TOKEN" in text.upper():
                    return "Custom token not allowed"
                return None

        is_valid, error = StrictValidator.validate_query("SELECT * FROM t")
        assert is_valid is True
        assert error is None

    def test_subclass_can_extend_allowed_prefixes(self):
        class CTEValidator(BaseQueryValidator):
            ALLOWED_PREFIXES = ("SELECT", "WITH")

        is_valid, error = CTEValidator.validate_query(
            "WITH x AS (SELECT 1) SELECT * FROM x"
        )
        assert is_valid is True
        assert error is None

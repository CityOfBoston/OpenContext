"""Security tests for the ArcGIS WHERE/HAVING guard (subqueries and set operators)."""

from unittest.mock import AsyncMock, patch

import pytest

from plugins.arcgis.plugin import ArcGISPlugin
from plugins.arcgis.where_validator import WhereValidator

pytestmark = pytest.mark.security

SUBQUERIES = [
    "1=1 UNION SELECT password FROM users",
    "1=1 union all select * from t",
    "OBJECTID IN (SELECT OBJECTID FROM other)",
    "x = 1 INTERSECT SELECT x FROM t",
    "x = 1 EXCEPT SELECT x FROM t",
    "x = 1\nUNION\nSELECT 1",
    "(SELECT 1) = 1",
    # Under C-style escaping the literal ends at \' and the UNION runs.
    "x = 'a\\'' UNION SELECT pw FROM t --'",
]


class TestWhereValidatorRefusesSubqueries:
    @pytest.mark.parametrize("bad", SUBQUERIES)
    def test_rejects_set_operators_and_select(self, bad):
        with pytest.raises(ValueError, match="Forbidden keyword|Backslash"):
            WhereValidator.validate(bad)

    def test_rejects_backslash_escaped_quote(self):
        with pytest.raises(ValueError, match="Backslash-escaped quote"):
            WhereValidator.validate("name = 'it\\'s'")

    def test_backslash_without_quote_is_data(self):
        assert WhereValidator.validate("path = 'C:\\temp'") == "path = 'C:\\temp'"

    @pytest.mark.parametrize(
        "ok",
        [
            "status = 'SELECT'",
            "note = 'trade union local 26'",
            "name = 'O''Brien EXCEPT'",
            "STATE IN ('MA', 'RI', 'CT')",
            "COUNTY_FIPS IN (25025, 25017)",
            "SELECTED_FLAG = 1",
            "UNION_NAME LIKE 'SEIU%'",
            "EXCEPTION_CODE IS NULL",
        ],
    )
    def test_keeps_literals_lists_and_lookalike_fields(self, ok):
        assert WhereValidator.validate(ok) == ok


@pytest.fixture
def plugin():
    p = ArcGISPlugin(
        {"portal_url": "https://hub.arcgis.com", "city_name": "T", "timeout": 30}
    )
    p.feature_client = AsyncMock()
    return p


class TestSubqueriesBlockedBeforeRequest:
    @pytest.mark.parametrize("bad", SUBQUERIES)
    async def test_query_data_where(self, plugin, bad):
        with patch.object(plugin, "get_dataset", new_callable=AsyncMock) as get_ds:
            result = await plugin.execute_tool(
                "query_data", {"dataset_id": "abc123", "where": bad}
            )
        assert result.success is False
        get_ds.assert_not_called()
        plugin.feature_client.get.assert_not_called()

    @pytest.mark.parametrize("bad", SUBQUERIES)
    async def test_aggregate_data_having(self, plugin, bad):
        with patch.object(plugin, "get_dataset", new_callable=AsyncMock) as get_ds:
            result = await plugin.execute_tool(
                "aggregate_data",
                {
                    "dataset_id": "abc123",
                    "statistics": [{"type": "count"}],
                    "having": f"COUNT(*) > 1 AND {bad}",
                },
            )
        assert result.success is False
        get_ds.assert_not_called()
        plugin.feature_client.get.assert_not_called()

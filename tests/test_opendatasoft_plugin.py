"""Comprehensive tests for the Opendatasoft plugin.

These tests verify plugin initialization, tool execution, Explore API v2.1
interactions, ODSQL validation, error handling, and data formatting. All
network access is mocked; no live portal is contacted.
"""

from typing import ClassVar
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

from plugins.opendatasoft.plugin import OpendatasoftPlugin

ODS_CONFIG = {
    "base_url": "https://data.longbeach.gov",
    "portal_url": "https://data.longbeach.gov",
    "city_name": "Long Beach",
    "timeout": 30.0,
}


def _mock_response(json_data):
    """Create a mock GET response returning ``json_data``."""
    mock = Mock()
    mock.json.return_value = json_data
    mock.raise_for_status = Mock()
    return mock


def _initialized_plugin(config=None, get_side_effect=None, get_return=None):
    """Create a plugin with a mocked HTTP client attached, already initialized."""
    plugin = OpendatasoftPlugin(dict(config or ODS_CONFIG))
    mock_client = AsyncMock()
    if get_side_effect is not None:
        mock_client.get = AsyncMock(side_effect=get_side_effect)
    else:
        mock_client.get = AsyncMock(
            return_value=_mock_response(get_return or {"total_count": 0, "results": []})
        )
    plugin.client = mock_client
    plugin._initialized = True
    return plugin, mock_client


class TestPluginInitialization:
    """Test plugin initialization."""

    @pytest.fixture
    def ods_config(self):
        """Standard Opendatasoft plugin configuration."""
        return dict(ODS_CONFIG)

    @pytest.mark.asyncio
    async def test_plugin_initialization_succeeds(self, ods_config):
        """Test that plugin initialization succeeds with valid config."""
        plugin = OpendatasoftPlugin(ods_config)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(
                return_value=_mock_response({"total_count": 1, "results": []})
            )
            mock_client_class.return_value = mock_client

            result = await plugin.initialize()

            assert result is True
            assert plugin.is_initialized is True
            assert plugin.client is not None
            assert mock_client.get.called

    @pytest.mark.asyncio
    async def test_initialization_uses_explore_api_base_url(self, ods_config):
        """Test that the HTTP client targets the Explore v2.1 base path."""
        plugin = OpendatasoftPlugin(ods_config)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=_mock_response({"results": []}))
            mock_client_class.return_value = mock_client

            await plugin.initialize()

            kwargs = mock_client_class.call_args[1]
            assert kwargs["base_url"] == ("https://data.longbeach.gov/api/explore/v2.1")
            assert kwargs["timeout"] == 30.0

    @pytest.mark.asyncio
    async def test_initialization_without_api_key_sends_no_auth_header(
        self, ods_config
    ):
        """Test that no Authorization header is set for public portals."""
        plugin = OpendatasoftPlugin(ods_config)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=_mock_response({"results": []}))
            mock_client_class.return_value = mock_client

            await plugin.initialize()

            assert mock_client_class.call_args[1]["headers"] == {}

    @pytest.mark.asyncio
    async def test_initialization_with_api_key_sets_auth_header(self, ods_config):
        """Test that an api_key produces the apikey Authorization header."""
        ods_config["api_key"] = "secret-key"
        plugin = OpendatasoftPlugin(ods_config)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=_mock_response({"results": []}))
            mock_client_class.return_value = mock_client

            await plugin.initialize()

            headers = mock_client_class.call_args[1]["headers"]
            assert headers["Authorization"] == "apikey secret-key"

    @pytest.mark.asyncio
    async def test_initialization_fails_on_http_error(self, ods_config):
        """Test that initialization returns False when the portal errors."""
        plugin = OpendatasoftPlugin(ods_config)

        error_response = Mock()
        error_response.status_code = 500
        error_response.json.return_value = {"message": "boom"}
        error_response.text = "boom"
        error_response.raise_for_status = Mock(
            side_effect=httpx.HTTPStatusError(
                "Server Error", request=Mock(), response=error_response
            )
        )

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=error_response)
            mock_client_class.return_value = mock_client

            result = await plugin.initialize()

            assert result is False
            assert plugin.is_initialized is False

    def test_config_rejects_unknown_keys(self, ods_config):
        """Test that unknown config keys are rejected by the schema."""
        ods_config["not_a_field"] = "x"
        with pytest.raises(Exception):
            OpendatasoftPlugin(ods_config)

    def test_config_rejects_invalid_url(self, ods_config):
        """Test that a malformed base_url is rejected."""
        ods_config["base_url"] = "not-a-url"
        with pytest.raises(Exception):
            OpendatasoftPlugin(ods_config)

    def test_config_strips_trailing_slash(self):
        """Test that URL validation strips trailing slashes."""
        plugin = OpendatasoftPlugin(
            {
                "base_url": "https://data.longbeach.gov/",
                "portal_url": "https://data.longbeach.gov/",
                "city_name": "Long Beach",
            }
        )
        assert plugin.plugin_config.base_url == "https://data.longbeach.gov"
        assert plugin.plugin_config.timeout == 30.0

    @pytest.mark.asyncio
    async def test_shutdown_closes_tracked_clients(self, ods_config):
        """Test that shutdown closes the tracked HTTP client."""
        plugin = OpendatasoftPlugin(ods_config)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=_mock_response({"results": []}))
            mock_client_class.return_value = mock_client

            await plugin.initialize()
            assert len(plugin._clients) == 1

            await plugin.shutdown()

            assert mock_client.aclose.call_count == 1
            assert plugin._clients == []
            assert plugin.is_initialized is False

    @pytest.mark.asyncio
    async def test_call_api_raises_when_not_initialized(self, ods_config):
        """Test that calling the API before initialize raises RuntimeError."""
        plugin = OpendatasoftPlugin(ods_config)
        with pytest.raises(RuntimeError, match="not initialized"):
            await plugin._call_api("/catalog/datasets")


class TestGetTools:
    """Test get_tools method."""

    def test_get_tools_returns_all_six_tools(self):
        """Test that get_tools returns all 6 expected tools."""
        plugin = OpendatasoftPlugin(dict(ODS_CONFIG))
        tools = plugin.get_tools()

        assert len(tools) == 6
        tool_names = [t.name for t in tools]
        assert "search_datasets" in tool_names
        assert "get_dataset" in tool_names
        assert "get_schema" in tool_names
        assert "query_data" in tool_names
        assert "aggregate_data" in tool_names
        assert "list_categories" in tool_names

    def test_get_tools_includes_city_name_in_descriptions(self):
        """Test that tool descriptions mention the city name."""
        plugin = OpendatasoftPlugin(dict(ODS_CONFIG))
        for tool in plugin.get_tools():
            assert "Long Beach" in tool.description

    def test_get_tools_declare_required_arguments(self):
        """Test that input schemas declare the expected required args."""
        plugin = OpendatasoftPlugin(dict(ODS_CONFIG))
        tools = {t.name: t for t in plugin.get_tools()}

        assert tools["search_datasets"].input_schema["required"] == ["query"]
        assert tools["get_dataset"].input_schema["required"] == ["dataset_id"]
        assert tools["get_schema"].input_schema["required"] == ["dataset_id"]
        assert tools["query_data"].input_schema["required"] == ["dataset_id"]
        assert tools["aggregate_data"].input_schema["required"] == [
            "dataset_id",
            "metrics",
        ]
        assert "required" not in tools["list_categories"].input_schema

    def test_query_data_schema_has_odsql_properties(self):
        """Test that query_data exposes the ODSQL clause parameters."""
        plugin = OpendatasoftPlugin(dict(ODS_CONFIG))
        tool = next(t for t in plugin.get_tools() if t.name == "query_data")
        props = tool.input_schema["properties"]

        assert set(["dataset_id", "where", "select", "order_by", "limit"]).issubset(
            props
        )
        assert props["limit"]["default"] == 100

    def test_tool_handlers_match_get_tools(self):
        """Test that every declared tool has a registered handler."""
        plugin = OpendatasoftPlugin(dict(ODS_CONFIG))
        handlers = plugin.tool_handlers()
        assert set(handlers) == {t.name for t in plugin.get_tools()}


class TestRequiredArgumentEnforcement:
    """Test dispatch-level required argument enforcement."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "tool_name,arguments,missing",
        [
            ("search_datasets", {}, "query"),
            ("get_dataset", {}, "dataset_id"),
            ("get_schema", {}, "dataset_id"),
            ("query_data", {}, "dataset_id"),
            ("aggregate_data", {"metrics": {"c": "count(*)"}}, "dataset_id"),
            ("aggregate_data", {"dataset_id": "d"}, "metrics"),
        ],
    )
    async def test_missing_required_arg_returns_error(
        self, tool_name, arguments, missing
    ):
        """Test that missing required arguments fail before the handler runs."""
        plugin, mock_client = _initialized_plugin()

        result = await plugin.execute_tool(tool_name, arguments)

        assert result.success is False
        assert result.error_message == f"{missing} is required"
        assert mock_client.get.called is False

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        """Test that an unknown tool name returns an unsuccessful result."""
        plugin, _ = _initialized_plugin()
        result = await plugin.execute_tool("nope", {})
        assert result.success is False
        assert "Unknown tool" in result.error_message


class TestSearchDatasets:
    """Test search_datasets tool and contract method."""

    SEARCH_RESPONSE = {
        "total_count": 1,
        "results": [
            {
                "dataset_id": "police-calls",
                "metas": {
                    "default": {
                        "title": "Police Calls for Service",
                        "description": "Calls received by dispatch. " + "x" * 200,
                        "theme": ["Public Safety"],
                        "records_count": 4321,
                    }
                },
            }
        ],
    }

    @pytest.mark.asyncio
    async def test_search_datasets_uses_odsql_search_where(self):
        """Test that the search term is wrapped in an ODSQL search() call."""
        plugin, mock_client = _initialized_plugin(get_return=self.SEARCH_RESPONSE)

        await plugin.search_datasets("crime", limit=5)

        path, kwargs = mock_client.get.call_args[0][0], mock_client.get.call_args[1]
        assert path == "/catalog/datasets"
        assert kwargs["params"] == {"where": 'search("crime")', "limit": 5}

    @pytest.mark.asyncio
    async def test_search_datasets_escapes_embedded_quotes(self):
        """Test that embedded double quotes cannot break out of the literal."""
        plugin, mock_client = _initialized_plugin(get_return=self.SEARCH_RESPONSE)

        await plugin.search_datasets('bad") or drop("')

        params = mock_client.get.call_args[1]["params"]
        assert params["where"] == 'search("bad\\") or drop(\\"")'

    @pytest.mark.asyncio
    async def test_search_datasets_tool_formats_results(self):
        """Test that search results are formatted with ID, theme and links."""
        plugin, _ = _initialized_plugin(get_return=self.SEARCH_RESPONSE)

        result = await plugin.execute_tool("search_datasets", {"query": "police"})

        assert result.success is True
        text = result.content[0]["text"]
        assert "Found 1 dataset(s) in Long Beach's open data portal" in text
        assert "Police Calls for Service" in text
        assert "ID: police-calls" in text
        assert "Theme: Public Safety" in text
        assert "Records: 4321" in text
        assert "https://data.longbeach.gov/explore/dataset/police-calls/" in text
        assert "…[truncated" in text  # description truncated
        assert "Use the get_dataset tool" in text

    @pytest.mark.asyncio
    async def test_search_datasets_empty_results_message(self):
        """Test the empty-results message."""
        plugin, _ = _initialized_plugin(get_return={"total_count": 0, "results": []})
        result = await plugin.execute_tool("search_datasets", {"query": "zzz"})
        assert (
            "No datasets found in Long Beach's open data portal."
            in (result.content[0]["text"])
        )

    @pytest.mark.asyncio
    async def test_search_datasets_handles_missing_metas(self):
        """Test defensive handling of catalog entries without a metas block."""
        plugin, _ = _initialized_plugin(
            get_return={"total_count": 1, "results": [{"dataset_id": "bare"}]}
        )
        result = await plugin.execute_tool("search_datasets", {"query": "bare"})
        text = result.content[0]["text"]
        assert "Untitled" in text
        assert "No description" in text
        assert "ID: bare" in text


class TestGetDatasetAndSchema:
    """Test get_dataset and get_schema tools."""

    DATASET_RESPONSE = {
        "dataset_id": "police-calls",
        "metas": {
            "default": {
                "title": "Police Calls for Service",
                "description": "Dispatch calls",
                "theme": ["Public Safety"],
                "keyword": ["police", "911"],
                "records_count": 4321,
                "modified": "2026-01-15",
            }
        },
        "fields": [
            {
                "name": "call_type",
                "type": "text",
                "label": "Call Type",
                "description": "Type of call",
            },
            {"name": "received", "type": "datetime", "label": "received"},
        ],
    }

    @pytest.mark.asyncio
    async def test_get_dataset_calls_catalog_endpoint(self):
        """Test that get_dataset hits the dataset detail endpoint."""
        plugin, mock_client = _initialized_plugin(get_return=self.DATASET_RESPONSE)

        await plugin.get_dataset("police-calls")

        assert mock_client.get.call_args[0][0] == "/catalog/datasets/police-calls"

    @pytest.mark.asyncio
    async def test_get_dataset_tool_formats_metadata(self):
        """Test that dataset metadata is fully formatted."""
        plugin, _ = _initialized_plugin(get_return=self.DATASET_RESPONSE)

        result = await plugin.execute_tool(
            "get_dataset", {"dataset_id": "police-calls"}
        )

        text = result.content[0]["text"]
        assert result.success is True
        assert "Dataset: Police Calls for Service" in text
        assert "ID: police-calls" in text
        assert "Description: Dispatch calls" in text
        assert "Records: 4321" in text
        assert "Last modified: 2026-01-15" in text
        assert "Theme: Public Safety" in text
        assert "Keywords: police, 911" in text
        assert (
            "Portal URL: https://data.longbeach.gov/explore/dataset/police-calls/"
            in text
        )
        assert "Use the get_schema" in text

    @pytest.mark.asyncio
    async def test_get_schema_tool_formats_fields(self):
        """Test that the field list is formatted like the other plugins."""
        plugin, _ = _initialized_plugin(get_return=self.DATASET_RESPONSE)

        result = await plugin.execute_tool("get_schema", {"dataset_id": "police-calls"})

        text = result.content[0]["text"]
        assert result.success is True
        assert "Schema fields (use these for ODSQL queries):" in text
        assert "• call_type (text)" in text
        assert "Label: Call Type" in text
        assert "Type of call" in text
        assert "• received (datetime)" in text
        # Label identical to the name is not repeated
        assert "Label: received" not in text

    @pytest.mark.asyncio
    async def test_get_schema_empty_fields_message(self):
        """Test the empty-schema message."""
        plugin, _ = _initialized_plugin(get_return={"dataset_id": "x", "fields": []})
        result = await plugin.execute_tool("get_schema", {"dataset_id": "x"})
        assert "No schema information available." in result.content[0]["text"]

    @pytest.mark.asyncio
    async def test_http_404_produces_descriptive_error(self):
        """Test that HTTP errors are translated into a portal-aware message."""
        error_response = Mock()
        error_response.status_code = 404
        error_response.json.return_value = {"message": "Dataset not found"}
        error_response.text = "Dataset not found"
        error_response.raise_for_status = Mock(
            side_effect=httpx.HTTPStatusError(
                "Not Found", request=Mock(), response=error_response
            )
        )
        plugin, _ = _initialized_plugin(get_side_effect=[error_response])

        result = await plugin.execute_tool("get_dataset", {"dataset_id": "missing"})

        assert result.success is False
        assert "Dataset not found" in result.error_message
        assert "Long Beach" in result.error_message
        assert "404" in result.error_message


class TestQueryData:
    """Test the query_data tool and DataPlugin contract method."""

    RECORDS_RESPONSE = {
        "total_count": 250,
        "results": [{"call_type": "Noise", "received": "2026-01-01"}],
    }

    @pytest.mark.asyncio
    async def test_query_data_sends_validated_clauses(self):
        """Test that where/select/order_by are forwarded as ODSQL params."""
        plugin, mock_client = _initialized_plugin(get_return=self.RECORDS_RESPONSE)

        result = await plugin.execute_tool(
            "query_data",
            {
                "dataset_id": "police-calls",
                "where": 'call_type = "Noise"',
                "select": "call_type, received",
                "order_by": "received DESC",
                "limit": 25,
            },
        )

        assert result.success is True
        assert (
            mock_client.get.call_args[0][0] == "/catalog/datasets/police-calls/records"
        )
        assert mock_client.get.call_args[1]["params"] == {
            "limit": 25,
            "where": 'call_type = "Noise"',
            "select": "call_type, received",
            "order_by": "received DESC",
        }

    @pytest.mark.asyncio
    async def test_query_data_defaults_and_caps_limit(self):
        """Test that limit defaults to 100 and is capped at 100."""
        plugin, mock_client = _initialized_plugin(get_return=self.RECORDS_RESPONSE)

        await plugin.execute_tool("query_data", {"dataset_id": "d"})
        assert mock_client.get.call_args[1]["params"]["limit"] == 100

        await plugin.execute_tool("query_data", {"dataset_id": "d", "limit": 5000})
        assert mock_client.get.call_args[1]["params"]["limit"] == 100

    @pytest.mark.asyncio
    async def test_query_data_formats_records_with_total_count(self):
        """Test the record header mentions total_count when it is larger."""
        plugin, _ = _initialized_plugin(get_return=self.RECORDS_RESPONSE)

        result = await plugin.execute_tool("query_data", {"dataset_id": "d"})

        text = result.content[0]["text"]
        assert "Found 1 record(s) (of 250 matching record(s)):" in text
        assert "call_type: Noise" in text

    @pytest.mark.asyncio
    async def test_query_data_displays_all_fetched_records(self):
        """Every fetched record is rendered (display cap = fetch cap), so no
        transfer is wasted on records the caller never sees."""
        records = [{"i": i} for i in range(25)]
        plugin, _ = _initialized_plugin(
            get_return={"total_count": 25, "results": records}
        )

        result = await plugin.execute_tool("query_data", {"dataset_id": "d"})

        text = result.content[0]["text"]
        assert "Record 25:" in text
        assert "more record(s)" not in text

    @pytest.mark.asyncio
    async def test_query_data_empty_results_message(self):
        """Test the empty-records message."""
        plugin, _ = _initialized_plugin(get_return={"total_count": 0, "results": []})
        result = await plugin.execute_tool("query_data", {"dataset_id": "d"})
        assert "No records found matching the query." in result.content[0]["text"]

    @pytest.mark.asyncio
    async def test_query_data_rejects_malicious_where(self):
        """Test that a forbidden keyword in where fails the tool call."""
        plugin, mock_client = _initialized_plugin()

        result = await plugin.execute_tool(
            "query_data",
            {"dataset_id": "d", "where": "1=1; DROP TABLE records"},
        )

        assert result.success is False
        assert "Forbidden keyword" in result.error_message
        assert mock_client.get.called is False

    @pytest.mark.asyncio
    async def test_query_data_rejects_malicious_select(self):
        """Test that a forbidden keyword in select fails the tool call."""
        plugin, _ = _initialized_plugin()
        result = await plugin.execute_tool(
            "query_data", {"dataset_id": "d", "select": "a, (delete from t)"}
        )
        assert result.success is False
        assert "select clause" in result.error_message

    @pytest.mark.asyncio
    async def test_query_data_allows_keyword_in_quoted_literal(self):
        """Test that quoted literals containing keywords are accepted."""
        plugin, mock_client = _initialized_plugin(get_return=self.RECORDS_RESPONSE)

        result = await plugin.execute_tool(
            "query_data", {"dataset_id": "d", "where": 'status = "UPDATE requested"'}
        )

        assert result.success is True
        assert (
            mock_client.get.call_args[1]["params"]["where"]
            == 'status = "UPDATE requested"'
        )

    @pytest.mark.asyncio
    async def test_contract_query_data_compiles_filters_to_where(self):
        """Test that the DataPlugin contract compiles filters into ODSQL where."""
        plugin, mock_client = _initialized_plugin(get_return=self.RECORDS_RESPONSE)

        records = await plugin.query_data(
            "police-calls", {"call_type": "Noise", "year": 2026}, limit=10
        )

        params = mock_client.get.call_args[1]["params"]
        # ODSQL string literals are double-quoted (single-quote doubling is
        # SQL convention and an ODSQL syntax error).
        assert params["where"] == 'call_type = "Noise" and year = 2026'
        assert params["limit"] == 10
        assert records == self.RECORDS_RESPONSE["results"]

    @pytest.mark.asyncio
    async def test_contract_query_data_without_filters_sends_no_where(self):
        """Test that no where param is sent when there are no filters."""
        plugin, mock_client = _initialized_plugin(get_return=self.RECORDS_RESPONSE)

        await plugin.query_data("police-calls")

        assert "where" not in mock_client.get.call_args[1]["params"]

    @pytest.mark.asyncio
    async def test_contract_query_data_rejects_malicious_filter_field(self):
        """Test that filter field names are validated by the base class."""
        plugin, _ = _initialized_plugin()
        with pytest.raises(ValueError, match="Invalid identifier"):
            await plugin.query_data("d", {"a; DROP TABLE t": 1})


class TestAggregateData:
    """Test aggregate_data compilation and validation."""

    AGG_RESPONSE = {
        "total_count": 2,
        "results": [
            {"call_type": "Noise", "total": 12},
            {"call_type": "Traffic", "total": 5},
        ],
    }

    @pytest.mark.asyncio
    async def test_aggregate_data_compiles_select_and_group_by(self):
        """Test that metrics/group_by compile into ODSQL params."""
        plugin, mock_client = _initialized_plugin(get_return=self.AGG_RESPONSE)

        result = await plugin.execute_tool(
            "aggregate_data",
            {
                "dataset_id": "police-calls",
                "metrics": {"total": "count(*)", "avg_delay": "avg(delay)"},
                "group_by": ["call_type", "district"],
                "where": 'year = "2026"',
                "order_by": "-total",
                "limit": 50,
            },
        )

        assert result.success is True
        assert (
            mock_client.get.call_args[0][0] == "/catalog/datasets/police-calls/records"
        )
        assert mock_client.get.call_args[1]["params"] == {
            "select": "count(*) as total, avg(delay) as avg_delay",
            "group_by": "call_type,district",
            "where": 'year = "2026"',
            "order_by": "total DESC",
            "limit": 50,
        }

    @pytest.mark.asyncio
    async def test_aggregate_data_without_group_by_omits_param(self):
        """Test that group_by is omitted when no fields are given."""
        plugin, mock_client = _initialized_plugin(get_return=self.AGG_RESPONSE)

        await plugin.aggregate_data("d", metrics={"total": "count(*)"})

        params = mock_client.get.call_args[1]["params"]
        assert "group_by" not in params
        assert params["select"] == "count(*) as total"
        # Global aggregates request a single row (API repeats them per record).
        assert params["limit"] == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "order_by,expected",
        [
            ("call_type", "call_type"),
            ("-total", "total DESC"),
            ("total DESC", "total DESC"),
            ("call_type asc", "call_type ASC"),
        ],
    )
    async def test_aggregate_data_order_by_grammar(self, order_by, expected):
        """Test the supported order_by grammar, including metric aliases."""
        plugin, mock_client = _initialized_plugin(get_return=self.AGG_RESPONSE)

        await plugin.aggregate_data(
            "d",
            metrics={"total": "count(*)"},
            group_by=["call_type"],
            order_by=order_by,
        )

        assert mock_client.get.call_args[1]["params"]["order_by"] == expected

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "expr",
        [
            "count(*)",
            "count(call_type)",
            "count(distinct call_type)",
            "sum(amount)",
            "avg(amount)",
            "min(amount)",
            "max(amount)",
            "AVG(amount)",
        ],
    )
    async def test_aggregate_data_accepts_valid_metric_expressions(self, expr):
        """Test that all documented aggregate expressions are accepted."""
        plugin, mock_client = _initialized_plugin(get_return=self.AGG_RESPONSE)

        result = await plugin.aggregate_data("d", metrics={"m": expr})

        assert result.get("error") is not True
        assert mock_client.get.call_args[1]["params"]["select"] == f"{expr} as m"

    @pytest.mark.asyncio
    async def test_aggregate_data_formats_results(self):
        """Test that aggregation output is formatted with a field header."""
        plugin, _ = _initialized_plugin(get_return=self.AGG_RESPONSE)

        result = await plugin.execute_tool(
            "aggregate_data",
            {
                "dataset_id": "d",
                "metrics": {"total": "count(*)"},
                "group_by": ["call_type"],
            },
        )

        text = result.content[0]["text"]
        assert "Aggregation Results: 2 row(s)" in text
        assert "Fields: call_type, total" in text
        assert "call_type: Noise" in text
        assert "total: 12" in text

    @pytest.mark.asyncio
    async def test_aggregate_data_empty_results_message(self):
        """Test the empty-aggregation message."""
        plugin, _ = _initialized_plugin(get_return={"total_count": 0, "results": []})
        result = await plugin.execute_tool(
            "aggregate_data", {"dataset_id": "d", "metrics": {"total": "count(*)"}}
        )
        assert "No records found matching the aggregation." in result.content[0]["text"]


class TestAggregateDataSecurity:
    """Test that aggregate_data rejects injection vectors."""

    @pytest.mark.asyncio
    async def test_malicious_group_by_rejected(self):
        """Injection via group_by field name is rejected before the request."""
        plugin, mock_client = _initialized_plugin()

        result = await plugin.aggregate_data(
            "d", metrics={"total": "count(*)"}, group_by=["status; DROP TABLE users"]
        )

        assert result.get("error") is True
        assert "identifier" in result["message"].lower()
        assert mock_client.get.called is False

    @pytest.mark.asyncio
    async def test_malicious_metric_alias_rejected(self):
        """Malicious metric alias is rejected."""
        plugin, _ = _initialized_plugin()
        result = await plugin.aggregate_data("d", metrics={"a; DROP x": "count(*)"})
        assert result.get("error") is True
        assert "identifier" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_malicious_metric_expression_rejected(self):
        """Non-aggregate metric expression is rejected."""
        plugin, _ = _initialized_plugin()
        result = await plugin.aggregate_data(
            "d", metrics={"total": "count(*); DROP TABLE users"}
        )
        assert result.get("error") is True
        assert "metric expression" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_malicious_order_by_rejected(self):
        """Injection via order_by is rejected."""
        plugin, _ = _initialized_plugin()
        result = await plugin.aggregate_data(
            "d", metrics={"total": "count(*)"}, order_by="total; DROP TABLE users"
        )
        assert result.get("error") is True
        assert result["message"]

    @pytest.mark.asyncio
    async def test_malformed_order_by_rejected(self):
        """An order_by with too many tokens is rejected."""
        plugin, _ = _initialized_plugin()
        result = await plugin.aggregate_data(
            "d", metrics={"total": "count(*)"}, order_by="a b c"
        )
        assert result.get("error") is True
        assert "Invalid order_by" in result["message"]

    @pytest.mark.asyncio
    async def test_malicious_where_rejected(self):
        """Injection via where is rejected by the ODSQL validator."""
        plugin, _ = _initialized_plugin()
        result = await plugin.aggregate_data(
            "d", metrics={"total": "count(*)"}, where="1=1; DELETE FROM t"
        )
        assert result.get("error") is True
        assert "Forbidden keyword" in result["message"]

    @pytest.mark.asyncio
    async def test_empty_metrics_rejected(self):
        """Empty metrics are rejected with a clear message."""
        plugin, _ = _initialized_plugin()
        result = await plugin.aggregate_data("d", metrics={})
        assert result.get("error") is True
        assert "metrics" in result["message"]


class TestListCategories:
    """Test the list_categories tool."""

    FACETS_RESPONSE = {
        "facets": [
            {
                "name": "theme",
                "facets": [
                    {"name": "Public Safety", "count": 12},
                    {"name": "Environment", "count": 4},
                ],
            }
        ]
    }

    @pytest.mark.asyncio
    async def test_list_categories_calls_facets_endpoint(self):
        """Test that the theme facet endpoint is used."""
        plugin, mock_client = _initialized_plugin(get_return=self.FACETS_RESPONSE)

        await plugin.execute_tool("list_categories", {})

        assert mock_client.get.call_args[0][0] == "/catalog/facets"
        assert mock_client.get.call_args[1]["params"] == {"facet": "theme"}

    @pytest.mark.asyncio
    async def test_list_categories_formats_counts(self):
        """Test the category formatting matches the shared style."""
        plugin, _ = _initialized_plugin(get_return=self.FACETS_RESPONSE)

        result = await plugin.execute_tool("list_categories", {})

        text = result.content[0]["text"]
        assert result.success is True
        assert "Categories on Long Beach's open data portal:" in text
        assert "1. Public Safety: 12 dataset(s)" in text
        assert "2. Environment: 4 dataset(s)" in text

    @pytest.mark.asyncio
    async def test_list_categories_empty_message(self):
        """Test the empty-categories message."""
        plugin, _ = _initialized_plugin(get_return={"facets": []})
        result = await plugin.execute_tool("list_categories", {})
        assert (
            "No categories found on Long Beach's open data portal."
            in result.content[0]["text"]
        )


class TestHealthCheck:
    """Test health_check."""

    @pytest.mark.asyncio
    async def test_health_check_returns_true_when_reachable(self):
        """Test that health_check probes the catalog and returns True."""
        plugin, mock_client = _initialized_plugin(
            get_return={"total_count": 1, "results": []}
        )

        assert await plugin.health_check() is True
        assert mock_client.get.call_args[0][0] == "/catalog/datasets"
        assert mock_client.get.call_args[1]["params"] == {"limit": 1}

    @pytest.mark.asyncio
    async def test_health_check_returns_false_on_error(self):
        """Test that health_check swallows errors and returns False."""
        plugin = OpendatasoftPlugin(dict(ODS_CONFIG))
        assert await plugin.health_check() is False


class TestPluginMetadata:
    """Test plugin class attributes."""

    def test_plugin_metadata(self):
        """Test that plugin identity attributes are set."""
        plugin = OpendatasoftPlugin(dict(ODS_CONFIG))
        assert plugin.plugin_name == "opendatasoft"
        assert plugin.plugin_type.value == "open_data"
        assert plugin.plugin_version == "1.0.0"


class TestAggregateWithoutGroupBy:
    """A global aggregate (no group_by) requests a single row: the Explore
    API otherwise repeats the aggregate once per underlying record."""

    @pytest.mark.asyncio
    async def test_limit_forced_to_one(self):
        plugin = OpendatasoftPlugin(
            {
                "base_url": "https://data.example.com",
                "portal_url": "https://data.example.com",
                "city_name": "TestCity",
            }
        )
        plugin._initialized = True
        mock_client = AsyncMock()
        mock_response = Mock()
        mock_response.json.return_value = {
            "total_count": 1728,
            "results": [{"n": 1728}],
        }
        mock_response.raise_for_status = Mock()
        mock_client.get = AsyncMock(return_value=mock_response)
        plugin.client = mock_client

        result = await plugin.aggregate_data("ds", metrics={"n": "count(*)"})
        assert result.get("error") is not True
        assert mock_client.get.call_args[1]["params"]["limit"] == 1

        await plugin.aggregate_data(
            "ds", metrics={"n": "count(*)"}, group_by=["f"], limit=50
        )
        assert mock_client.get.call_args[1]["params"]["limit"] == 50


class TestDatasetIdValidation:
    """dataset_id is interpolated into the request path and must be a safe
    URL slug (code-review finding)."""

    def _plugin(self):
        plugin = OpendatasoftPlugin(
            {
                "base_url": "https://data.example.com",
                "portal_url": "https://data.example.com",
                "city_name": "TestCity",
            }
        )
        plugin._initialized = True
        mock_client = AsyncMock()
        mock_response = Mock()
        mock_response.json.return_value = {"total_count": 0, "results": []}
        mock_response.raise_for_status = Mock()
        mock_client.get = AsyncMock(return_value=mock_response)
        plugin.client = mock_client
        return plugin, mock_client

    @pytest.mark.asyncio
    async def test_path_traversal_rejected(self):
        plugin, mock_client = self._plugin()
        for bad in ("../../catalog/exports", "x/y", "x?apikey=steal", "x#f", "a b"):
            result = await plugin.execute_tool("get_dataset", {"dataset_id": bad})
            assert result.success is False, bad
            assert "Invalid dataset_id" in result.error_message
        mock_client.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_legitimate_slugs_accepted(self):
        plugin, _ = self._plugin()
        for good in ("tree-inventory", "sv2030", "code_violations", "ds@catalog"):
            result = await plugin.execute_tool("get_dataset", {"dataset_id": good})
            assert result.success is True, good

    @pytest.mark.asyncio
    async def test_query_and_aggregate_also_guarded(self):
        plugin, _ = self._plugin()
        r = await plugin.execute_tool("query_data", {"dataset_id": "../x", "limit": 1})
        assert r.success is False
        r = await plugin.execute_tool(
            "aggregate_data", {"dataset_id": "../x", "metrics": {"n": "count(*)"}}
        )
        assert r.success is False


class TestCodeReviewFixes:
    """Regressions confirmed by the adversarial review of this branch."""

    def _plugin(self, get_return=None):
        plugin = OpendatasoftPlugin(
            {
                "base_url": "https://data.example.com",
                "portal_url": "https://data.example.com",
                "city_name": "TestCity",
            }
        )
        plugin._initialized = True
        mock_client = AsyncMock()
        mock_response = Mock()
        mock_response.json.return_value = get_return or {
            "total_count": 0,
            "results": [],
        }
        mock_response.raise_for_status = Mock()
        mock_client.get = AsyncMock(return_value=mock_response)
        plugin.client = mock_client
        return plugin, mock_client

    @pytest.mark.asyncio
    async def test_apostrophe_filter_value_uses_odsql_escaping(self):
        plugin, mock_client = self._plugin()
        await plugin.query_data("d", {"name": "Val-d'Or"}, limit=5)
        where = mock_client.get.call_args[1]["params"]["where"]
        assert where == 'name = "Val-d\'Or"'

    @pytest.mark.asyncio
    async def test_embedded_double_quote_escaped_with_backslash(self):
        plugin, mock_client = self._plugin()
        await plugin.query_data("d", {"name": 'say "hi"'}, limit=5)
        where = mock_client.get.call_args[1]["params"]["where"]
        assert where == 'name = "say \\"hi\\""'

    @pytest.mark.asyncio
    async def test_limits_clamped_on_all_paths(self):
        plugin, mock_client = self._plugin()
        await plugin.query_data("d", limit=0)
        assert mock_client.get.call_args[1]["params"]["limit"] == 1
        await plugin.query_data("d", limit=5000)
        assert mock_client.get.call_args[1]["params"]["limit"] == 100
        await plugin.search_datasets("x", limit=500)
        assert mock_client.get.call_args[1]["params"]["limit"] == 100

    @pytest.mark.asyncio
    async def test_group_by_string_coerced_to_list(self):
        plugin, mock_client = self._plugin(
            get_return={"total_count": 1, "results": [{"neighborhood": "A", "n": 1}]}
        )
        result = await plugin.aggregate_data(
            "d", metrics={"n": "count(*)"}, group_by="neighborhood"
        )
        assert result.get("error") is not True
        assert mock_client.get.call_args[1]["params"]["group_by"] == "neighborhood"

    @pytest.mark.asyncio
    async def test_empty_count_rejected(self):
        plugin, _ = self._plugin()
        result = await plugin.aggregate_data("d", metrics={"n": "count()"})
        assert result.get("error") is True
        assert "metric expression" in result["message"]


class TestMetadataEnrichment:
    ENRICHED: ClassVar[dict] = {
        "dataset_id": "police-calls",
        "metas": {
            "default": {
                "title": "Police Calls",
                "description": "Dispatch calls",
                "records_count": 4321,
                "modified": "2026-01-15T08:00:00+00:00",
                "data_processed": "2026-01-16T01:00:00+00:00",
                "metadata_processed": "2026-01-14",
                "publisher": "Long Beach PD",
                "license": "Open Database License",
                "license_url": "https://opendatacommons.org/licenses/odbl/",
                "attributions": ["LBPD", "City of Long Beach"],
                "references": "https://data.longbeach.gov/pages/police/",
                "theme": ["Public Safety"],
            }
        },
        "fields": [{"name": "a"}, {"name": "b"}],
    }

    def test_format_dataset_surfaces_publisher_license_dates(self):
        plugin, _ = _initialized_plugin()
        out = plugin._format_dataset(self.ENRICHED)
        assert "Publisher: Long Beach PD" in out
        assert "License: Open Database License — (external: opendatacommons.org)" in out
        assert "Attribution: LBPD, City of Long Beach" in out
        assert (
            "Last modified: 2026-01-15 | Data processed: 2026-01-16 | "
            "Metadata processed: 2026-01-14"
        ) in out
        assert "Records: 4321 | Fields: 2" in out
        assert "References: https://data.longbeach.gov/pages/police/" in out

    def test_search_results_header_and_facts(self):
        plugin, _ = _initialized_plugin()
        out = plugin._format_search_results([self.ENRICHED], total=88)
        assert out.startswith(
            "Found 88 matching dataset(s) in Long Beach's open data portal (showing 1-1):"
        )
        assert "Publisher: Long Beach PD | Modified: 2026-01-15 | Records: 4321" in out

    @pytest.mark.asyncio
    async def test_search_tool_uses_total_count(self):
        plugin, _ = _initialized_plugin(
            get_return={"total_count": 88, "results": [self.ENRICHED]}
        )
        result = await plugin.execute_tool("search_datasets", {"query": "police"})
        assert result.success
        assert "Found 88 matching dataset(s)" in result.content[0]["text"]

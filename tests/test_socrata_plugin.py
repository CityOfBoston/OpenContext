"""Comprehensive tests for Socrata plugin.

These tests verify plugin initialization, tool execution, API interactions,
error handling, and data formatting. Tests are designed to fail if functionality breaks.
"""

import pytest
from unittest.mock import AsyncMock, Mock, patch

from plugins.socrata.plugin import SocrataPlugin


class TestPluginInitialization:
    """Test plugin initialization."""

    @pytest.fixture
    def socrata_config(self):
        """Standard Socrata plugin configuration."""
        return {
            "base_url": "https://data.cityofboston.gov",
            "portal_url": "https://data.cityofboston.gov",
            "city_name": "Boston",
            "app_token": "test-app-token-123",
            "timeout": 30.0,
        }

    def _mock_get_response(self, json_data):
        """Create a mock GET response."""
        mock = Mock()
        mock.json.return_value = json_data
        mock.raise_for_status = Mock()
        return mock

    @pytest.mark.asyncio
    async def test_plugin_initialization_succeeds(self, socrata_config):
        """Test that plugin initialization succeeds with valid config."""
        plugin = SocrataPlugin(socrata_config)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(
                return_value=self._mock_get_response({"results": []})
            )
            mock_client_class.return_value = mock_client

            result = await plugin.initialize()

            assert result is True
            assert plugin.is_initialized is True
            assert plugin.discovery_client is not None
            assert plugin.soda_client is not None
            assert mock_client.get.called

    @pytest.mark.asyncio
    async def test_plugin_initialization_fails_with_missing_app_token(
        self, socrata_config
    ):
        """Test that plugin initialization fails when app token is missing."""
        del socrata_config["app_token"]
        # Config schema will raise on validation - we need invalid config
        with pytest.raises(Exception):
            SocrataPlugin(socrata_config)

    @pytest.mark.asyncio
    async def test_plugin_initialization_fails_with_empty_app_token(
        self, socrata_config
    ):
        """Test that plugin initialization fails when app token is empty."""
        socrata_config["app_token"] = ""
        with pytest.raises(Exception):
            SocrataPlugin(socrata_config)

    @pytest.mark.asyncio
    async def test_plugin_initialization_includes_app_token_header(
        self, socrata_config
    ):
        """Test that plugin initialization includes X-App-Token in headers."""
        plugin = SocrataPlugin(socrata_config)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(
                return_value=self._mock_get_response({"results": []})
            )
            mock_client_class.return_value = mock_client

            await plugin.initialize()

            # AsyncClient called twice (discovery + soda)
            assert mock_client_class.call_count == 2
            for call in mock_client_class.call_args_list:
                call_kwargs = call[1]
                assert "headers" in call_kwargs
                assert call_kwargs["headers"]["X-App-Token"] == "test-app-token-123"

    @pytest.mark.asyncio
    async def test_plugin_shutdown_closes_both_clients(self, socrata_config):
        """Test that plugin shutdown closes both HTTP clients."""
        plugin = SocrataPlugin(socrata_config)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(
                return_value=self._mock_get_response({"results": []})
            )
            mock_client_class.return_value = mock_client

            await plugin.initialize()
            assert plugin.discovery_client is not None
            assert plugin.soda_client is not None

            await plugin.shutdown()

            assert mock_client.aclose.call_count == 2
            assert plugin.is_initialized is False


class TestGetTools:
    """Test get_tools method."""

    @pytest.fixture
    def socrata_config(self):
        return {
            "base_url": "https://data.cityofboston.gov",
            "portal_url": "https://data.cityofboston.gov",
            "city_name": "Boston",
            "app_token": "test-token",
        }

    def test_get_tools_returns_all_six_tools(self, socrata_config):
        """Test that get_tools returns all 6 expected tools."""
        plugin = SocrataPlugin(socrata_config)
        tools = plugin.get_tools()

        assert len(tools) == 6
        tool_names = [t.name for t in tools]
        assert "search_datasets" in tool_names
        assert "get_dataset" in tool_names
        assert "get_schema" in tool_names
        assert "query_dataset" in tool_names
        assert "list_categories" in tool_names
        assert "execute_sql" in tool_names

    def test_get_tools_includes_city_name_in_descriptions(self, socrata_config):
        """Test that tool descriptions include city name."""
        plugin = SocrataPlugin(socrata_config)
        tools = plugin.get_tools()

        for tool in tools:
            if tool.name != "execute_sql":  # execute_sql has generic description
                assert "Boston" in tool.description

    def test_get_tools_has_correct_input_schemas(self, socrata_config):
        """Test that tools have correct input schemas."""
        plugin = SocrataPlugin(socrata_config)
        tools = plugin.get_tools()

        search_tool = next(t for t in tools if t.name == "search_datasets")
        assert search_tool.input_schema["type"] == "object"
        assert "query" in search_tool.input_schema["properties"]
        assert "limit" in search_tool.input_schema["properties"]
        assert "query" in search_tool.input_schema["required"]

        execute_sql_tool = next(t for t in tools if t.name == "execute_sql")
        assert execute_sql_tool.input_schema["type"] == "object"
        assert "dataset_id" in execute_sql_tool.input_schema["properties"]
        assert "soql" in execute_sql_tool.input_schema["properties"]
        assert "dataset_id" in execute_sql_tool.input_schema["required"]
        assert "soql" in execute_sql_tool.input_schema["required"]


class TestSearchDatasets:
    """Test search_datasets method."""

    @pytest.fixture
    def socrata_config(self):
        return {
            "base_url": "https://data.cityofboston.gov",
            "portal_url": "https://data.cityofboston.gov",
            "city_name": "Boston",
            "app_token": "test-token",
        }

    @pytest.mark.asyncio
    async def test_search_datasets_scopes_to_domain(self, socrata_config):
        """Test that search_datasets scopes Discovery API to configured domain."""
        plugin = SocrataPlugin(socrata_config)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(
                side_effect=[
                    Mock(
                        json=Mock(return_value={"results": []}), raise_for_status=Mock()
                    ),
                    Mock(
                        json=Mock(
                            return_value={
                                "results": [
                                    {
                                        "resource": {
                                            "id": "wc4w-4jew",
                                            "name": "Test Dataset",
                                            "description": "Desc",
                                        }
                                    }
                                ]
                            }
                        ),
                        raise_for_status=Mock(),
                    ),
                ]
            )
            mock_client_class.return_value = mock_client

            await plugin.initialize()
            results = await plugin.search_datasets("housing", limit=10)

            assert len(results) == 1
            assert results[0]["resource"]["id"] == "wc4w-4jew"
            # Verify domains param was passed
            get_calls = mock_client.get.call_args_list
            assert len(get_calls) >= 2
            params = get_calls[1][1].get("params", {})
            assert "domains" in params
            assert "data.cityofboston.gov" in str(params.get("domains", ""))


class TestGetDataset:
    """Test get_dataset method."""

    @pytest.fixture
    def socrata_config(self):
        return {
            "base_url": "https://data.cityofboston.gov",
            "portal_url": "https://data.cityofboston.gov",
            "city_name": "Boston",
            "app_token": "test-token",
        }

    @pytest.mark.asyncio
    async def test_get_dataset_constructs_correct_url(self, socrata_config):
        """Test that get_dataset constructs correct metadata URL."""
        plugin = SocrataPlugin(socrata_config)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(
                side_effect=[
                    Mock(
                        json=Mock(return_value={"results": []}), raise_for_status=Mock()
                    ),
                    Mock(
                        json=Mock(
                            return_value={
                                "id": "wc4w-4jew",
                                "name": "311 Requests",
                                "description": "Test",
                            }
                        ),
                        raise_for_status=Mock(),
                    ),
                ]
            )
            mock_client.post = AsyncMock()
            mock_client_class.return_value = mock_client

            await plugin.initialize()
            dataset = await plugin.get_dataset("wc4w-4jew")

            assert dataset["id"] == "wc4w-4jew"
            assert dataset["name"] == "311 Requests"
            # Second get call should be to soda client (portal)
            get_calls = mock_client.get.call_args_list
            assert len(get_calls) >= 2
            assert "/api/views/wc4w-4jew.json" in str(get_calls[1][0])


class TestGetSchema:
    """Test get_schema method."""

    @pytest.fixture
    def socrata_config(self):
        return {
            "base_url": "https://data.cityofboston.gov",
            "portal_url": "https://data.cityofboston.gov",
            "city_name": "Boston",
            "app_token": "test-token",
        }

    @pytest.mark.asyncio
    async def test_get_schema_returns_columns(self, socrata_config):
        """Test that get_schema returns parsed column list."""
        plugin = SocrataPlugin(socrata_config)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(
                side_effect=[
                    Mock(
                        json=Mock(return_value={"results": []}), raise_for_status=Mock()
                    ),
                    Mock(
                        json=Mock(
                            return_value={
                                "columns": [
                                    {
                                        "fieldName": "id",
                                        "name": "ID",
                                        "dataTypeName": "number",
                                    },
                                    {
                                        "fieldName": "name",
                                        "name": "Name",
                                        "dataTypeName": "text",
                                    },
                                ]
                            }
                        ),
                        raise_for_status=Mock(),
                    ),
                ]
            )
            mock_client_class.return_value = mock_client

            await plugin.initialize()
            schema = await plugin.get_schema("wc4w-4jew")

            assert len(schema) == 2
            assert schema[0]["fieldName"] == "id"
            assert schema[1]["fieldName"] == "name"


class TestQueryDataset:
    """Test query_dataset (SoQL) method."""

    @pytest.fixture
    def socrata_config(self):
        return {
            "base_url": "https://data.cityofboston.gov",
            "portal_url": "https://data.cityofboston.gov",
            "city_name": "Boston",
            "app_token": "test-token",
        }

    @pytest.mark.asyncio
    async def test_query_dataset_sends_correct_post_body(self, socrata_config):
        """Test that query_dataset sends correct SODA3 POST body."""
        plugin = SocrataPlugin(socrata_config)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(
                return_value=Mock(
                    json=Mock(return_value={"results": []}), raise_for_status=Mock()
                )
            )
            mock_client.post = AsyncMock(
                return_value=Mock(
                    json=Mock(return_value=[{"id": 1, "name": "Test"}]),
                    raise_for_status=Mock(),
                )
            )
            mock_client_class.return_value = mock_client

            await plugin.initialize()
            result = await plugin._query_dataset(
                "wc4w-4jew", "SELECT * WHERE year > 2020 LIMIT 50"
            )

            assert len(result) == 1
            assert result[0]["id"] == 1
            post_call = mock_client.post.call_args
            assert "/api/v3/views/wc4w-4jew/query.json" in str(post_call[0])
            body = post_call[1]["json"]
            assert body["query"] == "SELECT * WHERE year > 2020 LIMIT 50"
            assert "page" in body
            assert body["page"]["pageNumber"] == 1
            assert body["page"]["pageSize"] == 50


class TestListCategories:
    """Test list_categories method."""

    @pytest.fixture
    def socrata_config(self):
        return {
            "base_url": "https://data.cityofboston.gov",
            "portal_url": "https://data.cityofboston.gov",
            "city_name": "Boston",
            "app_token": "test-token",
        }

    @pytest.mark.asyncio
    async def test_list_categories_hits_facet_endpoint(self, socrata_config):
        """Test that list_categories hits Discovery API facets."""
        plugin = SocrataPlugin(socrata_config)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(
                side_effect=[
                    Mock(
                        json=Mock(return_value={"results": []}), raise_for_status=Mock()
                    ),
                    Mock(
                        json=Mock(
                            return_value={
                                "facets": {
                                    "categories": [
                                        {"name": "Finance", "count": 10},
                                        {"name": "Health", "count": 5},
                                    ]
                                }
                            }
                        ),
                        raise_for_status=Mock(),
                    ),
                ]
            )
            mock_client_class.return_value = mock_client

            await plugin.initialize()
            categories = await plugin._list_categories()

            assert len(categories) == 2
            assert categories[0]["name"] == "Finance"
            get_calls = mock_client.get.call_args_list
            params = get_calls[1][1].get("params", {})
            assert "facets" in params
            assert "categories" in str(params.get("facets", ""))


class TestExecuteTool:
    """Test execute_tool method."""

    @pytest.fixture
    def socrata_config(self):
        return {
            "base_url": "https://data.cityofboston.gov",
            "portal_url": "https://data.cityofboston.gov",
            "city_name": "Boston",
            "app_token": "test-token",
        }

    @pytest.mark.asyncio
    async def test_execute_tool_search_datasets_succeeds(self, socrata_config):
        """Test executing search_datasets tool."""
        plugin = SocrataPlugin(socrata_config)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(
                side_effect=[
                    Mock(
                        json=Mock(return_value={"results": []}), raise_for_status=Mock()
                    ),
                    Mock(
                        json=Mock(
                            return_value={
                                "results": [
                                    {
                                        "resource": {
                                            "id": "1",
                                            "name": "Test",
                                            "description": "Desc",
                                        }
                                    }
                                ]
                            }
                        ),
                        raise_for_status=Mock(),
                    ),
                ]
            )
            mock_client_class.return_value = mock_client

            await plugin.initialize()
            result = await plugin.execute_tool(
                "search_datasets", {"query": "test", "limit": 10}
            )

            assert result.success is True
            assert len(result.content) > 0
            assert "text" in result.content[0]

    @pytest.mark.asyncio
    async def test_execute_tool_get_dataset_missing_param(self, socrata_config):
        """Test executing get_dataset tool without required parameter."""
        plugin = SocrataPlugin(socrata_config)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(
                return_value=Mock(
                    json=Mock(return_value={"results": []}), raise_for_status=Mock()
                )
            )
            mock_client_class.return_value = mock_client

            await plugin.initialize()
            result = await plugin.execute_tool("get_dataset", {})

            assert result.success is False
            assert "required" in result.error_message.lower()

    @pytest.mark.asyncio
    async def test_execute_tool_returns_failed_toolresult_on_api_error(
        self, socrata_config
    ):
        """Test that execute_tool returns failed ToolResult on API errors."""
        plugin = SocrataPlugin(socrata_config)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(
                side_effect=[
                    Mock(
                        json=Mock(return_value={"results": []}), raise_for_status=Mock()
                    ),
                    RuntimeError("Dataset not found"),
                ]
            )
            mock_client_class.return_value = mock_client

            await plugin.initialize()
            result = await plugin.execute_tool(
                "get_dataset", {"dataset_id": "nonexistent"}
            )

            assert result.success is False
            assert result.error_message is not None
            assert "Dataset not found" in result.error_message

    @pytest.mark.asyncio
    async def test_execute_tool_unknown_tool(self, socrata_config):
        """Test executing unknown tool."""
        plugin = SocrataPlugin(socrata_config)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(
                return_value=Mock(
                    json=Mock(return_value={"results": []}), raise_for_status=Mock()
                )
            )
            mock_client_class.return_value = mock_client

            await plugin.initialize()
            result = await plugin.execute_tool("unknown_tool", {})

            assert result.success is False
            assert "Unknown tool" in result.error_message

    @pytest.mark.asyncio
    async def test_execute_tool_execute_sql_succeeds(self, socrata_config):
        """Test executing execute_sql tool with valid SoQL."""
        plugin = SocrataPlugin(socrata_config)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(
                return_value=Mock(
                    json=Mock(return_value={"results": []}), raise_for_status=Mock()
                )
            )
            mock_client.post = AsyncMock(
                return_value=Mock(
                    json=Mock(return_value=[{"id": 1, "name": "Test"}]),
                    raise_for_status=Mock(),
                )
            )
            mock_client_class.return_value = mock_client

            await plugin.initialize()
            result = await plugin.execute_tool(
                "execute_sql",
                {
                    "dataset_id": "wc4w-4jew",
                    "soql": "SELECT * LIMIT 10",
                },
            )

            assert result.success is True
            assert len(result.content) > 0
            assert "SQL Query Results" in result.content[0]["text"]

    @pytest.mark.asyncio
    async def test_execute_tool_execute_sql_validation_error(self, socrata_config):
        """Test executing execute_sql tool with invalid SoQL."""
        plugin = SocrataPlugin(socrata_config)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(
                return_value=Mock(
                    json=Mock(return_value={"results": []}), raise_for_status=Mock()
                )
            )
            mock_client_class.return_value = mock_client

            await plugin.initialize()
            result = await plugin.execute_tool(
                "execute_sql",
                {"dataset_id": "wc4w-4jew", "soql": "DELETE FROM users"},
            )

            assert result.success is False
            assert result.error_message is not None
            assert "SELECT" in result.error_message or "DELETE" in result.error_message

    @pytest.mark.asyncio
    async def test_execute_tool_execute_sql_missing_param(self, socrata_config):
        """Test executing execute_sql tool without required parameters."""
        plugin = SocrataPlugin(socrata_config)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(
                return_value=Mock(
                    json=Mock(return_value={"results": []}), raise_for_status=Mock()
                )
            )
            mock_client_class.return_value = mock_client

            await plugin.initialize()
            result = await plugin.execute_tool("execute_sql", {})

            assert result.success is False
            assert "required" in result.error_message.lower()

    @pytest.mark.asyncio
    async def test_execute_sql_returns_error_on_api_failure(self, socrata_config):
        """Test execute_sql returns error when SODA API fails."""
        plugin = SocrataPlugin(socrata_config)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(
                return_value=Mock(
                    json=Mock(return_value={"results": []}), raise_for_status=Mock()
                )
            )
            mock_client.post = AsyncMock(side_effect=RuntimeError("Dataset not found"))
            mock_client_class.return_value = mock_client

            await plugin.initialize()
            result = await plugin.execute_tool(
                "execute_sql",
                {"dataset_id": "nonexistent", "soql": "SELECT * LIMIT 1"},
            )

            assert result.success is False
            assert "Dataset not found" in result.error_message


class TestHealthCheck:
    """Test health_check method."""

    @pytest.fixture
    def socrata_config(self):
        return {
            "base_url": "https://data.cityofboston.gov",
            "portal_url": "https://data.cityofboston.gov",
            "city_name": "Boston",
            "app_token": "test-token",
        }

    @pytest.mark.asyncio
    async def test_health_check_succeeds(self, socrata_config):
        """Test that health check succeeds when API is healthy."""
        plugin = SocrataPlugin(socrata_config)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(
                return_value=Mock(
                    json=Mock(return_value={"results": []}), raise_for_status=Mock()
                )
            )
            mock_client_class.return_value = mock_client

            await plugin.initialize()
            health = await plugin.health_check()

            assert health is True

    @pytest.mark.asyncio
    async def test_health_check_fails_on_exception(self, socrata_config):
        """Test that health check fails when API is unreachable."""
        plugin = SocrataPlugin(socrata_config)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(
                side_effect=[
                    Mock(
                        json=Mock(return_value={"results": []}), raise_for_status=Mock()
                    ),
                    Exception("Connection failed"),
                ]
            )
            mock_client_class.return_value = mock_client

            await plugin.initialize()
            health = await plugin.health_check()

            assert health is False


class TestQueryData:
    """Test query_data (DataPlugin contract) method."""

    @pytest.fixture
    def socrata_config(self):
        return {
            "base_url": "https://data.cityofboston.gov",
            "portal_url": "https://data.cityofboston.gov",
            "city_name": "Boston",
            "app_token": "test-token",
        }

    @pytest.mark.asyncio
    async def test_query_data_compiles_filters_to_soql(self, socrata_config):
        """Test that query_data compiles filters dict to SoQL WHERE."""
        plugin = SocrataPlugin(socrata_config)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(
                return_value=Mock(
                    json=Mock(return_value={"results": []}), raise_for_status=Mock()
                )
            )
            mock_client.post = AsyncMock(
                return_value=Mock(
                    json=Mock(return_value=[{"id": 1}]),
                    raise_for_status=Mock(),
                )
            )
            mock_client_class.return_value = mock_client

            await plugin.initialize()
            await plugin.query_data(
                "wc4w-4jew",
                filters={"status": "Open", "year": 2020},
                limit=50,
            )

            post_call = mock_client.post.call_args
            body = post_call[1]["json"]
            query = body["query"]
            assert "status = 'Open'" in query
            assert "year = 2020" in query
            assert "LIMIT 50" in query

    @pytest.mark.asyncio
    async def test_query_data_filter_with_none_value(self, socrata_config):
        """Test that query_data handles None filter values as IS NULL."""
        plugin = SocrataPlugin(socrata_config)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(
                return_value=Mock(
                    json=Mock(return_value={"results": []}), raise_for_status=Mock()
                )
            )
            mock_client.post = AsyncMock(
                return_value=Mock(
                    json=Mock(return_value=[]),
                    raise_for_status=Mock(),
                )
            )
            mock_client_class.return_value = mock_client

            await plugin.initialize()
            await plugin.query_data("wc4w-4jew", filters={"status": None}, limit=10)

            post_call = mock_client.post.call_args
            body = post_call[1]["json"]
            assert "status IS NULL" in body["query"]


class TestFormatMethods:
    """Test formatting helper methods."""

    @pytest.fixture
    def plugin(self):
        return SocrataPlugin(
            {
                "base_url": "https://data.cityofboston.gov",
                "portal_url": "https://data.cityofboston.gov",
                "city_name": "Boston",
                "app_token": "test-token",
            }
        )

    def test_format_search_results_empty(self, plugin):
        result = plugin._format_search_results([])
        assert "No datasets found" in result

    def test_format_search_results_with_category(self, plugin):
        datasets = [
            {
                "resource": {
                    "id": "abcd-1234",
                    "name": "Budget Data",
                    "description": "City budget",
                    "category": "Finance",
                    "permalink": "https://data.cityofboston.gov/d/abcd-1234",
                }
            }
        ]
        result = plugin._format_search_results(datasets)
        assert "Budget Data" in result
        assert "Finance" in result
        assert "abcd-1234" in result

    def test_format_search_results_no_permalink(self, plugin):
        datasets = [
            {
                "resource": {
                    "id": "abcd-1234",
                    "name": "Budget Data",
                    "description": "City budget",
                }
            }
        ]
        result = plugin._format_search_results(datasets)
        assert "abcd-1234" in result
        assert "data.cityofboston.gov/d/abcd-1234" in result

    def test_format_dataset(self, plugin):
        dataset = {
            "id": "abcd-1234",
            "name": "311 Service Requests",
            "description": "All 311 calls",
            "rowCount": 500000,
            "rowsUpdatedAt": "2024-01-01",
            "tags": ["service", "311"],
            "category": "Public Safety",
            "license": {"name": "Public Domain"},
        }
        result = plugin._format_dataset(dataset)
        assert "311 Service Requests" in result
        assert "abcd-1234" in result
        assert "500000" in result
        assert "service" in result
        assert "Public Safety" in result

    def test_format_dataset_minimal(self, plugin):
        result = plugin._format_dataset({})
        assert "Untitled" in result
        assert "ID: unknown" in result
        # Connector guidance lives on the ToolHandler, outside the data body.
        assert "get_schema" not in result
        assert plugin.tool_handlers()["get_dataset"].guidance is not None
        assert "get_schema" in plugin.tool_handlers()["get_dataset"].guidance

    def test_format_schema_empty(self, plugin):
        result = plugin._format_schema([])
        assert "No schema" in result

    def test_format_schema_with_columns(self, plugin):
        columns = [
            {
                "fieldName": "case_id",
                "name": "Case ID",
                "dataTypeName": "number",
                "description": "Unique case identifier",
            },
            {
                "fieldName": "status",
                "name": "Status",
                "dataTypeName": "text",
            },
        ]
        result = plugin._format_schema(columns)
        assert "case_id" in result
        assert "number" in result
        assert "Unique case identifier" in result
        assert "status" in result

    def test_format_query_results_empty(self, plugin):
        result = plugin._format_query_results([])
        assert "No records found" in result

    def test_format_query_results_with_records(self, plugin):
        records = [{"id": 1, "name": "Test"}, {"id": 2, "name": "Test2"}]
        result = plugin._format_query_results(records, limit=10)
        assert "2 record(s)" in result
        assert "id: 1" in result

    def test_format_query_results_truncated(self, plugin):
        records = [{"id": i} for i in range(20)]
        result = plugin._format_query_results(records, limit=5)
        assert "15 more" in result

    def test_format_sql_results_empty(self, plugin):
        result = plugin._format_sql_results([], [])
        assert "No records found" in result

    def test_format_sql_results_with_many_records(self, plugin):
        records = [{"id": i, "name": f"Row {i}"} for i in range(15)]
        fields = [{"id": "id"}, {"id": "name"}]
        result = plugin._format_sql_results(records, fields)
        assert "SQL Query Results" in result
        assert "5 more" in result

    def test_format_categories_empty(self, plugin):
        result = plugin._format_categories([])
        assert "No categories found" in result

    def test_format_categories_dict_list(self, plugin):
        categories = [{"name": "Finance", "count": 10}, {"name": "Health", "count": 5}]
        result = plugin._format_categories(categories)
        assert "Finance" in result
        assert "10" in result

    def test_format_categories_non_dict(self, plugin):
        result = plugin._format_categories(["Finance", "Health"])
        assert "Finance" in result
        assert "Health" in result


class TestExecuteToolMissingParams:
    """Test execute_tool missing required param branches."""

    @pytest.fixture
    def socrata_config(self):
        return {
            "base_url": "https://data.cityofboston.gov",
            "portal_url": "https://data.cityofboston.gov",
            "city_name": "Boston",
            "app_token": "test-token",
        }

    @pytest.mark.asyncio
    async def test_execute_tool_get_schema_missing_dataset_id(self, socrata_config):
        plugin = SocrataPlugin(socrata_config)
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(
                return_value=Mock(
                    json=Mock(return_value={"results": []}), raise_for_status=Mock()
                )
            )
            mock_client_class.return_value = mock_client
            await plugin.initialize()
            result = await plugin.execute_tool("get_schema", {})
        assert result.success is False
        assert "required" in result.error_message.lower()

    @pytest.mark.asyncio
    async def test_execute_tool_query_dataset_missing_dataset_id(self, socrata_config):
        plugin = SocrataPlugin(socrata_config)
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(
                return_value=Mock(
                    json=Mock(return_value={"results": []}), raise_for_status=Mock()
                )
            )
            mock_client_class.return_value = mock_client
            await plugin.initialize()
            result = await plugin.execute_tool("query_dataset", {})
        assert result.success is False
        assert "required" in result.error_message.lower()

    @pytest.mark.asyncio
    async def test_execute_tool_query_dataset_missing_soql_query(self, socrata_config):
        plugin = SocrataPlugin(socrata_config)
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(
                return_value=Mock(
                    json=Mock(return_value={"results": []}), raise_for_status=Mock()
                )
            )
            mock_client_class.return_value = mock_client
            await plugin.initialize()
            result = await plugin.execute_tool(
                "query_dataset", {"dataset_id": "wc4w-4jew"}
            )
        assert result.success is False
        assert "required" in result.error_message.lower()

    @pytest.mark.asyncio
    async def test_execute_tool_list_categories_succeeds(self, socrata_config):
        plugin = SocrataPlugin(socrata_config)
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(
                side_effect=[
                    Mock(
                        json=Mock(return_value={"results": []}), raise_for_status=Mock()
                    ),
                    Mock(
                        json=Mock(
                            return_value={
                                "facets": {
                                    "categories": [{"name": "Finance", "count": 5}]
                                }
                            }
                        ),
                        raise_for_status=Mock(),
                    ),
                ]
            )
            mock_client_class.return_value = mock_client
            await plugin.initialize()
            result = await plugin.execute_tool("list_categories", {})
        assert result.success is True
        assert "Finance" in result.content[0]["text"]


class TestParseSoqlLimit:
    """Test _parse_soql_limit edge cases."""

    @pytest.fixture
    def plugin(self):
        return SocrataPlugin(
            {
                "base_url": "https://data.cityofboston.gov",
                "portal_url": "https://data.cityofboston.gov",
                "city_name": "Boston",
                "app_token": "test-token",
            }
        )

    def test_no_limit_returns_default(self, plugin):
        assert plugin._parse_soql_limit("SELECT *", default=50) == 50

    def test_with_max_val_caps_result(self, plugin):
        assert plugin._parse_soql_limit("SELECT * LIMIT 10000", max_val=500) == 500

    def test_invalid_limit_returns_default(self, plugin):
        assert plugin._parse_soql_limit("SELECT * LIMIT abc", default=25) == 25


class TestQueryDatasetRowExtraction:
    """Test _query_dataset handles non-list API responses."""

    @pytest.fixture
    def socrata_config(self):
        return {
            "base_url": "https://data.cityofboston.gov",
            "portal_url": "https://data.cityofboston.gov",
            "city_name": "Boston",
            "app_token": "test-token",
        }

    @pytest.mark.asyncio
    async def test_query_dataset_dict_response_with_rows_key(self, socrata_config):
        plugin = SocrataPlugin(socrata_config)
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(
                return_value=Mock(
                    json=Mock(return_value={"results": []}), raise_for_status=Mock()
                )
            )
            mock_client.post = AsyncMock(
                return_value=Mock(
                    json=Mock(return_value={"rows": [{"id": 1}, {"id": 2}]}),
                    raise_for_status=Mock(),
                )
            )
            mock_client_class.return_value = mock_client
            await plugin.initialize()
            result = await plugin._query_dataset("wc4w-4jew", "SELECT * LIMIT 10")
        assert len(result) == 2
        assert result[0]["id"] == 1


class TestSearchRequiresQuery:
    """search_datasets enforces its required 'query' argument (review fix)."""

    @pytest.mark.asyncio
    async def test_missing_query_rejected(self):
        plugin = SocrataPlugin(
            {
                "base_url": "https://data.example.com",
                "portal_url": "https://data.example.com",
                "city_name": "TestCity",
                "app_token": "tok",
            }
        )
        plugin._initialized = True
        result = await plugin.execute_tool("search_datasets", {})
        assert result.success is False
        assert "query is required" in result.error_message


class TestMetadataEnrichment:
    @pytest.fixture
    def socrata_config(self):
        return {
            "base_url": "https://data.cityofboston.gov",
            "portal_url": "https://data.cityofboston.gov",
            "city_name": "Boston",
            "app_token": "test-app-token-123",
        }

    @pytest.fixture
    def plugin(self, socrata_config):
        return SocrataPlugin(socrata_config)

    def test_format_dataset_surfaces_views_metadata(self, plugin):
        dataset = {
            "id": "abcd-1234",
            "name": "311 Service Requests",
            "description": "All 311 calls",
            "createdAt": 1420070400,
            "publicationDate": 1420156800,
            "viewLastModified": 1735689600,
            "rowsUpdatedAt": 1735776000,
            "rowCount": 500000,
            "columns": [{"name": "a"}, {"name": "b"}, {"name": "c"}],
            "downloadCount": 42,
            "viewCount": 9001,
            "provenance": "official",
            "attribution": "City of Boston",
            "attributionLink": "https://www.boston.gov/311",
            "license": {
                "name": "Public Domain",
                "termsLink": "https://evil.example.org/terms",
            },
            "licenseId": "PUBLIC_DOMAIN",
            "category": "Public Safety",
            "tags": ["service", "311"],
        }
        out = plugin._format_dataset(dataset)
        assert "Source: City of Boston (external: www.boston.gov)" in out
        assert (
            "License: Public Domain (PUBLIC_DOMAIN) — (external: evil.example.org)"
            in out
        )
        assert "evil.example.org/terms" not in out
        assert (
            "Created: 2015-01-01 | Published: 2015-01-02 | "
            "Metadata modified: 2025-01-01 | Rows updated: 2025-01-02"
        ) in out
        assert (
            "Rows: 500000 | Columns: 3 | Downloads: 42 | Views: 9001 | Provenance: official"
            in out
        )
        assert "Category: Public Safety" in out
        assert "Tags: service, 311" in out
        assert "Portal URL: https://data.cityofboston.gov/d/abcd-1234" in out

    def test_format_dataset_omits_missing_fields(self, plugin):
        out = plugin._format_dataset({"id": "abcd-1234", "name": "X"})
        assert "License" not in out
        assert "Created" not in out
        assert "Rows" not in out
        assert "N/A" not in out

    def test_search_results_header_and_facts(self, plugin):
        hits = [
            {
                "resource": {
                    "id": "abcd-1234",
                    "name": "Budget",
                    "updatedAt": "2026-03-04T10:00:00.000Z",
                    "columns_name": ["a", "b"],
                    "download_count": 5,
                    "attribution": "Finance Dept",
                },
                "classification": {"domain_category": "Finance"},
            }
        ]
        out = plugin._format_search_results(hits, total=57)
        assert out.startswith(
            "Found 57 matching dataset(s) in Boston's open data portal (showing 1-1):"
        )
        assert (
            "Modified: 2026-03-04 | Columns: 2 | Downloads: 5 | Source: Finance Dept"
            in out
        )
        assert "Category: Finance" in out

    @pytest.mark.asyncio
    async def test_search_tool_uses_result_set_size(self, socrata_config):
        plugin = SocrataPlugin(socrata_config)
        with patch.object(
            plugin,
            "_discovery_search",
            new_callable=AsyncMock,
            return_value={
                "resultSetSize": 120,
                "results": [{"resource": {"id": "abcd-1234", "name": "A"}}],
            },
        ) as mock_search:
            result = await plugin.execute_tool(
                "search_datasets", {"query": "a", "limit": 1}
            )
        assert result.success
        assert "Found 120 matching dataset(s)" in result.content[0]["text"]
        mock_search.assert_called_once_with({"q": "a", "limit": 1})

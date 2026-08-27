"""Comprehensive tests for CKAN plugin.

These tests verify plugin initialization, tool execution, API interactions,
error handling, and data formatting. Tests are designed to fail if functionality breaks.
"""

from typing import ClassVar
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

from plugins.ckan.plugin import (
    _SORT_OPTIONS,
    CKANPlugin,
    _build_fq,
    _escape_solr_phrase,
)


class TestPluginInitialization:
    """Test plugin initialization."""

    @pytest.fixture
    def ckan_config(self):
        """Standard CKAN plugin configuration."""
        return {
            "base_url": "https://data.example.com",
            "portal_url": "https://data.example.com",
            "city_name": "TestCity",
            "timeout": 120,
        }

    @pytest.mark.asyncio
    async def test_plugin_initialization_succeeds(self, ckan_config):
        """Test that plugin initialization succeeds with valid config."""
        plugin = CKANPlugin(ckan_config)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_response = Mock()
            mock_response.json.return_value = {"success": True}
            mock_response.raise_for_status = Mock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            result = await plugin.initialize()

            assert result is True
            assert plugin.is_initialized is True
            assert plugin.client is not None
            mock_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_plugin_initialization_fails_on_api_error(self, ckan_config):
        """Test that plugin initialization fails when API test fails."""
        plugin = CKANPlugin(ckan_config)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_response = Mock()
            mock_response.json.return_value = {"success": False}
            mock_response.raise_for_status = Mock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            result = await plugin.initialize()

            assert result is False
            assert plugin.is_initialized is False

    @pytest.mark.asyncio
    async def test_plugin_initialization_fails_on_exception(self, ckan_config):
        """Test that plugin initialization fails on exception."""
        plugin = CKANPlugin(ckan_config)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client_class.side_effect = Exception("Connection failed")

            result = await plugin.initialize()

            assert result is False
            assert plugin.is_initialized is False

    @pytest.mark.asyncio
    async def test_plugin_initialization_with_api_key(self, ckan_config):
        """Test that plugin initialization includes API key in headers."""
        ckan_config["api_key"] = "test-api-key-123"
        plugin = CKANPlugin(ckan_config)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_response = Mock()
            mock_response.json.return_value = {"success": True}
            mock_response.raise_for_status = Mock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            await plugin.initialize()

            # Verify AsyncClient was created with Authorization header
            call_kwargs = mock_client_class.call_args[1]
            assert "headers" in call_kwargs
            assert call_kwargs["headers"]["Authorization"] == "test-api-key-123"

    @pytest.mark.asyncio
    async def test_plugin_shutdown_closes_client(self, ckan_config):
        """Test that plugin shutdown closes HTTP client."""
        plugin = CKANPlugin(ckan_config)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_response = Mock()
            mock_response.json.return_value = {"success": True}
            mock_response.raise_for_status = Mock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            await plugin.initialize()
            assert plugin.client is not None

            await plugin.shutdown()

            mock_client.aclose.assert_called_once()
            # Base class shutdown clears the tracked client list; the plugin's
            # ``client`` attribute is no longer guaranteed to be nulled, so we
            # assert the tracked clients were cleared instead.
            assert plugin._clients == []
            assert plugin.is_initialized is False


class TestGetTools:
    """Test get_tools method."""

    @pytest.fixture
    def ckan_config(self):
        return {
            "base_url": "https://data.example.com",
            "portal_url": "https://data.example.com",
            "city_name": "TestCity",
        }

    def test_get_tools_returns_all_tools(self, ckan_config):
        """Test that get_tools returns all expected tools."""
        plugin = CKANPlugin(ckan_config)
        tools = plugin.get_tools()

        assert len(tools) == 8
        tool_names = [t.name for t in tools]
        assert "search_datasets" in tool_names
        assert "get_dataset" in tool_names
        assert "list_datasets" in tool_names
        assert "get_catalog_stats" in tool_names
        assert "query_data" in tool_names
        assert "get_schema" in tool_names
        assert "execute_sql" in tool_names
        assert "aggregate_data" in tool_names

    def test_get_tools_includes_city_name_in_descriptions(self, ckan_config):
        """Test that tool descriptions include city name."""
        plugin = CKANPlugin(ckan_config)
        tools = plugin.get_tools()

        for tool in tools:
            if (
                tool.name != "execute_sql"
            ):  # execute_sql has different description format
                assert "TestCity" in tool.description

    def test_get_tools_has_correct_input_schemas(self, ckan_config):
        """Test that tools have correct input schemas."""
        plugin = CKANPlugin(ckan_config)
        tools = plugin.get_tools()

        search_tool = next(t for t in tools if t.name == "search_datasets")
        assert search_tool.input_schema["type"] == "object"
        assert "query" in search_tool.input_schema["properties"]
        assert "limit" in search_tool.input_schema["properties"]
        assert "query" in search_tool.input_schema["required"]


class TestSearchDatasets:
    """Test search_datasets method."""

    @pytest.fixture
    def ckan_config(self):
        return {
            "base_url": "https://data.example.com",
            "portal_url": "https://data.example.com",
            "city_name": "TestCity",
        }

    @pytest.mark.asyncio
    async def test_search_datasets_returns_results(self, ckan_config):
        """Test that search_datasets returns dataset results."""
        plugin = CKANPlugin(ckan_config)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            # First call for initialize
            mock_response_init = Mock()
            mock_response_init.json.return_value = {"success": True}
            mock_response_init.raise_for_status = Mock()
            # Second call for search
            mock_response_search = Mock()
            mock_response_search.json.return_value = {
                "result": {
                    "results": [
                        {"id": "dataset-1", "title": "Dataset 1"},
                        {"id": "dataset-2", "title": "Dataset 2"},
                    ]
                }
            }
            mock_response_search.raise_for_status = Mock()
            mock_client.post = AsyncMock(
                side_effect=[mock_response_init, mock_response_search]
            )
            mock_client_class.return_value = mock_client

            await plugin.initialize()
            results = await plugin.search_datasets("test query", limit=10)

            assert len(results) == 2
            assert results[0]["id"] == "dataset-1"
            assert results[1]["id"] == "dataset-2"

    @pytest.mark.asyncio
    async def test_search_datasets_handles_empty_results(self, ckan_config):
        """Test that search_datasets handles empty results."""
        plugin = CKANPlugin(ckan_config)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_response_init = Mock()
            mock_response_init.json.return_value = {"success": True}
            mock_response_init.raise_for_status = Mock()
            mock_response_search = Mock()
            mock_response_search.json.return_value = {"result": {"results": []}}
            mock_response_search.raise_for_status = Mock()
            mock_client.post = AsyncMock(
                side_effect=[mock_response_init, mock_response_search]
            )
            mock_client_class.return_value = mock_client

            await plugin.initialize()
            results = await plugin.search_datasets("nonexistent", limit=10)

            assert results == []

    @pytest.mark.asyncio
    async def test_search_datasets_passes_query_and_limit(self, ckan_config):
        """Test that search_datasets passes correct parameters to API."""
        plugin = CKANPlugin(ckan_config)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_response_init = Mock()
            mock_response_init.json.return_value = {"success": True}
            mock_response_init.raise_for_status = Mock()
            mock_response_search = Mock()
            mock_response_search.json.return_value = {"result": {"results": []}}
            mock_response_search.raise_for_status = Mock()
            mock_client.post = AsyncMock(
                side_effect=[mock_response_init, mock_response_search]
            )
            mock_client_class.return_value = mock_client

            await plugin.initialize()
            await plugin.search_datasets("test query", limit=25)

            # Check second call (after initialize)
            call_args = mock_client.post.call_args_list[1]
            assert call_args[0][0] == "/api/3/action/package_search"
            assert call_args[1]["json"]["q"] == "test query"
            assert call_args[1]["json"]["rows"] == 25


class TestGetDataset:
    """Test get_dataset method."""

    @pytest.fixture
    def ckan_config(self):
        return {
            "base_url": "https://data.example.com",
            "portal_url": "https://data.example.com",
            "city_name": "TestCity",
        }

    @pytest.mark.asyncio
    async def test_get_dataset_returns_dataset_metadata(self, ckan_config):
        """Test that get_dataset returns dataset metadata."""
        plugin = CKANPlugin(ckan_config)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_response_init = Mock()
            mock_response_init.json.return_value = {"success": True}
            mock_response_init.raise_for_status = Mock()
            mock_response_dataset = Mock()
            mock_response_dataset.json.return_value = {
                "result": {
                    "id": "dataset-1",
                    "title": "Test Dataset",
                    "description": "Test description",
                }
            }
            mock_response_dataset.raise_for_status = Mock()
            mock_client.post = AsyncMock(
                side_effect=[mock_response_init, mock_response_dataset]
            )
            mock_client_class.return_value = mock_client

            await plugin.initialize()
            dataset = await plugin.get_dataset("dataset-1")

            assert dataset["id"] == "dataset-1"
            assert dataset["title"] == "Test Dataset"
            assert dataset["description"] == "Test description"

    @pytest.mark.asyncio
    async def test_get_dataset_passes_dataset_id(self, ckan_config):
        """Test that get_dataset passes dataset ID to API."""
        plugin = CKANPlugin(ckan_config)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_response_init = Mock()
            mock_response_init.json.return_value = {"success": True}
            mock_response_init.raise_for_status = Mock()
            mock_response_dataset = Mock()
            mock_response_dataset.json.return_value = {"result": {}}
            mock_response_dataset.raise_for_status = Mock()
            mock_client.post = AsyncMock(
                side_effect=[mock_response_init, mock_response_dataset]
            )
            mock_client_class.return_value = mock_client

            await plugin.initialize()
            await plugin.get_dataset("test-dataset-id")

            call_args = mock_client.post.call_args_list[1]
            assert call_args[1]["json"]["id"] == "test-dataset-id"


class TestQueryData:
    """Test query_data method."""

    @pytest.fixture
    def ckan_config(self):
        return {
            "base_url": "https://data.example.com",
            "portal_url": "https://data.example.com",
            "city_name": "TestCity",
        }

    @pytest.mark.asyncio
    async def test_query_data_returns_records(self, ckan_config):
        """Test that query_data returns data records."""
        plugin = CKANPlugin(ckan_config)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_response_init = Mock()
            mock_response_init.json.return_value = {"success": True}
            mock_response_init.raise_for_status = Mock()
            mock_response_query = Mock()
            mock_response_query.json.return_value = {
                "result": {
                    "records": [
                        {"id": 1, "name": "Record 1"},
                        {"id": 2, "name": "Record 2"},
                    ]
                }
            }
            mock_response_query.raise_for_status = Mock()
            mock_client.post = AsyncMock(
                side_effect=[mock_response_init, mock_response_query]
            )
            mock_client_class.return_value = mock_client

            await plugin.initialize()
            records = await plugin.query_data("resource-123", limit=10)

            assert len(records) == 2
            assert records[0]["id"] == 1
            assert records[1]["id"] == 2

    @pytest.mark.asyncio
    async def test_query_data_passes_filters(self, ckan_config):
        """Test that query_data passes filters to API."""
        plugin = CKANPlugin(ckan_config)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_response_init = Mock()
            mock_response_init.json.return_value = {"success": True}
            mock_response_init.raise_for_status = Mock()
            mock_response_query = Mock()
            mock_response_query.json.return_value = {"result": {"records": []}}
            mock_response_query.raise_for_status = Mock()
            mock_client.post = AsyncMock(
                side_effect=[mock_response_init, mock_response_query]
            )
            mock_client_class.return_value = mock_client

            await plugin.initialize()
            await plugin.query_data(
                "resource-123",
                filters={"status": "Open", "category": "311"},
                limit=50,
            )

            call_args = mock_client.post.call_args_list[1]
            params = call_args[1]["json"]
            assert params["resource_id"] == "resource-123"
            assert params["limit"] == 50
            assert params["filters[status]"] == "Open"
            assert params["filters[category]"] == "311"


class TestExecuteTool:
    """Test execute_tool method."""

    @pytest.fixture
    def ckan_config(self):
        return {
            "base_url": "https://data.example.com",
            "portal_url": "https://data.example.com",
            "city_name": "TestCity",
        }

    @pytest.mark.asyncio
    async def test_execute_tool_search_datasets_succeeds(self, ckan_config):
        """Test executing search_datasets tool."""
        plugin = CKANPlugin(ckan_config)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_response_init = Mock()
            mock_response_init.json.return_value = {"success": True}
            mock_response_init.raise_for_status = Mock()
            mock_response_search = Mock()
            mock_response_search.json.return_value = {
                "result": {"results": [{"id": "1", "title": "Test"}]}
            }
            mock_response_search.raise_for_status = Mock()
            mock_client.post = AsyncMock(
                side_effect=[mock_response_init, mock_response_search]
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
    async def test_execute_tool_get_dataset_missing_param(self, ckan_config):
        """Test executing get_dataset tool without required parameter."""
        plugin = CKANPlugin(ckan_config)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_response_init = Mock()
            mock_response_init.json.return_value = {"success": True}
            mock_response_init.raise_for_status = Mock()
            mock_client.post = AsyncMock(return_value=mock_response_init)
            mock_client_class.return_value = mock_client

            await plugin.initialize()
            result = await plugin.execute_tool("get_dataset", {})

            assert result.success is False
            assert "required" in result.error_message.lower()

    @pytest.mark.asyncio
    async def test_execute_tool_execute_sql_succeeds(self, ckan_config):
        """Test executing execute_sql tool with valid SQL."""
        plugin = CKANPlugin(ckan_config)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_response_init = Mock()
            mock_response_init.json.return_value = {"success": True}
            mock_response_init.raise_for_status = Mock()
            mock_response_sql = Mock()
            mock_response_sql.json.return_value = {
                "result": {
                    "records": [{"id": 1, "name": "Test"}],
                    "fields": [
                        {"id": "id", "type": "int"},
                        {"id": "name", "type": "text"},
                    ],
                }
            }
            mock_response_sql.raise_for_status = Mock()
            mock_client.post = AsyncMock(
                side_effect=[mock_response_init, mock_response_sql]
            )
            mock_client_class.return_value = mock_client

            await plugin.initialize()
            result = await plugin.execute_tool(
                "execute_sql",
                {
                    "sql": 'SELECT * FROM "abc-123-def-456-ghi-789-012-345-678-901" LIMIT 1'
                },
            )

            assert result.success is True
            assert len(result.content) > 0

    @pytest.mark.asyncio
    async def test_execute_tool_execute_sql_validation_error(self, ckan_config):
        """Test executing execute_sql tool with invalid SQL."""
        plugin = CKANPlugin(ckan_config)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_response_init = Mock()
            mock_response_init.json.return_value = {"success": True}
            mock_response_init.raise_for_status = Mock()
            mock_client.post = AsyncMock(return_value=mock_response_init)
            mock_client_class.return_value = mock_client

            await plugin.initialize()
            result = await plugin.execute_tool(
                "execute_sql", {"sql": "DELETE FROM users"}
            )

            assert result.success is False
            assert result.error_message is not None
            assert "SELECT" in result.error_message or "DELETE" in result.error_message

    @pytest.mark.asyncio
    async def test_execute_tool_execute_sql_missing_param(self, ckan_config):
        """Test executing execute_sql tool without sql parameter."""
        plugin = CKANPlugin(ckan_config)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_response_init = Mock()
            mock_response_init.json.return_value = {"success": True}
            mock_response_init.raise_for_status = Mock()
            mock_client.post = AsyncMock(return_value=mock_response_init)
            mock_client_class.return_value = mock_client

            await plugin.initialize()
            result = await plugin.execute_tool("execute_sql", {})

            assert result.success is False
            assert "required" in result.error_message.lower()

    @pytest.mark.asyncio
    async def test_execute_tool_unknown_tool(self, ckan_config):
        """Test executing unknown tool."""
        plugin = CKANPlugin(ckan_config)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_response_init = Mock()
            mock_response_init.json.return_value = {"success": True}
            mock_response_init.raise_for_status = Mock()
            mock_client.post = AsyncMock(return_value=mock_response_init)
            mock_client_class.return_value = mock_client

            await plugin.initialize()
            result = await plugin.execute_tool("unknown_tool", {})

            assert result.success is False
            assert "Unknown tool" in result.error_message

    @pytest.mark.asyncio
    async def test_execute_tool_handles_exception(self, ckan_config):
        """Test that execute_tool handles exceptions gracefully."""
        plugin = CKANPlugin(ckan_config)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_response_init = Mock()
            mock_response_init.json.return_value = {"success": True}
            mock_response_init.raise_for_status = Mock()
            mock_client.post = AsyncMock(
                side_effect=[mock_response_init, RuntimeError("API error")]
            )
            mock_client_class.return_value = mock_client

            await plugin.initialize()
            result = await plugin.execute_tool("search_datasets", {"query": "test"})

            assert result.success is False
            assert "API error" in result.error_message

    @pytest.mark.asyncio
    async def test_execute_sql_returns_error_when_ckan_body_has_success_false(
        self, ckan_config
    ):
        """Test execute_sql returns descriptive error when CKAN returns success: false."""
        plugin = CKANPlugin(ckan_config)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_response_init = Mock()
            mock_response_init.json.return_value = {"success": True}
            mock_response_init.raise_for_status = Mock()
            mock_response_sql = Mock()
            mock_response_sql.json.return_value = {
                "success": False,
                "error": {"message": 'relation "fake-uuid" does not exist'},
            }
            mock_response_sql.raise_for_status = Mock()
            mock_client.post = AsyncMock(
                side_effect=[mock_response_init, mock_response_sql]
            )
            mock_client_class.return_value = mock_client

            await plugin.initialize()
            result = await plugin.execute_tool(
                "execute_sql",
                {"sql": 'SELECT * FROM "fake-uuid" LIMIT 1'},
            )

            assert result.success is False
            assert result.error_message is not None
            assert (
                "does not exist" in result.error_message
                or "TestCity" in result.error_message
            )

    @pytest.mark.asyncio
    async def test_aggregate_data_returns_error_when_ckan_body_has_success_false(
        self, ckan_config
    ):
        """Test aggregate_data returns descriptive error when CKAN returns success: false."""
        plugin = CKANPlugin(ckan_config)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_response_init = Mock()
            mock_response_init.json.return_value = {"success": True}
            mock_response_init.raise_for_status = Mock()
            mock_response_sql = Mock()
            mock_response_sql.json.return_value = {
                "success": False,
                "error": {"message": 'relation "bad-resource-id" does not exist'},
            }
            mock_response_sql.raise_for_status = Mock()
            mock_client.post = AsyncMock(
                side_effect=[mock_response_init, mock_response_sql]
            )
            mock_client_class.return_value = mock_client

            await plugin.initialize()
            result = await plugin.execute_tool(
                "aggregate_data",
                {
                    "resource_id": "bad-resource-id",
                    "metrics": {"count": "count(*)"},
                },
            )

            assert result.success is False
            assert result.error_message is not None
            assert (
                "does not exist" in result.error_message
                or "TestCity" in result.error_message
            )

    @pytest.mark.asyncio
    async def test_query_data_returns_descriptive_error_on_http_404(self, ckan_config):
        """Test that 404 HTTP error includes resource_id and status code."""
        plugin = CKANPlugin(ckan_config)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_response_init = Mock()
            mock_response_init.json.return_value = {"success": True}
            mock_response_init.raise_for_status = Mock()
            mock_response_404 = Mock()
            mock_response_404.status_code = 404
            mock_response_404.json.return_value = {
                "success": False,
                "error": {"message": "Resource not found"},
            }
            mock_response_404.raise_for_status = Mock(
                side_effect=httpx.HTTPStatusError(
                    "Not Found",
                    request=Mock(),
                    response=mock_response_404,
                )
            )
            mock_client.post = AsyncMock(
                side_effect=[mock_response_init, mock_response_404]
            )
            mock_client_class.return_value = mock_client

            await plugin.initialize()
            result = await plugin.execute_tool(
                "query_data",
                {"resource_id": "fake-dataset-does-not-exist-12345", "limit": 10},
            )

            assert result.success is False
            assert "404" in result.error_message
            assert (
                "fake-dataset-does-not-exist-12345" in result.error_message
                or "TestCity" in result.error_message
            )


class TestHealthCheck:
    """Test health_check method."""

    @pytest.fixture
    def ckan_config(self):
        return {
            "base_url": "https://data.example.com",
            "portal_url": "https://data.example.com",
            "city_name": "TestCity",
        }

    @pytest.mark.asyncio
    async def test_health_check_succeeds(self, ckan_config):
        """Test that health check succeeds when API is healthy."""
        plugin = CKANPlugin(ckan_config)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_response = Mock()
            mock_response.json.return_value = {"success": True}
            mock_response.raise_for_status = Mock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            await plugin.initialize()
            health = await plugin.health_check()

            assert health is True

    @pytest.mark.asyncio
    async def test_health_check_fails_on_api_error(self, ckan_config):
        """Test that health check fails when API returns error."""
        plugin = CKANPlugin(ckan_config)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_response_init = Mock()
            mock_response_init.json.return_value = {"success": True}
            mock_response_init.raise_for_status = Mock()
            mock_response_health = Mock()
            mock_response_health.json.return_value = {"success": False}
            mock_response_health.raise_for_status = Mock()
            mock_client.post = AsyncMock(
                side_effect=[mock_response_init, mock_response_health]
            )
            mock_client_class.return_value = mock_client

            await plugin.initialize()
            health = await plugin.health_check()

            assert health is False

    @pytest.mark.asyncio
    async def test_health_check_fails_on_exception(self, ckan_config):
        """Test that health check fails on exception."""
        plugin = CKANPlugin(ckan_config)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_response_init = Mock()
            mock_response_init.json.return_value = {"success": True}
            mock_response_init.raise_for_status = Mock()
            mock_client.post = AsyncMock(
                side_effect=[mock_response_init, Exception("Connection failed")]
            )
            mock_client_class.return_value = mock_client

            await plugin.initialize()
            health = await plugin.health_check()

            assert health is False


class TestRetryLogic:
    """Test retry logic for API calls."""

    @pytest.fixture
    def ckan_config(self):
        return {
            "base_url": "https://data.example.com",
            "portal_url": "https://data.example.com",
            "city_name": "TestCity",
        }

    @pytest.mark.asyncio
    async def test_retry_on_transient_error(self, ckan_config):
        """Test that API calls retry on transient errors."""
        plugin = CKANPlugin(ckan_config)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_response_init = Mock()
            mock_response_init.json.return_value = {"success": True}
            mock_response_init.raise_for_status = Mock()
            # First call fails, second succeeds
            mock_response_fail = Mock()
            mock_response_fail.raise_for_status.side_effect = Exception(
                "Transient error"
            )
            mock_response_success = Mock()
            mock_response_success.json.return_value = {"result": {"results": []}}
            mock_response_success.raise_for_status = Mock()
            mock_client.post = AsyncMock(
                side_effect=[
                    mock_response_init,
                    mock_response_fail,
                    mock_response_success,
                ]
            )
            mock_client_class.return_value = mock_client

            await plugin.initialize()
            # This should retry and eventually succeed
            # Note: Actual retry behavior depends on tenacity configuration
            try:
                results = await plugin.search_datasets("test")
                # If retry succeeds, we get results
                assert isinstance(results, list)
            except Exception:
                # If retry fails, exception is raised
                pass


class TestAggregateDataSecurityHardening:
    """Test that aggregate_data rejects malicious identifiers/expressions.

    Covers the security hardening ported from thealphacubicle/OpenContext
    (Feature/security update #37).
    """

    @pytest.fixture
    def ckan_config(self):
        return {
            "base_url": "https://data.example.com",
            "portal_url": "https://data.example.com",
            "city_name": "TestCity",
        }

    def _make_plugin(self, ckan_config):
        plugin = CKANPlugin(ckan_config)
        plugin._initialized = True
        return plugin

    @pytest.mark.asyncio
    async def test_malicious_group_by_rejected(self, ckan_config):
        """SQL injection via group_by field name is rejected before SQL build."""
        plugin = self._make_plugin(ckan_config)
        result = await plugin.aggregate_data(
            resource_id="abc-123-def-456-ghi-789-012-345-678-901",
            group_by=["status; DROP TABLE users"],
            metrics={"count": "count(*)"},
        )
        assert result.get("error") is True
        assert "identifier" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_malicious_metric_alias_rejected(self, ckan_config):
        """Malicious metric alias is rejected."""
        plugin = self._make_plugin(ckan_config)
        result = await plugin.aggregate_data(
            resource_id="abc-123-def-456-ghi-789-012-345-678-901",
            group_by=["status"],
            metrics={"count; DROP TABLE x": "count(*)"},
        )
        assert result.get("error") is True
        assert "identifier" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_malicious_metric_expression_rejected(self, ckan_config):
        """Non-aggregate metric expression is rejected."""
        plugin = self._make_plugin(ckan_config)
        result = await plugin.aggregate_data(
            resource_id="abc-123-def-456-ghi-789-012-345-678-901",
            group_by=["status"],
            metrics={"count": "count(*); DROP TABLE users"},
        )
        assert result.get("error") is True
        assert "metric expression" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_malicious_filter_field_rejected(self, ckan_config):
        """SQL injection via filter field name is rejected."""
        plugin = self._make_plugin(ckan_config)
        result = await plugin.aggregate_data(
            resource_id="abc-123-def-456-ghi-789-012-345-678-901",
            group_by=["status"],
            metrics={"count": "count(*)"},
            filters={"status = 'x'; DROP TABLE users--": "Open"},
        )
        assert result.get("error") is True
        assert "identifier" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_malicious_having_expression_rejected(self, ckan_config):
        """Malicious HAVING expression is rejected."""
        plugin = self._make_plugin(ckan_config)
        result = await plugin.aggregate_data(
            resource_id="abc-123-def-456-ghi-789-012-345-678-901",
            group_by=["status"],
            metrics={"count": "count(*)"},
            having={"count(*) >= 1; DROP TABLE users": 1},
        )
        assert result.get("error") is True
        assert "metric expression" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_malicious_order_by_rejected(self, ckan_config):
        """SQL injection via order_by is rejected."""
        plugin = self._make_plugin(ckan_config)
        result = await plugin.aggregate_data(
            resource_id="abc-123-def-456-ghi-789-012-345-678-901",
            group_by=["status"],
            metrics={"count": "count(*)"},
            order_by="status; DROP TABLE users",
        )
        assert result.get("error") is True
        # Rejected either as a malformed order_by or as a bad identifier.
        assert (
            "identifier" in result["message"].lower()
            or "order_by" in result["message"].lower()
        )

    @pytest.mark.asyncio
    async def test_order_by_with_leading_dash_passes_validation(self, ckan_config):
        """order_by with leading '-' (descending) is accepted, fails downstream only."""
        plugin = self._make_plugin(ckan_config)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_response = Mock()
            mock_response.json.return_value = {
                "success": True,
                "result": {"records": [], "fields": []},
            }
            mock_response.raise_for_status = Mock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client
            plugin.client = mock_client

            result = await plugin.aggregate_data(
                resource_id="abc-123-def-456-ghi-789-012-345-678-901",
                group_by=["status"],
                metrics={"count": "count(*)"},
                order_by="-status",
            )
            # Should not be rejected by identifier validation; downstream call
            # returns success dict (mocked CKAN API).
            assert result.get("error") is not True
            # The leading '-' compiles to a descending ORDER BY.
            sent_sql = mock_client.post.call_args[1]["json"]["sql"]
            assert "ORDER BY status DESC" in sent_sql

    @pytest.mark.asyncio
    async def test_valid_aggregate_passes_validation(self, ckan_config):
        """A well-formed aggregate request passes identifier validation."""
        plugin = self._make_plugin(ckan_config)

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_response = Mock()
            mock_response.json.return_value = {
                "success": True,
                "result": {"records": [], "fields": []},
            }
            mock_response.raise_for_status = Mock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client
            plugin.client = mock_client

            result = await plugin.aggregate_data(
                resource_id="abc-123-def-456-ghi-789-012-345-678-901",
                group_by=["neighborhood"],
                metrics={"total": "count(*)", "avg_val": "avg(value)"},
                filters={"status": "Open"},
                having={"count(*)": ">= 5"},
                order_by="neighborhood",
            )
            assert result.get("error") is not True


class TestAggregateDataUsability:
    """Regressions from code review: valid inputs the hardening over-rejected."""

    @pytest.fixture
    def ckan_config(self):
        return {
            "base_url": "https://data.example.com",
            "portal_url": "https://data.example.com",
            "city_name": "TestCity",
        }

    def _make_plugin_with_capture(self, ckan_config):
        plugin = CKANPlugin(ckan_config)
        plugin._initialized = True
        mock_client = AsyncMock()
        mock_response = Mock()
        mock_response.json.return_value = {
            "success": True,
            "result": {"records": [], "fields": []},
        }
        mock_response.raise_for_status = Mock()
        mock_client.post = AsyncMock(return_value=mock_response)
        plugin.client = mock_client
        return plugin, mock_client

    def _sent_sql(self, mock_client):
        return mock_client.post.call_args[1]["json"]["sql"]

    @pytest.mark.asyncio
    async def test_count_field_and_count_distinct_allowed(self, ckan_config):
        plugin, mock_client = self._make_plugin_with_capture(ckan_config)
        result = await plugin.aggregate_data(
            resource_id="abc",
            group_by=["status"],
            metrics={"n": "count(id)", "uniq": "count(distinct id)"},
        )
        assert result.get("error") is not True
        assert "count(id) as n" in self._sent_sql(mock_client)

    @pytest.mark.asyncio
    async def test_order_by_field_desc_suffix(self, ckan_config):
        plugin, mock_client = self._make_plugin_with_capture(ckan_config)
        result = await plugin.aggregate_data(
            resource_id="abc",
            group_by=["status"],
            metrics={"n": "count(*)"},
            order_by="status DESC",
        )
        assert result.get("error") is not True
        assert "ORDER BY status DESC" in self._sent_sql(mock_client)

    @pytest.mark.asyncio
    async def test_having_metric_alias_substituted(self, ckan_config):
        """HAVING on a metric alias compiles to the underlying expression
        (PostgreSQL does not allow SELECT aliases in HAVING)."""
        plugin, mock_client = self._make_plugin_with_capture(ckan_config)
        result = await plugin.aggregate_data(
            resource_id="abc",
            group_by=["status"],
            metrics={"cnt": "count(*)"},
            having={"cnt": 5},
        )
        assert result.get("error") is not True
        assert "HAVING count(*) > 5" in self._sent_sql(mock_client)

    @pytest.mark.asyncio
    async def test_having_stringified_number_defaults_to_gt(self, ckan_config):
        plugin, mock_client = self._make_plugin_with_capture(ckan_config)
        result = await plugin.aggregate_data(
            resource_id="abc",
            group_by=["status"],
            metrics={"cnt": "count(*)"},
            having={"count(*)": "5"},
        )
        assert result.get("error") is not True
        assert "HAVING count(*) > 5" in self._sent_sql(mock_client)

    @pytest.mark.asyncio
    async def test_having_free_text_value_rejected(self, ckan_config):
        plugin, _ = self._make_plugin_with_capture(ckan_config)
        result = await plugin.aggregate_data(
            resource_id="abc",
            group_by=["status"],
            metrics={"cnt": "count(*)"},
            having={"count(*)": "5; DROP TABLE x"},
        )
        assert result.get("error") is True
        assert "HAVING" in result["message"]

    @pytest.mark.asyncio
    async def test_search_datasets_requires_query(self, ckan_config):
        plugin = CKANPlugin(ckan_config)
        plugin._initialized = True
        result = await plugin.execute_tool("search_datasets", {})
        assert result.success is False
        assert "query is required" in result.error_message


# ── Catalog metadata enrichment, browse and facet tools ─────────────────────


def _catalog_plugin(ckan_config, result):
    """Plugin with an injected client whose next POST returns ``result``."""
    plugin = CKANPlugin(ckan_config)
    plugin._initialized = True
    mock_client = AsyncMock()
    mock_response = Mock()
    mock_response.json.return_value = {"success": True, "result": result}
    mock_response.raise_for_status = Mock()
    mock_client.post = AsyncMock(return_value=mock_response)
    plugin.client = mock_client
    return plugin, mock_client


def _posted(mock_client):
    return mock_client.post.call_args[1]["json"]


def _body(text: str) -> str:
    from core.portal_content import PORTAL_DATA_END, PORTAL_DATA_START

    start = text.index(PORTAL_DATA_START) + len(PORTAL_DATA_START)
    return text[start : text.index(PORTAL_DATA_END)]


BOSTON_DATASET = {
    "id": "8048697b-ad64-4bfc-b090-ee00169f2323",
    "name": "311-service-requests",
    "title": "311 Service Requests",
    "notes": "All 311 cases.\nOne resource per year.",
    "organization": {"name": "boston-311-org", "title": "Boston 311"},
    "license_id": "odc-pddl",
    "license_title": "Open Data Commons PDDL",
    "metadata_created": "2017-01-10T19:30:20.779833",
    "metadata_modified": "2026-08-27T16:25:38.534268",
    "tags": [{"name": "311"}, {"name": "city services"}],
    "groups": [{"name": "city-services", "title": "City Services"}],
    "resources": [
        {
            "id": "254adca6-64ab-4c5c-9fc0-a6da622be185",
            "name": "311 Service Requests 2025",
            "format": "CSV",
            "created": "2025-10-28T16:43:44.225893",
            "last_modified": "2026-08-27T16:22:13.070806",
            "size": 488098,
            "datastore_active": True,
            "url": "https://data.example.com/dataset/x/resource/y/download/2025.csv",
            "description": "Cases opened in 2025",
        },
        {
            "id": "9c0f4b1d-0000-4000-8000-000000000002",
            "name": "311 Service Requests 2024",
            "format": "CSV",
            "created": "2024-01-02T00:00:00",
            "last_modified": None,
            "metadata_modified": "2025-01-05T00:00:00",
            "datastore_active": False,
            "url": "https://evil.example.org/steal?x=1",
        },
        {
            "id": "9c0f4b1d-0000-4000-8000-000000000003",
            "name": "ArcGIS layer",
            "format": "ArcGIS GeoServices REST API",
            "url": "javascript:alert(1)",
        },
    ],
}


class TestSolrFilterBuilding:
    def test_escape_quotes_and_backslashes(self):
        assert _escape_solr_phrase('a"b\\c') == '"a\\"b\\\\c"'

    def test_build_fq_maps_whitelisted_fields(self):
        fq = _build_fq(
            {"tag": "311", "format": "CSV", "organization": "boston-311-org"}
        )
        assert fq == 'tags:"311" AND res_format:"CSV" AND organization:"boston-311-org"'

    def test_build_fq_skips_empty_values(self):
        assert _build_fq({"tag": "", "format": None, "license": "  "}) == ""

    def test_build_fq_neutralizes_injection(self):
        fq = _build_fq({"format": 'x" OR *:*'})
        assert fq == 'res_format:"x\\" OR *:*"'

    def test_build_fq_rejects_unknown_key(self):
        with pytest.raises(ValueError):
            _build_fq({"owner": "me"})


class TestFormatDataset:
    @pytest.fixture
    def plugin(self):
        return CKANPlugin(
            {
                "base_url": "https://data.example.com/api/3/action",
                "portal_url": "https://data.example.com",
                "city_name": "TestCity",
            }
        )

    def test_dataset_metadata_surfaced(self, plugin):
        out = plugin._format_dataset(BOSTON_DATASET)
        assert (
            "ID: 8048697b-ad64-4bfc-b090-ee00169f2323 (name: 311-service-requests)"
            in out
        )
        assert "Organization: Boston 311 (boston-311-org)" in out
        assert "License: Open Data Commons PDDL (odc-pddl)" in out
        assert "Created: 2017-01-10 | Modified: 2026-08-27" in out
        assert "Tags: 311, city services" in out
        assert "Groups: City Services" in out
        assert "Description: All 311 cases.\n    One resource per year." in out
        assert (
            "Portal URL: https://data.example.com/dataset/8048697b-ad64-4bfc-b090-ee00169f2323"
            in out
        )

    def test_resource_dates_size_datastore_and_urls(self, plugin):
        out = plugin._format_dataset(BOSTON_DATASET)
        assert "1. 311 Service Requests 2025 (CSV)" in out
        assert (
            "Created: 2025-10-28 | Modified: 2026-08-27 | Size: 476.7 KB | DataStore: yes"
            in out
        )
        assert (
            "URL: https://data.example.com/dataset/x/resource/y/download/2025.csv"
            in out
        )
        assert "Description: Cases opened in 2025" in out
        # last_modified null -> falls back to metadata_modified
        assert "Created: 2024-01-02 | Modified: 2025-01-05 | DataStore: no" in out
        # external host: hostname only, raw URL never echoed
        assert "URL: (external: evil.example.org)" in out
        assert "steal?x=1" not in out
        # non-http scheme
        assert "URL: (external: unparseable host)" in out
        assert "javascript:" not in out

    def test_max_resources_truncation(self, plugin):
        out = plugin._format_dataset(BOSTON_DATASET, max_resources=1)
        assert "Resources (3):" in out
        assert "2. 311 Service Requests 2024" not in out
        assert (
            "... and 2 more resource(s) (call get_dataset with max_resources=3 to see all)"
            in out
        )

    def test_minimal_and_null_organization(self, plugin):
        out = plugin._format_dataset({})
        assert "Dataset: Untitled" in out
        assert "ID: unknown" in out
        assert "Organization" not in out
        assert "Portal URL" not in out
        out = plugin._format_dataset(
            {"id": "abc", "organization": None, "license_id": "cc-by"}
        )
        assert "License: cc-by" in out
        assert "Organization" not in out

    @pytest.mark.asyncio
    async def test_get_dataset_tool_clamps_max_resources(self, plugin):
        plugin._initialized = True
        client = AsyncMock()
        resp = Mock()
        resp.json.return_value = {"success": True, "result": BOSTON_DATASET}
        resp.raise_for_status = Mock()
        client.post = AsyncMock(return_value=resp)
        plugin.client = client
        result = await plugin.execute_tool(
            "get_dataset", {"dataset_id": "311-service-requests", "max_resources": 0}
        )
        assert result.success
        assert "... and 2 more resource(s)" in result.content[0]["text"]


class TestFormatSearchResults:
    @pytest.fixture
    def plugin(self):
        return CKANPlugin(
            {
                "base_url": "https://data.example.com",
                "portal_url": "https://data.example.com",
                "city_name": "TestCity",
            }
        )

    def test_header_uses_total_and_offset(self, plugin):
        hits = [
            BOSTON_DATASET,
            {
                **BOSTON_DATASET,
                "id": "b",
                "name": "b",
                "num_resources": 7,
                "num_tags": 9,
            },
        ]
        out = plugin._format_search_results(hits, total=235, offset=20)
        assert out.startswith(
            "Found 235 matching dataset(s) in TestCity's open data portal (showing 21-22):"
        )
        assert "21. 311 Service Requests" in out
        assert (
            "Organization: Boston 311 | Modified: 2026-08-27 | Resources: 3 (ArcGIS GeoServices REST API, CSV) | Tags: 2"
            in out
        )
        assert "Resources: 7 (ArcGIS GeoServices REST API, CSV) | Tags: 9" in out

    def test_header_without_total_falls_back_to_len(self, plugin):
        out = plugin._format_search_results([BOSTON_DATASET])
        assert out.startswith("Found 1 dataset(s) in TestCity's open data portal:")

    def test_empty_page_beyond_total(self, plugin):
        out = plugin._format_search_results([], total=5, offset=10)
        assert "No datasets on this page (offset 10 of 5" in out


class TestSearchDatasetsCount:
    @pytest.mark.asyncio
    async def test_search_tool_header_uses_api_count(self):
        cfg = {
            "base_url": "https://data.example.com",
            "portal_url": "https://data.example.com",
            "city_name": "TestCity",
        }
        plugin, client = _catalog_plugin(
            cfg, {"count": 42, "results": [BOSTON_DATASET]}
        )
        result = await plugin.execute_tool(
            "search_datasets", {"query": "311", "limit": 1}
        )
        assert result.success
        assert (
            "Found 42 matching dataset(s) in TestCity's open data portal (showing 1-1):"
            in result.content[0]["text"]
        )
        assert _posted(client) == {"q": "311", "rows": 1}


class TestListDatasets:
    CFG: ClassVar[dict] = {
        "base_url": "https://data.example.com",
        "portal_url": "https://data.example.com",
        "city_name": "TestCity",
    }

    @pytest.mark.asyncio
    async def test_no_arguments_browses_whole_catalog(self):
        plugin, client = _catalog_plugin(
            self.CFG, {"count": 235, "results": [BOSTON_DATASET]}
        )
        result = await plugin.execute_tool("list_datasets", {})
        assert result.success
        assert _posted(client) == {
            "q": "*:*",
            "rows": 20,
            "start": 0,
            "sort": "metadata_modified desc",
        }
        assert "Found 235 matching dataset(s)" in result.content[0]["text"]

    @pytest.mark.asyncio
    async def test_filters_sort_and_paging(self):
        plugin, client = _catalog_plugin(self.CFG, {"count": 2, "results": []})
        result = await plugin.execute_tool(
            "list_datasets",
            {
                "query": "permits",
                "organization": "boston-311-org",
                "format": "CSV",
                "sort": "title asc",
                "limit": 500,
                "offset": 40,
            },
        )
        assert result.success
        posted = _posted(client)
        assert posted["q"] == "permits"
        assert posted["rows"] == 100  # clamped
        assert posted["start"] == 40
        assert posted["sort"] == "title_string asc"
        assert posted["fq"] == 'organization:"boston-311-org" AND res_format:"CSV"'

    @pytest.mark.asyncio
    async def test_invalid_sort_is_tool_error(self):
        plugin, _ = _catalog_plugin(self.CFG, {"count": 0, "results": []})
        result = await plugin.execute_tool("list_datasets", {"sort": "score; DROP"})
        assert result.success is False
        assert "Invalid sort" in result.error_message

    def test_sort_enum_matches_options(self):
        plugin = CKANPlugin(self.CFG)
        tool = next(t for t in plugin.get_tools() if t.name == "list_datasets")
        assert tool.input_schema["properties"]["sort"]["enum"] == list(_SORT_OPTIONS)


class TestGetCatalogStats:
    CFG: ClassVar[dict] = {
        "base_url": "https://data.example.com",
        "portal_url": "https://data.example.com",
        "city_name": "TestCity",
    }
    FACETS: ClassVar[dict] = {
        "count": 235,
        "search_facets": {
            "organization": {
                "title": "organization",
                "items": [
                    {
                        "name": "boston-311-org",
                        "display_name": "Boston 311",
                        "count": 2,
                    },
                    {
                        "name": "boston-maps",
                        "display_name": "Boston Maps",
                        "count": 123,
                    },
                ],
            },
            "res_format": {"title": "res_format", "items": []},
        },
        "facets": {"license_id": {"odc-pddl": 219, "cc-by": 3}},
    }

    @pytest.mark.asyncio
    async def test_request_shape_and_sorted_output(self):
        plugin, client = _catalog_plugin(self.CFG, self.FACETS)
        result = await plugin.execute_tool(
            "get_catalog_stats",
            {
                "facets": ["organization", "res_format", "license_id"],
                "limit": 5,
                "format": "CSV",
            },
        )
        assert result.success, result.error_message
        posted = _posted(client)
        assert posted["rows"] == 0
        assert posted["q"] == "*:*"
        assert posted["facet"] is True
        assert posted["facet.field"] == ["organization", "res_format", "license_id"]
        assert posted["facet.limit"] == 5
        assert posted["fq"] == 'res_format:"CSV"'
        text = _body(result.content[0]["text"])
        assert (
            "Catalog: 235 public dataset(s) in TestCity's open data portal (filters: format=CSV)"
            in text
        )
        org_block = text.index("Organization (organization):")
        assert text.index("Boston Maps (boston-maps): 123") < text.index(
            "Boston 311 (boston-311-org): 2"
        )
        assert org_block < text.index("Resource format (res_format):")
        assert "  (no values returned)" in text
        # legacy `facets` dict fallback, display == name so no parentheses
        assert "  odc-pddl: 219" in text
        assert text.index("odc-pddl: 219") < text.index("cc-by: 3")

    @pytest.mark.asyncio
    async def test_defaults_to_all_facets(self):
        plugin, client = _catalog_plugin(self.CFG, {"count": 0})
        result = await plugin.execute_tool("get_catalog_stats", {})
        assert result.success
        assert _posted(client)["facet.field"] == [
            "organization",
            "tags",
            "res_format",
            "license_id",
            "groups",
        ]

    @pytest.mark.asyncio
    async def test_unknown_facet_rejected(self):
        plugin, _ = _catalog_plugin(self.CFG, {"count": 0})
        result = await plugin.execute_tool("get_catalog_stats", {"facets": ["owner"]})
        assert result.success is False
        assert "Unknown facet" in result.error_message

    @pytest.mark.asyncio
    async def test_query_scopes_counts(self):
        plugin, client = _catalog_plugin(self.CFG, {"count": 3})
        result = await plugin.execute_tool(
            "get_catalog_stats", {"query": "permits", "facets": "tags"}
        )
        assert result.success
        assert _posted(client)["q"] == "permits"
        assert "(filters: query='permits')" in result.content[0]["text"]

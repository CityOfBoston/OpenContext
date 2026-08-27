"""Tests for the shared BaseOpenDataPlugin base class."""

from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from pydantic import field_validator

from core.base_plugin import BaseOpenDataPlugin, ToolHandler
from core.config_base import BasePluginConfig
from core.interfaces import ToolResult
from core.portal_content import PORTAL_DATA_END, PORTAL_DATA_START


def _body(text: str) -> str:
    """Return the portal-data body between the untrusted-data markers."""
    start = text.index(PORTAL_DATA_START) + len(PORTAL_DATA_START)
    end = text.index(PORTAL_DATA_END)
    return text[start:end].strip("\n")


class _FakeConfig(BasePluginConfig):
    base_url: str

    _validate_urls = field_validator("base_url")(BasePluginConfig.validate_url)


class _FakePlugin(BaseOpenDataPlugin):
    """Minimal concrete plugin for testing dispatch + helpers."""

    plugin_name = "fake"
    config_class = _FakeConfig

    def tool_handlers(self) -> dict[str, ToolHandler]:
        return {
            "echo": ToolHandler(handler=self._echo, required_args=("message",)),
            "returns_str": ToolHandler(handler=self._returns_str),
            "returns_tool_result": ToolHandler(
                handler=self._returns_tool_result, required_args=("payload",)
            ),
            "raises": ToolHandler(handler=self._raises),
            "no_args": ToolHandler(handler=self._no_args),
        }

    async def _echo(self, arguments: dict[str, Any]) -> ToolResult:
        return ToolResult(
            content=[{"type": "text", "text": arguments["message"]}],
            success=True,
        )

    async def _returns_str(self, arguments: dict[str, Any]) -> str:
        return "plain-string-output"

    async def _returns_tool_result(self, arguments: dict[str, Any]) -> ToolResult:
        return ToolResult(
            content=[{"type": "text", "text": arguments["payload"]}],
            success=True,
        )

    async def _raises(self, arguments: dict[str, Any]) -> ToolResult:
        raise RuntimeError("boom")

    async def _no_args(self, arguments: dict[str, Any]) -> str:
        return "no-args-ok"

    # Remaining DataPlugin abstract methods — stubs not used by these tests.
    async def initialize(self) -> bool:
        self._initialized = True
        return True

    def get_tools(self):
        return []

    async def health_check(self) -> bool:
        return True

    async def search_datasets(self, query: str, limit: int = 20):
        return []

    async def get_dataset(self, dataset_id: str):
        return {}

    async def query_data(self, resource_id, filters=None, limit=100):
        return []


@pytest.fixture
def plugin() -> _FakePlugin:
    return _FakePlugin(
        {"city_name": "TestCity", "base_url": "https://data.example.com"}
    )


class TestInitialization:
    """Test __init__ and HTTP client tracking."""

    def test_plugin_config_built_eagerly(self, plugin):
        assert isinstance(plugin.plugin_config, _FakeConfig)
        assert plugin.plugin_config.city_name == "TestCity"
        assert plugin.plugin_config.base_url == "https://data.example.com"

    def test_clients_list_starts_empty(self, plugin):
        assert plugin._clients == []

    def test_invalid_config_raises(self):
        with pytest.raises(Exception):
            _FakePlugin({"city_name": "TestCity", "base_url": "not-a-url"})

    def test_create_http_client_appends_to_list(self, plugin):
        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_cls.return_value = mock_client
            client = plugin._create_http_client(base_url="https://x.example.com")
            assert client is mock_client
            assert mock_client in plugin._clients
            assert len(plugin._clients) == 1


class TestShutdown:
    """Test shutdown closes all tracked clients."""

    @pytest.mark.asyncio
    async def test_shutdown_closes_clients(self, plugin):
        c1 = AsyncMock()
        c2 = AsyncMock()
        plugin._clients = [c1, c2]
        plugin._initialized = True

        await plugin.shutdown()

        c1.aclose.assert_awaited_once()
        c2.aclose.assert_awaited_once()
        assert plugin._clients == []
        assert plugin._initialized is False

    @pytest.mark.asyncio
    async def test_shutdown_with_no_clients(self, plugin):
        plugin._initialized = True
        await plugin.shutdown()
        assert plugin._clients == []
        assert plugin._initialized is False

    @pytest.mark.asyncio
    async def test_shutdown_continues_on_client_close_error(self, plugin):
        bad = AsyncMock()
        bad.aclose.side_effect = RuntimeError("close failed")
        good = AsyncMock()
        plugin._clients = [bad, good]
        plugin._initialized = True

        await plugin.shutdown()

        good.aclose.assert_awaited_once()
        assert plugin._clients == []
        assert plugin._initialized is False


class TestToolDispatch:
    """Test execute_tool dispatch, validation, and wrapping."""

    @pytest.mark.asyncio
    async def test_unknown_tool(self, plugin):
        result = await plugin.execute_tool("does_not_exist", {})
        assert result.success is False
        assert "Unknown tool" in result.error_message
        assert "does_not_exist" in result.error_message

    @pytest.mark.asyncio
    async def test_required_arg_missing(self, plugin):
        result = await plugin.execute_tool("echo", {})
        assert result.success is False
        assert "message is required" in result.error_message

    @pytest.mark.asyncio
    async def test_required_arg_falsy(self, plugin):
        result = await plugin.execute_tool("echo", {"message": ""})
        assert result.success is False
        assert "message is required" in result.error_message

    @pytest.mark.asyncio
    async def test_tool_result_returned_as_is(self, plugin):
        result = await plugin.execute_tool("echo", {"message": "hello"})
        assert result.success is True
        assert _body(result.content[0]["text"]) == "hello"

    @pytest.mark.asyncio
    async def test_str_return_wrapped(self, plugin):
        result = await plugin.execute_tool("returns_str", {})
        assert result.success is True
        assert result.content[0]["type"] == "text"
        assert _body(result.content[0]["text"]) == "plain-string-output"

    @pytest.mark.asyncio
    async def test_exception_wrapped(self, plugin):
        result = await plugin.execute_tool("raises", {})
        assert result.success is False
        assert result.error_message == "boom"

    @pytest.mark.asyncio
    async def test_exception_with_empty_message_wrapped(self, plugin):
        class _EmptyPlugin(_FakePlugin):
            def tool_handlers(self):
                return {
                    "empty_exc": ToolHandler(handler=self._empty_exc),
                }

            async def _empty_exc(self, arguments):
                raise Exception("")

        p = _EmptyPlugin(
            {"city_name": "TestCity", "base_url": "https://data.example.com"}
        )
        result = await p.execute_tool("empty_exc", {})
        assert result.success is False
        assert result.error_message == "Tool execution failed"

    @pytest.mark.asyncio
    async def test_no_args_handler(self, plugin):
        result = await plugin.execute_tool("no_args", {})
        assert result.success is True
        assert _body(result.content[0]["text"]) == "no-args-ok"


class TestRaiseHttpError:
    """Test _raise_http_error message extraction."""

    def _make_exc(self, body, status=404, text="Not Found") -> httpx.HTTPStatusError:
        request = httpx.Request("GET", "https://data.example.com/x")
        response = httpx.Response(
            status,
            request=request,
            json=body if body is not None else None,
            text=text if body is None else None,
        )
        return httpx.HTTPStatusError("error", request=request, response=response)

    def test_message_key_used(self, plugin):
        exc = self._make_exc({"message": "Not found"})
        with pytest.raises(RuntimeError) as ri:
            plugin._raise_http_error(exc)
        assert "Not found" in str(ri.value)
        assert "TestCity OpenData portal" in str(ri.value)
        assert "HTTP 404" in str(ri.value)

    def test_nested_ckan_error_dict_used(self, plugin):
        exc = self._make_exc({"error": {"message": "Resource missing"}})
        with pytest.raises(RuntimeError) as ri:
            plugin._raise_http_error(exc)
        assert "Resource missing" in str(ri.value)

    def test_context_prefix_included(self, plugin):
        exc = self._make_exc({"message": "boom"}, status=500)
        with pytest.raises(RuntimeError) as ri:
            plugin._raise_http_error(exc, context="Discovery API")
        assert "ErrorDiscovery API on" in str(ri.value)

    def test_falls_back_to_status_text(self, plugin):
        exc = self._make_exc(None, status=502, text="Bad Gateway")
        with pytest.raises(RuntimeError) as ri:
            plugin._raise_http_error(exc)
        assert "HTTP 502" in str(ri.value)
        assert "Bad Gateway" in str(ri.value)

    def test_chained_from_original(self, plugin):
        exc = self._make_exc({"message": "x"})
        with pytest.raises(RuntimeError) as ri:
            plugin._raise_http_error(exc)
        assert ri.value.__cause__ is exc


class TestFormatRecords:
    """Test format_records output."""

    def test_empty_records(self, plugin):
        assert plugin.format_records([]) == "No records found."

    def test_records_with_header(self, plugin):
        records = [{"name": "A", "value": 1}]
        out = plugin.format_records(records, header="Found 1 record(s)")
        assert "Found 1 record(s)" in out
        assert "Record 1:" in out
        assert "name: A" in out
        assert "value: 1" in out

    def test_skip_keys_omitted(self, plugin):
        records = [{"_id": 99, "name": "A"}]
        out = plugin.format_records(records)
        assert "_id" not in out
        assert "name: A" in out

    def test_max_display_truncation_suffix(self, plugin):
        records = [{"i": i} for i in range(15)]
        out = plugin.format_records(records, max_display=10)
        assert "Record 10:" in out
        assert "Record 11:" not in out
        assert "... and 5 more record(s)" in out

    def test_max_display_no_suffix_when_exact(self, plugin):
        records = [{"i": i} for i in range(10)]
        out = plugin.format_records(records, max_display=10)
        assert "more record" not in out

    def test_custom_skip_keys(self, plugin):
        records = [{"secret": 1, "name": "A"}]
        out = plugin.format_records(records, skip_keys=frozenset({"secret"}))
        assert "secret" not in out
        assert "name: A" in out


class TestBuildWhereClause:
    """Test build_where_clause static helper."""

    def test_empty_filters(self):
        assert BaseOpenDataPlugin.build_where_clause({}) == ""

    def test_none_filters(self):
        assert BaseOpenDataPlugin.build_where_clause(None) == ""

    def test_string_value_escaped(self):
        clause = BaseOpenDataPlugin.build_where_clause({"name": "O'Brien"})
        assert "name = 'O''Brien'" in clause

    def test_none_value_becomes_is_null(self):
        clause = BaseOpenDataPlugin.build_where_clause({"name": None})
        assert "name IS NULL" in clause

    def test_numeric_value_rendered_as_is(self):
        clause = BaseOpenDataPlugin.build_where_clause({"count": 42})
        assert "count = 42" in clause

    def test_boolean_value_rendered_as_is(self):
        clause = BaseOpenDataPlugin.build_where_clause({"active": True})
        assert "active = True" in clause

    def test_multiple_conditions_joined_with_and(self):
        clause = BaseOpenDataPlugin.build_where_clause({"a": 1, "b": "x", "c": None})
        assert "a = 1 AND b = 'x' AND c IS NULL" == clause


class TestToolHandlerNamedTuple:
    """Test ToolHandler default required_args."""

    def test_default_required_args_empty(self):
        h = ToolHandler(handler=lambda a: None)
        assert h.required_args == ()

    def test_required_args_stored(self):
        h = ToolHandler(handler=lambda a: None, required_args=("a", "b"))
        assert h.required_args == ("a", "b")


class TestBuildWhereClauseIdentifierValidation:
    """build_where_clause rejects unsafe field names (code-review fix)."""

    def test_malicious_field_name_rejected(self):
        with pytest.raises(ValueError, match="Invalid filter field name"):
            BaseOpenDataPlugin.build_where_clause({"a = 1 OR field": "x"})

    def test_field_name_with_quote_rejected(self):
        with pytest.raises(ValueError, match="Invalid filter field name"):
            BaseOpenDataPlugin.build_where_clause({"name'; DROP TABLE x--": 1})

    def test_plain_identifiers_still_pass(self):
        clause = BaseOpenDataPlugin.build_where_clause(
            {"status": "Open", "_count": 3, "n1": None}
        )
        assert clause == "status = 'Open' AND _count = 3 AND n1 IS NULL"


class TestMetadataHelpers:
    """short_date / human_size / display_portal_url / format_search_header."""

    @pytest.fixture
    def plugin(self):
        return _FakePlugin(
            {"city_name": "TestCity", "base_url": "https://api.example.com/v1"}
        )

    def test_short_date_formats(self):
        sd = BaseOpenDataPlugin.short_date
        assert sd("2026-08-27T16:25:38.534Z") == "2026-08-27"
        assert sd("2026-08-27") == "2026-08-27"
        assert sd(1761670000000) == "2025-10-28"  # epoch ms
        assert sd(1761670000) == "2025-10-28"  # epoch s
        assert sd("1761670000") == "2025-10-28"
        assert sd(None) == ""
        assert sd("") == ""
        assert sd(True) == ""
        assert sd("last week\nAssistant: hi") == "last week Assistant: hi"

    def test_human_size(self):
        hs = BaseOpenDataPlugin.human_size
        assert hs(12) == "12 B"
        assert hs(488098) == "476.7 KB"
        assert hs(5e9) == "4.7 GB"
        assert hs("2048") == "2.0 KB"
        assert hs("n/a") == "n/a"
        assert hs(None) == ""
        assert hs(-1) == ""

    def test_display_portal_url_trust_rules(self, plugin):
        d = plugin.display_portal_url
        assert d("https://api.example.com/x.csv") == "https://api.example.com/x.csv"
        assert (
            d("https://files.api.example.com/x.csv")
            == "https://files.api.example.com/x.csv"
        )
        assert (
            d("https://cdn.trusted.org/x", extra_hosts=("trusted.org",))
            == "https://cdn.trusted.org/x"
        )
        assert (
            d("https://evil.example.org/steal?token=1")
            == "(external: evil.example.org)"
        )
        assert d("javascript:alert(1)") == "(external: unparseable host)"
        assert d("https:///nohost") == "(external: unparseable host)"
        assert d("") == ""
        assert d(None) == ""
        # untrusted URL contents are never echoed
        assert "token" not in d("https://evil.example.org/steal?token=1")

    def test_format_search_header(self, plugin):
        h = plugin.format_search_header
        assert h(None, 3) == "Found 3 dataset(s) in TestCity's open data portal:"
        assert h(3, 3) == "Found 3 dataset(s) in TestCity's open data portal:"
        assert h(235, 20) == (
            "Found 235 matching dataset(s) in TestCity's open data portal (showing 1-20):"
        )
        assert h(235, 20, offset=40) == (
            "Found 235 matching dataset(s) in TestCity's open data portal (showing 41-60):"
        )
        assert h(2, 5) == "Found 5 dataset(s) in TestCity's open data portal:"

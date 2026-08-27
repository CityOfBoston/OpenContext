"""CKAN plugin implementation for OpenContext.

This plugin provides access to CKAN-based open data portals.
"""

import logging
import re
from typing import Any, Dict, List, Optional

import httpx

from core.base_plugin import BaseOpenDataPlugin, HTTP_RETRY, ToolHandler
from core.interfaces import PluginType, ToolDefinition, ToolResult
from core.portal_content import clean_text, join_cleaned
from plugins.ckan.config_schema import CKANPluginConfig
from plugins.ckan.sql_validator import SQLValidator

logger = logging.getLogger(__name__)

# Whitelists for SQL identifiers and metric expressions assembled by
# aggregate_data, to prevent SQL injection through field names / aliases.
# Ported from thealphacubicle/OpenContext (Feature/security update #37).
_SAFE_IDENTIFIER = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$")
_SAFE_METRIC_EXPR = re.compile(
    r"^(count\(\s*(\*|(distinct\s+)?[a-zA-Z_][a-zA-Z0-9_]{0,63})?\s*\)"
    r"|(?:sum|avg|min|max|stddev|variance)\(\s*[a-zA-Z_][a-zA-Z0-9_]{0,63}\s*\))$",
    re.IGNORECASE,
)

# HAVING string values may carry their own comparison operator (e.g. ">= 5");
# anything else must be a plain number.
_SAFE_HAVING_VALUE = re.compile(r"^\s*(=|!=|<>|>=|<=|>|<)?\s*-?\d+(\.\d+)?\s*$")

# ORDER BY accepts "field", "-field" (descending), or "field ASC|DESC".
_ORDER_BY_DIRECTION = re.compile(r"^(asc|desc)$", re.IGNORECASE)


# Catalog browse/facet vocabulary. Tool arguments on the left are the only
# filter names accepted; they map to CKAN's Solr index fields on the right.
_CATALOG_FILTER_FIELDS: Dict[str, str] = {
    "organization": "organization",
    "tag": "tags",
    "format": "res_format",
    "license": "license_id",
    "group": "groups",
}
_FACET_FIELDS: tuple = ("organization", "tags", "res_format", "license_id", "groups")
_FACET_LABELS: Dict[str, str] = {
    "organization": "Organization",
    "tags": "Tags",
    "res_format": "Resource format",
    "license_id": "License",
    "groups": "Groups",
}
# Sort options exposed to the model -> the sort string actually sent.
_SORT_OPTIONS: Dict[str, str] = {
    "relevance": "score desc, metadata_modified desc",
    "metadata_modified desc": "metadata_modified desc",
    "metadata_modified asc": "metadata_modified asc",
    "name asc": "name asc",
    "title asc": "title_string asc",
}
_MAX_FILTER_VALUE_LEN = 200
_MAX_LIST_LIMIT = 100
_MAX_FACET_LIMIT = 100
_MAX_RESOURCES = 500
_DEFAULT_MAX_RESOURCES = 50

# Shared JSON-schema fragment for the exact-match catalog filters.
_CATALOG_FILTER_SCHEMA: Dict[str, Any] = {
    "organization": {
        "type": "string",
        "description": "Exact organization slug (e.g. boston-311-org); "
        "get slugs from get_catalog_stats",
    },
    "tag": {"type": "string", "description": "Exact tag name"},
    "format": {
        "type": "string",
        "description": "Exact resource format (e.g. CSV, GeoJSON)",
    },
    "license": {"type": "string", "description": "Exact license id (e.g. odc-pddl)"},
    "group": {"type": "string", "description": "Exact group name"},
}


def _escape_solr_phrase(value: str) -> str:
    """Quote ``value`` as a Solr phrase.

    Inside a double-quoted Solr phrase only ``\\`` and ``"`` are
    metacharacters, so escaping those two is sufficient to make operators
    (``AND``, ``*``, ``:``, parentheses) inert.
    """
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _build_fq(filters: Dict[str, Any]) -> str:
    """Build a CKAN/Solr ``fq`` string from whitelisted exact-match filters.

    Args:
        filters: Mapping of tool argument name (``organization``, ``tag``,
            ``format``, ``license``, ``group``) to value.

    Returns:
        ``field:"value" AND field:"value"`` or ``""`` when nothing applies.

    Raises:
        ValueError: If a filter name is not whitelisted.
    """
    clauses: List[str] = []
    for key, raw in (filters or {}).items():
        if key not in _CATALOG_FILTER_FIELDS:
            raise ValueError(f"Unknown filter: {key!r}")
        if raw is None:
            continue
        value = clean_text(raw, max_len=_MAX_FILTER_VALUE_LEN, single_line=True)
        if not value:
            continue
        clauses.append(f"{_CATALOG_FILTER_FIELDS[key]}:{_escape_solr_phrase(value)}")
    return " AND ".join(clauses)


def _clamp(value: Any, default: int, lo: int, hi: int) -> int:
    """Coerce ``value`` to an int within ``[lo, hi]``, falling back to ``default``."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = default
    return max(lo, min(hi, n))


def _validate_identifier(name: str) -> None:
    """Validate that ``name`` is a safe SQL identifier.

    Args:
        name: Identifier to validate.

    Raises:
        ValueError: If ``name`` is not a safe identifier.
    """
    if not isinstance(name, str) or not _SAFE_IDENTIFIER.match(name):
        raise ValueError(f"Invalid identifier: {name!r}")


def _validate_metric_expr(expr: str) -> None:
    """Validate that ``expr`` is a safe aggregate metric expression.

    Args:
        expr: Metric expression to validate (e.g. ``count(*)`` or ``avg(field)``).

    Raises:
        ValueError: If ``expr`` is not an allowed aggregate expression.
    """
    if not isinstance(expr, str) or not _SAFE_METRIC_EXPR.match(expr):
        raise ValueError(f"Invalid metric expression: {expr!r}")


class CKANPlugin(BaseOpenDataPlugin):
    """Plugin for accessing CKAN-based open data portals.

    This plugin implements the :class:`DataPlugin` interface on top of
    :class:`BaseOpenDataPlugin` and provides tools for searching datasets,
    retrieving dataset metadata, and querying data.
    """

    plugin_name = "ckan"
    plugin_type = PluginType.OPEN_DATA
    plugin_version = "1.0.0"

    config_class = CKANPluginConfig
    # CKAN dataset/resource IDs are UUIDs or URL slugs.
    id_pattern = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$")
    provider_label = "open data portal (CKAN)"

    async def initialize(self) -> bool:
        """Initialize CKAN plugin and test connection.

        Returns:
            True if initialization succeeded
        """
        try:
            # Create HTTP client via the shared helper so it is tracked for
            # shutdown by the base class.
            headers = {}
            if self.plugin_config.api_key:
                headers["Authorization"] = self.plugin_config.api_key

            self.client = self._create_http_client(
                base_url=self.plugin_config.base_url,
                headers=headers,
                timeout=self.plugin_config.timeout,
            )

            # Test connection
            response = await self._call_ckan_api("status_show", {})
            if response.get("success"):
                self._initialized = True
                logger.info(
                    f"CKAN plugin initialized successfully for {self.plugin_config.city_name}"
                )
                return True
            else:
                logger.error("CKAN API connection test failed")
                return False

        except Exception as e:
            logger.error(f"Failed to initialize CKAN plugin: {e}", exc_info=True)
            return False

    def _parse_ckan_error(
        self, response_body: Dict[str, Any], context: str = ""
    ) -> str:
        """Extract human-readable error from CKAN API response body."""
        if response_body.get("success") is True:
            return ""
        err = response_body.get("error", {})
        msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
        portal = f" on {self.plugin_config.city_name} OpenData portal"
        base = f"{msg}{portal}" if msg else f"Unknown error{portal}"
        return f"{context}: {base}" if context else base

    @HTTP_RETRY
    async def _call_ckan_api(self, action: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Call CKAN API action.

        Args:
            action: CKAN action name (e.g., "package_search")
            data: Action parameters

        Returns:
            CKAN API response

        Raises:
            RuntimeError: On HTTP errors or when CKAN returns success: false
        """
        if not self.client:
            raise RuntimeError("Plugin not initialized")

        url = f"/api/3/action/{action}"
        portal = f"{self.plugin_config.city_name} OpenData portal"

        try:
            response = await self.client.post(url, json=data)
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            status_code = e.response.status_code
            try:
                body = e.response.json()
                ckan_msg = self._parse_ckan_error(body, "")
                if ckan_msg:
                    raise RuntimeError(f"Error: {ckan_msg} (HTTP {status_code})") from e
            except ValueError:
                pass
            param_hint = ""
            if "resource_id" in data:
                param_hint = f" Resource '{data.get('resource_id')}'"
            elif "id" in data:
                param_hint = f" Dataset '{data.get('id')}'"
            raise RuntimeError(
                f"Error:{param_hint} not found on {portal} (HTTP {status_code})"
            ) from e

        result = response.json()

        if result.get("success") is False:
            msg = self._parse_ckan_error(result, "")
            raise RuntimeError(f"Error: {msg}" if msg else f"API error on {portal}")

        return result

    def get_tools(self) -> List[ToolDefinition]:
        """Get list of tools provided by CKAN plugin.

        Returns:
            List of tool definitions
        """
        return [
            ToolDefinition(
                name="search_datasets",
                description=f"Search for datasets in {self.plugin_config.city_name}'s open data portal",
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query string",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of results (default: 20)",
                            "default": 20,
                        },
                    },
                    "required": ["query"],
                },
            ),
            ToolDefinition(
                name="get_dataset",
                description=(
                    f"Get detailed information about a specific dataset from "
                    f"{self.plugin_config.city_name}'s open data portal: "
                    "organization, license, created/modified dates, tags, groups, "
                    "and every resource with its ID, format, dates, size, "
                    "DataStore flag, and download URL."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "dataset_id": {
                            "type": "string",
                            "description": "Dataset ID or name",
                        },
                        "max_resources": {
                            "type": "integer",
                            "description": (
                                "Maximum resources to list (default "
                                f"{_DEFAULT_MAX_RESOURCES}, max {_MAX_RESOURCES}). "
                                "Increase for datasets split into one resource per year."
                            ),
                            "default": _DEFAULT_MAX_RESOURCES,
                            "minimum": 1,
                            "maximum": _MAX_RESOURCES,
                        },
                    },
                    "required": ["dataset_id"],
                },
            ),
            ToolDefinition(
                name="list_datasets",
                description=(
                    f"Browse {self.plugin_config.city_name}'s open data catalog with "
                    "exact-match filters (organization, tag, format, license, group), "
                    "sorting (default: most recently modified first), and paging. "
                    "Returns the total matching count plus a page of datasets with "
                    "organization, modified date, resource count and formats. Use "
                    "search_datasets for free-text relevance search; use this to "
                    "list everything from an organization or find what changed recently."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Optional free-text query (omit to browse the whole catalog)",
                        },
                        **_CATALOG_FILTER_SCHEMA,
                        "sort": {
                            "type": "string",
                            "enum": list(_SORT_OPTIONS),
                            "default": "metadata_modified desc",
                            "description": "Sort order",
                        },
                        "limit": {
                            "type": "integer",
                            "description": f"Datasets per page (default 20, max {_MAX_LIST_LIMIT})",
                            "default": 20,
                            "minimum": 1,
                            "maximum": _MAX_LIST_LIMIT,
                        },
                        "offset": {
                            "type": "integer",
                            "description": "Number of datasets to skip (for paging)",
                            "default": 0,
                            "minimum": 0,
                        },
                    },
                },
            ),
            ToolDefinition(
                name="get_catalog_stats",
                description=(
                    f"Count datasets in {self.plugin_config.city_name}'s open data "
                    "portal, overall and broken down by organization, tag, resource "
                    "format, license, or group. Counts come from the portal's search "
                    "index and cover public datasets only. Optionally scope the counts "
                    "with a free-text query and/or the same exact-match filters as "
                    "list_datasets. Use this for catalog-wide statistics instead of "
                    "estimating from search results."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "facets": {
                            "type": "array",
                            "items": {"type": "string", "enum": list(_FACET_FIELDS)},
                            "description": "Facets to count (default: all)",
                        },
                        "query": {
                            "type": "string",
                            "description": "Optional free-text query to scope the counts",
                        },
                        **_CATALOG_FILTER_SCHEMA,
                        "limit": {
                            "type": "integer",
                            "description": f"Max values per facet (default 20, max {_MAX_FACET_LIMIT})",
                            "default": 20,
                            "minimum": 1,
                            "maximum": _MAX_FACET_LIMIT,
                        },
                    },
                },
            ),
            ToolDefinition(
                name="query_data",
                description=f"Query data from a specific resource in {self.plugin_config.city_name}'s open data portal",
                input_schema={
                    "type": "object",
                    "properties": {
                        "resource_id": {
                            "type": "string",
                            "description": "Resource ID to query",
                        },
                        "filters": {
                            "type": "object",
                            "description": "Optional filters (field: value pairs)",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of records (default: 100)",
                            "default": 100,
                        },
                    },
                    "required": ["resource_id"],
                },
            ),
            ToolDefinition(
                name="get_schema",
                description=f"Get schema information for a resource in {self.plugin_config.city_name}'s open data portal",
                input_schema={
                    "type": "object",
                    "properties": {
                        "resource_id": {
                            "type": "string",
                            "description": "Resource ID",
                        },
                    },
                    "required": ["resource_id"],
                },
            ),
            ToolDefinition(
                name="execute_sql",
                description="""Execute raw PostgreSQL SELECT query.

⚠️ Advanced users only. For complex queries requiring full SQL.

Security: Only SELECT allowed. INSERT/UPDATE/DELETE blocked.

Examples:
- Window functions: RANK() OVER (...)
- CTEs: WITH subquery AS (...)
- Complex aggregations: PERCENTILE_CONT(0.5) WITHIN GROUP

Resource IDs must be double-quoted: FROM "uuid-here"
""",
                input_schema={
                    "type": "object",
                    "properties": {
                        "sql": {
                            "type": "string",
                            "description": "PostgreSQL SELECT statement",
                        },
                    },
                    "required": ["sql"],
                },
            ),
            ToolDefinition(
                name="aggregate_data",
                description=f"""Aggregate data with GROUP BY from {self.plugin_config.city_name}'s open data portal.

Prerequisites: get_schema for field names

Examples:
- Count by field: group_by=["neighborhood"], metrics={{count: "count(*)"}}
- Multiple metrics: metrics={{total: "count(*)", avg: "avg(field)"}}
- With filters: filters={{"status": "Open"}}
- Having: having={{"count(*)": ">= 5"}} (string values may include the
  operator; numeric values default to ">")

Supports: count(*), sum(), avg(), min(), max(), stddev()
""",
                input_schema={
                    "type": "object",
                    "properties": {
                        "resource_id": {"type": "string"},
                        "group_by": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "metrics": {"type": "object"},
                        "filters": {"type": "object"},
                        "having": {"type": "object"},
                        "order_by": {"type": "string"},
                        "limit": {"type": "integer", "default": 100},
                    },
                    "required": ["resource_id", "metrics"],
                },
            ),
        ]

    def tool_handlers(self) -> Dict[str, ToolHandler]:
        """Return the mapping of tool name to :class:`ToolHandler`.

        Returns:
            Dict mapping tool name (without plugin prefix) to ToolHandler.
        """
        return {
            "search_datasets": ToolHandler(
                handler=self._tool_search_datasets,
                required_args=("query",),
                guidance=(
                    f"View all datasets at: {self.plugin_config.portal_url}\n"
                    "Use the get_dataset tool with a dataset ID from the list "
                    "to get details and resource IDs. Use list_datasets to "
                    "filter/sort/page the catalog and get_catalog_stats for counts."
                ),
            ),
            "get_dataset": ToolHandler(
                handler=self._tool_get_dataset,
                required_args=("dataset_id",),
                guidance=(
                    "Use the get_schema or query_data tool with a Resource ID "
                    "from the list above to inspect or query its data. Resource "
                    "names and URLs usually carry the year for datasets split by "
                    "year; pass max_resources to see more resources."
                ),
            ),
            "list_datasets": ToolHandler(
                handler=self._tool_list_datasets,
                guidance=(
                    "Use get_catalog_stats for the exact organization/tag/format/"
                    "license/group values accepted by the filters, and get_dataset "
                    "with an ID for resources."
                ),
            ),
            "get_catalog_stats": ToolHandler(
                handler=self._tool_get_catalog_stats,
                guidance=(
                    "Pass a value shown in parentheses to list_datasets "
                    "(organization=, tag=, format=, license=, group=) to browse "
                    "that slice of the catalog."
                ),
            ),
            "query_data": ToolHandler(
                handler=self._tool_query_data,
                required_args=("resource_id",),
            ),
            "get_schema": ToolHandler(
                handler=self._tool_get_schema,
                required_args=("resource_id",),
            ),
            "execute_sql": ToolHandler(
                handler=self._tool_execute_sql,
                required_args=("sql",),
            ),
            "aggregate_data": ToolHandler(
                handler=self._tool_aggregate_data,
                required_args=("resource_id", "metrics"),
            ),
        }

    async def _tool_search_datasets(self, arguments: Dict[str, Any]) -> ToolResult:
        query = arguments.get("query", "")
        limit = arguments.get("limit", 20)
        result = await self._package_search({"q": query, "rows": limit})
        datasets = result.get("results", []) or []
        return ToolResult(
            content=[
                {
                    "type": "text",
                    "text": self._format_search_results(
                        datasets, total=result.get("count")
                    ),
                }
            ],
            success=True,
        )

    async def _tool_get_dataset(self, arguments: Dict[str, Any]) -> ToolResult:
        dataset = await self.get_dataset(arguments["dataset_id"])
        max_resources = _clamp(
            arguments.get("max_resources"), _DEFAULT_MAX_RESOURCES, 1, _MAX_RESOURCES
        )
        return ToolResult(
            content=[
                {
                    "type": "text",
                    "text": self._format_dataset(dataset, max_resources=max_resources),
                }
            ],
            success=True,
        )

    @staticmethod
    def _catalog_filters(arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Pick the whitelisted catalog filter arguments out of ``arguments``."""
        return {k: arguments.get(k) for k in _CATALOG_FILTER_FIELDS if arguments.get(k)}

    async def _tool_list_datasets(self, arguments: Dict[str, Any]) -> ToolResult:
        sort = arguments.get("sort") or "metadata_modified desc"
        if sort not in _SORT_OPTIONS:
            raise ValueError(
                f"Invalid sort {sort!r}; choose one of: {', '.join(_SORT_OPTIONS)}"
            )
        limit = _clamp(arguments.get("limit"), 20, 1, _MAX_LIST_LIMIT)
        offset = _clamp(arguments.get("offset"), 0, 0, 10_000_000)
        query = (arguments.get("query") or "").strip()
        params: Dict[str, Any] = {
            "q": query or "*:*",
            "rows": limit,
            "start": offset,
            "sort": _SORT_OPTIONS[sort],
        }
        fq = _build_fq(self._catalog_filters(arguments))
        if fq:
            params["fq"] = fq
        result = await self._package_search(params)
        datasets = result.get("results", []) or []
        return ToolResult(
            content=[
                {
                    "type": "text",
                    "text": self._format_search_results(
                        datasets, total=result.get("count"), offset=offset
                    ),
                }
            ],
            success=True,
        )

    async def _tool_get_catalog_stats(self, arguments: Dict[str, Any]) -> ToolResult:
        facets = arguments.get("facets") or list(_FACET_FIELDS)
        if isinstance(facets, str):
            facets = [facets]
        for facet in facets:
            if facet not in _FACET_FIELDS:
                raise ValueError(
                    f"Unknown facet {facet!r}; choose from: {', '.join(_FACET_FIELDS)}"
                )
        limit = _clamp(arguments.get("limit"), 20, 1, _MAX_FACET_LIMIT)
        query = (arguments.get("query") or "").strip()
        filters = self._catalog_filters(arguments)
        stats = await self.get_catalog_stats(
            list(facets), query=query, filters=filters, limit=limit
        )
        return ToolResult(
            content=[
                {
                    "type": "text",
                    "text": self._format_catalog_stats(
                        stats["count"], stats["facets"], query=query, filters=filters
                    ),
                }
            ],
            success=True,
        )

    async def _tool_query_data(self, arguments: Dict[str, Any]) -> ToolResult:
        filters = arguments.get("filters", {})
        limit = arguments.get("limit", 100)
        data = await self.query_data(arguments["resource_id"], filters, limit)
        return ToolResult(
            content=[
                {
                    "type": "text",
                    "text": self._format_query_results(data, limit),
                }
            ],
            success=True,
        )

    async def _tool_get_schema(self, arguments: Dict[str, Any]) -> ToolResult:
        schema = await self.get_schema(arguments["resource_id"])
        return ToolResult(
            content=[
                {
                    "type": "text",
                    "text": self._format_schema(schema),
                }
            ],
            success=True,
        )

    async def _tool_execute_sql(self, arguments: Dict[str, Any]) -> ToolResult:
        result = await self.execute_sql(arguments["sql"])
        if result.get("error"):
            return ToolResult(
                content=[],
                success=False,
                error_message=result.get("message", "SQL execution failed"),
            )
        records = result.get("records", [])
        fields = result.get("fields", [])
        formatted_text = self._format_sql_results(records, fields)
        return ToolResult(
            content=[{"type": "text", "text": formatted_text}],
            success=True,
        )

    async def _tool_aggregate_data(self, arguments: Dict[str, Any]) -> ToolResult:
        result = await self.aggregate_data(
            resource_id=arguments["resource_id"],
            group_by=arguments.get("group_by", []),
            metrics=arguments["metrics"],
            filters=arguments.get("filters"),
            having=arguments.get("having"),
            order_by=arguments.get("order_by"),
            limit=arguments.get("limit", 100),
        )
        if result.get("error"):
            return ToolResult(
                content=[],
                success=False,
                error_message=result.get("message", "Aggregation failed"),
            )
        formatted = self._format_sql_results(
            result.get("records", []), result.get("fields", [])
        )
        return ToolResult(content=[{"type": "text", "text": formatted}], success=True)

    async def search_datasets(
        self, query: str, limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Search for datasets matching a query.

        Args:
            query: Search query string
            limit: Maximum number of results

        Returns:
            List of dataset metadata dictionaries
        """
        result = await self._package_search({"q": query, "rows": limit})
        return result.get("results", []) or []

    async def _package_search(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """POST ``package_search`` and return the full ``result`` envelope.

        The envelope carries ``count`` (catalog-wide total), ``results``,
        ``search_facets`` and legacy ``facets``. ``fl`` must not be used:
        it collapses ``organization`` to a slug and drops ``resources``.
        """
        response = await self._call_ckan_api("package_search", params)
        return response.get("result", {}) or {}

    async def get_catalog_stats(
        self,
        facets: List[str],
        *,
        query: str = "",
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 20,
    ) -> Dict[str, Any]:
        """Count datasets overall and per facet value via ``package_search``.

        Args:
            facets: Facet fields to count (subset of ``_FACET_FIELDS``).
            query: Optional free-text query scoping the counts.
            filters: Optional whitelisted exact-match filters (see ``_build_fq``).
            limit: Maximum values returned per facet.

        Returns:
            ``{"count": int, "facets": {field: [{"name", "display_name", "count"}]}}``
            with each facet's values sorted by count descending.
        """
        for facet in facets:
            if facet not in _FACET_FIELDS:
                raise ValueError(f"Unknown facet {facet!r}")
        params: Dict[str, Any] = {
            "q": query or "*:*",
            "rows": 0,
            "facet": True,
            "facet.field": list(facets),
            "facet.limit": limit,
        }
        fq = _build_fq(filters or {})
        if fq:
            params["fq"] = fq
        result = await self._package_search(params)

        search_facets = result.get("search_facets") or {}
        legacy_facets = result.get("facets") or {}
        out: Dict[str, List[Dict[str, Any]]] = {}
        for facet in facets:
            items: List[Dict[str, Any]] = []
            block = search_facets.get(facet)
            if isinstance(block, dict) and isinstance(block.get("items"), list):
                for item in block["items"]:
                    if not isinstance(item, dict):
                        continue
                    items.append(
                        {
                            "name": item.get("name", ""),
                            "display_name": item.get("display_name")
                            or item.get("name", ""),
                            "count": self._as_int(item.get("count")),
                        }
                    )
            elif isinstance(legacy_facets.get(facet), dict):
                for name, count in legacy_facets[facet].items():
                    items.append(
                        {
                            "name": name,
                            "display_name": name,
                            "count": self._as_int(count),
                        }
                    )
            items.sort(key=lambda i: (-i["count"], str(i["name"])))
            out[facet] = items
        return {"count": self._as_int(result.get("count")), "facets": out}

    @staticmethod
    def _as_int(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    async def get_dataset(self, dataset_id: str) -> Dict[str, Any]:
        """Get detailed metadata for a specific dataset.

        Args:
            dataset_id: Dataset ID or name

        Returns:
            Dataset metadata dictionary
        """
        response = await self._call_ckan_api("package_show", {"id": dataset_id})
        return response.get("result", {})

    async def query_data(
        self,
        resource_id: str,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Query data from a specific resource.

        Args:
            resource_id: Resource ID
            filters: Optional filters (field: value pairs)
            limit: Maximum number of records

        Returns:
            List of data records
        """
        params = {"resource_id": resource_id, "limit": limit}

        # Convert filters to CKAN filter format
        if filters:
            for field, value in filters.items():
                params[f"filters[{field}]"] = value

        response = await self._call_ckan_api("datastore_search", params)
        return response.get("result", {}).get("records", [])

    async def get_schema(self, resource_id: str) -> Dict[str, Any]:
        """Get schema information for a resource.

        Args:
            resource_id: Resource ID

        Returns:
            Schema information dictionary
        """
        # Get schema by calling datastore_search with limit=0
        response = await self._call_ckan_api(
            "datastore_search", {"resource_id": resource_id, "limit": 0}
        )
        return response.get("result", {}).get("fields", [])

    async def execute_sql(self, sql: str) -> Dict[str, Any]:
        """Execute raw PostgreSQL SELECT query with security validation.

        Args:
            sql: PostgreSQL SELECT statement

        Returns:
            Dictionary with success flag, records, fields, or error message
        """
        # Validate SQL
        is_valid, error = SQLValidator.validate_query(sql)
        if not is_valid:
            return {"error": True, "message": error}

        # Log SQL execution (truncated for security)
        logger.info("Executing SQL", extra={"sql": sql[:500]})

        # Execute
        try:
            result = await self._call_ckan_api("datastore_search_sql", {"sql": sql})
            if not result.get("success", True):
                return {
                    "error": True,
                    "message": self._parse_ckan_error(result, "SQL execution failed"),
                }
            return {
                "success": True,
                "records": result.get("result", {}).get("records", []),
                "fields": result.get("result", {}).get("fields", []),
            }
        except Exception as e:
            logger.error(f"SQL execution failed: {e}", exc_info=True)
            return {"error": True, "message": str(e)}

    async def aggregate_data(
        self,
        resource_id: str,
        group_by: List[str],
        metrics: Dict[str, str],
        filters: Optional[Dict[str, Any]] = None,
        having: Optional[Dict[str, Any]] = None,
        order_by: Optional[str] = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """Aggregate data with GROUP BY.

        Args:
            resource_id: Resource ID (must be valid UUID)
            group_by: List of fields to group by
            metrics: Dictionary of metric_name: sql_expression (e.g., {"count": "count(*)"})
            filters: Optional WHERE clause filters (field: value pairs)
            having: Optional HAVING clause filters (expression: value pairs).
                String values may include their own operator (e.g.
                ``{">= 5"}``-style); numeric values default to the ``>``
                operator for backward compatibility.
            order_by: Optional field to order by
            limit: Maximum number of results

        Returns:
            Dictionary with success flag, records, fields, or error message
        """
        # Validate all identifiers/expressions before building SQL to prevent
        # SQL injection through field names, metric aliases/expressions, or
        # order_by. Ported from thealphacubicle/OpenContext (security update #37).
        try:
            for field in group_by or []:
                _validate_identifier(field)
            for alias, expr in metrics.items():
                _validate_identifier(alias)
                _validate_metric_expr(expr)
            if filters:
                for field in filters:
                    _validate_identifier(field)
            if having:
                for expr in having:
                    # HAVING keys are aggregate expressions like "count(*)" or
                    # declared metric aliases (substituted below, since
                    # PostgreSQL does not allow SELECT aliases in HAVING).
                    if expr not in metrics:
                        _validate_metric_expr(expr)
            order_field = None
            order_direction = ""
            if order_by:
                # Accept "field", "-field" (descending), or "field ASC|DESC".
                parts = order_by.strip().split()
                if len(parts) == 2 and _ORDER_BY_DIRECTION.match(parts[1]):
                    order_field, order_direction = parts[0], parts[1].upper()
                elif len(parts) == 1:
                    order_field = parts[0]
                    if order_field.startswith("-"):
                        order_field = order_field[1:]
                        order_direction = "DESC"
                else:
                    raise ValueError(
                        f"Invalid order_by: {order_by!r} "
                        "(expected 'field', '-field', or 'field ASC|DESC')"
                    )
                _validate_identifier(order_field)
        except ValueError as e:
            return {"error": True, "message": str(e)}

        # SELECT
        select_fields = ", ".join(group_by) if group_by else ""
        select_metrics = ", ".join(
            [f"{expr} as {name}" for name, expr in metrics.items()]
        )
        select_clause = (
            f"{select_fields}, {select_metrics}" if select_fields else select_metrics
        )

        # WHERE
        where_body = self.build_where_clause(filters) if filters else ""
        where_clause = f"WHERE {where_body}" if where_body else ""

        # GROUP BY
        group_clause = f"GROUP BY {', '.join(group_by)}" if group_by else ""

        # HAVING
        having_clause = ""
        if having:
            conditions = []
            for expr, value in having.items():
                # Metric aliases are substituted with their expression:
                # PostgreSQL does not allow SELECT aliases in HAVING.
                sql_expr = metrics.get(expr, expr)
                if isinstance(value, str):
                    if not _SAFE_HAVING_VALUE.match(value):
                        return {
                            "error": True,
                            "message": (
                                f"Invalid HAVING value: {value!r} (expected a "
                                "number, optionally prefixed with a comparison "
                                "operator, e.g. '>= 5')"
                            ),
                        }
                    value = value.strip()
                    # A bare numeric string defaults to the ">" operator.
                    if value[0].isdigit() or value[0] == "-":
                        value = f"> {value}"
                    conditions.append(f"{sql_expr} {value}")
                else:
                    # Numeric value: default to the documented ">" operator.
                    conditions.append(f"{sql_expr} > {value}")
            having_clause = "HAVING " + " AND ".join(conditions)

        # ORDER BY
        order_clause = ""
        if order_field:
            order_clause = f"ORDER BY {order_field} {order_direction}".strip()

        # Build SQL
        sql = f'SELECT {select_clause} FROM "{resource_id}" {where_clause} {group_clause} {having_clause} {order_clause} LIMIT {limit}'.strip()

        return await self.execute_sql(sql)

    async def health_check(self) -> bool:
        """Check if CKAN API is accessible.

        Returns:
            True if healthy
        """
        try:
            response = await self._call_ckan_api("status_show", {})
            return response.get("success", False)
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False

    def _format_search_results(
        self,
        datasets: List[Dict[str, Any]],
        *,
        total: Optional[int] = None,
        offset: int = 0,
    ) -> str:
        """Format search/list results for user display.

        Args:
            datasets: Page of ``package_search`` hits.
            total: Catalog-wide hit count from the API (``None`` if unknown).
            offset: Paging offset, used in the header.
        """
        if not datasets:
            if isinstance(total, int) and total > 0 and offset:
                return (
                    f"No datasets on this page (offset {offset} of {total} matching "
                    f"dataset(s) in {self.plugin_config.city_name}'s open data portal)."
                )
            return f"No datasets found in {self.plugin_config.city_name}'s open data portal."

        lines = [self.format_search_header(total, len(datasets), offset=offset), ""]

        for i, dataset in enumerate(datasets, offset + 1):
            title = self.portal_line(dataset.get("title"), default="Untitled")
            dataset_id = self.safe_id(dataset.get("id"))
            name = self.safe_id(dataset.get("name"), default="")
            notes = self.portal_line(
                dataset.get("notes"), max_len=100, default="No description"
            )
            org = self.portal_line((dataset.get("organization") or {}).get("title"))
            modified = self.short_date(dataset.get("metadata_modified"))
            resources = dataset.get("resources") or []
            num_resources = dataset.get("num_resources")
            if not isinstance(num_resources, int):
                num_resources = len(resources)
            formats = sorted(
                {
                    self.portal_line(r.get("format"), max_len=40)
                    for r in resources
                    if isinstance(r, dict) and r.get("format")
                }
            )
            num_tags = dataset.get("num_tags")
            if not isinstance(num_tags, int):
                num_tags = len(dataset.get("tags") or [])

            id_line = f"   ID: {dataset_id}"
            if name and name != dataset_id:
                id_line += f" (name: {name})"
            lines.append(f"{i}. {title}")
            lines.append(id_line)
            facts = []
            if org:
                facts.append(f"Organization: {org}")
            if modified:
                facts.append(f"Modified: {modified}")
            res_fact = f"Resources: {num_resources}"
            if formats:
                shown = ", ".join(formats[:6]) + (", …" if len(formats) > 6 else "")
                res_fact += f" ({shown})"
            facts.append(res_fact)
            facts.append(f"Tags: {num_tags}")
            lines.append("   " + " | ".join(facts))
            lines.append(f"   Description: {notes}")
            if dataset_id != "unknown":
                lines.append(
                    f"   Portal: {self.plugin_config.portal_url}/dataset/{dataset_id}"
                )
            lines.append("")

        return "\n".join(lines)

    def _format_dataset(
        self, dataset: Dict[str, Any], *, max_resources: int = _DEFAULT_MAX_RESOURCES
    ) -> str:
        """Format dataset metadata for user display.

        Every field the portal returned that helps date, attribute, license,
        or locate the data is surfaced; empty values are omitted rather than
        rendered as "unknown". Per-resource dates and download URLs let the
        model date year-series resources.
        """
        title = self.portal_line(dataset.get("title"), default="Untitled")
        dataset_id = self.safe_id(dataset.get("id"))
        name = self.safe_id(dataset.get("name"), default="")
        org = dataset.get("organization") or {}
        org_title = self.portal_line(org.get("title"))
        org_slug = self.safe_id(org.get("name"), default="")
        license_title = self.portal_line(dataset.get("license_title"))
        license_id = self.portal_line(dataset.get("license_id"), max_len=100)
        created = self.short_date(dataset.get("metadata_created"))
        modified = self.short_date(dataset.get("metadata_modified"))
        tags = [
            t.get("name") if isinstance(t, dict) else t
            for t in dataset.get("tags") or []
        ]
        groups = [
            (g.get("title") or g.get("name")) if isinstance(g, dict) else g
            for g in dataset.get("groups") or []
        ]
        notes = self.portal_block(dataset.get("notes"), default="No description")
        resources = [r for r in dataset.get("resources") or [] if isinstance(r, dict)]

        id_line = f"ID: {dataset_id}"
        if name and name != dataset_id:
            id_line += f" (name: {name})"
        lines = [f"Dataset: {title}", id_line]
        if org_title or org_slug:
            lines.append(
                f"Organization: {org_title or org_slug}"
                + (f" ({org_slug})" if org_slug and org_title else "")
            )
        if license_title or license_id:
            lines.append(
                f"License: {license_title or license_id}"
                + (f" ({license_id})" if license_id and license_title else "")
            )
        dates = []
        if created:
            dates.append(f"Created: {created}")
        if modified:
            dates.append(f"Modified: {modified}")
        if dates:
            lines.append(" | ".join(dates))
        if any(tags):
            lines.append(f"Tags: {join_cleaned(t for t in tags if t)}")
        if any(groups):
            lines.append(f"Groups: {join_cleaned(g for g in groups if g)}")
        lines.append(f"Description: {notes}")
        lines.append("")
        if dataset_id != "unknown":
            lines.append(
                f"Portal URL: {self.plugin_config.portal_url}/dataset/{dataset_id}"
            )
            lines.append("")

        if resources:
            lines.append(f"Resources ({len(resources)}):")
            for i, resource in enumerate(resources[:max_resources], 1):
                res_name = self.portal_line(resource.get("name"), default="Unnamed")
                res_id = self.safe_id(resource.get("id"))
                res_format = self.portal_line(
                    resource.get("format"), max_len=40, default="unknown"
                )
                lines.append(f"  {i}. {res_name} ({res_format})")
                lines.append(f"     Resource ID: {res_id}")
                facts = []
                r_created = self.short_date(resource.get("created"))
                r_modified = self.short_date(
                    resource.get("last_modified") or resource.get("metadata_modified")
                )
                size = self.human_size(resource.get("size"))
                if r_created:
                    facts.append(f"Created: {r_created}")
                if r_modified:
                    facts.append(f"Modified: {r_modified}")
                if size:
                    facts.append(f"Size: {size}")
                if "datastore_active" in resource:
                    facts.append(
                        f"DataStore: {'yes' if resource.get('datastore_active') else 'no'}"
                    )
                if facts:
                    lines.append("     " + " | ".join(facts))
                url = self.display_portal_url(resource.get("url"))
                if url:
                    lines.append(f"     URL: {url}")
                description = self.portal_line(resource.get("description"), max_len=120)
                if description:
                    lines.append(f"     Description: {description}")
            remaining = len(resources) - max_resources
            if remaining > 0:
                lines.append(
                    f"... and {remaining} more resource(s) (call get_dataset with "
                    f"max_resources={min(len(resources), _MAX_RESOURCES)} to see all)"
                )
        else:
            lines.append("No resources available for this dataset.")

        return "\n".join(lines)

    def _format_catalog_stats(
        self,
        count: int,
        facets: Dict[str, List[Dict[str, Any]]],
        *,
        query: str = "",
        filters: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Format catalog counts and facet breakdowns for user display."""
        city = self.plugin_config.city_name
        scope = []
        if query:
            scope.append(f"query={clean_text(query, max_len=100, single_line=True)!r}")
        for key, value in (filters or {}).items():
            if value:
                scope.append(
                    f"{key}={clean_text(value, max_len=100, single_line=True)}"
                )
        header = f"Catalog: {count} public dataset(s) in {city}'s open data portal"
        if scope:
            header += f" (filters: {', '.join(scope)})"
        lines = [header, ""]
        for facet, items in facets.items():
            label = _FACET_LABELS.get(facet, facet)
            lines.append(f"{label} ({facet}):")
            if not items:
                lines.append("  (no values returned)")
                continue
            for item in items:
                name = self.portal_line(item.get("name"), max_len=150)
                display = (
                    self.portal_line(item.get("display_name"), max_len=150) or name
                )
                label_text = (
                    display if display == name or not name else f"{display} ({name})"
                )
                lines.append(f"  {label_text}: {self._as_int(item.get('count'))}")
        return "\n".join(lines)

    def _format_query_results(self, records: List[Dict[str, Any]], limit: int) -> str:
        """Format query results for user display."""
        if not records:
            return "No records found matching the query."

        return self.format_records(
            records,
            max_display=5,
            header=f"Found {len(records)} record(s) (showing up to {limit}):",
        )

    def _format_schema(self, fields: List[Dict[str, Any]]) -> str:
        """Format schema information for user display."""
        if not fields:
            return "No schema information available."

        lines = ["Schema fields:"]
        for field in fields:
            field_id = self.portal_line(field.get("id"), default="unknown")
            field_type = self.portal_line(field.get("type"), default="unknown")
            field_info = field.get("info", {})
            description = (
                self.portal_line(field_info.get("label")) if field_info else ""
            )

            lines.append(f"  • {field_id} ({field_type})")
            if description:
                lines.append(f"    {description}")

        return "\n".join(lines)

    def _format_sql_results(
        self, records: List[Dict[str, Any]], fields: List[Dict[str, Any]]
    ) -> str:
        """Format SQL query results for user display.

        Args:
            records: List of record dictionaries
            fields: List of field metadata dictionaries

        Returns:
            Formatted string representation of results
        """
        if not records:
            return "No records found matching the SQL query."

        header_lines = [f"SQL Query Results: {len(records)} record(s)"]
        # Show field names if available
        if fields:
            field_names = [field.get("id", "unknown") for field in fields]
            header_lines.append(f"Fields: {join_cleaned(field_names)}")

        header = "\n".join(header_lines)
        return self.format_records(records, max_display=10, header=header)

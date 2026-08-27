"""Opendatasoft plugin implementation for OpenContext.

This plugin provides access to Opendatasoft-based open data portals (e.g.,
data.longbeach.gov) through the Explore API v2.1, which exposes catalog
search, dataset metadata, field schemas, record queries and facets over a
read-only ODSQL dialect.
"""

import logging
import re
from typing import Any

import httpx

from core.base_plugin import BaseOpenDataPlugin, HTTP_RETRY, ToolHandler
from core.portal_content import join_cleaned
from core.interfaces import PluginType, ToolDefinition, ToolResult
from plugins.opendatasoft.config_schema import OpendatasoftPluginConfig
from plugins.opendatasoft.odsql_validator import ODSQLValidator

logger = logging.getLogger(__name__)

# Path prefix for the Explore API v2.1.
EXPLORE_API_PATH = "/api/explore/v2.1"

# Records endpoint page size ceiling enforced by Opendatasoft.
MAX_RECORDS_LIMIT = 100


def _clamp_limit(limit: Any, default: int = MAX_RECORDS_LIMIT) -> int:
    """Clamp a caller-supplied limit into the API's accepted 1..100 range."""
    try:
        value = int(limit)
    except (TypeError, ValueError):
        return default
    return max(1, min(value, MAX_RECORDS_LIMIT))


# Whitelists for ODSQL identifiers and aggregate expressions assembled by
# aggregate_data, to prevent injection through field names / aliases. Mirrors
# the CKAN plugin's approach.
_SAFE_IDENTIFIER = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$")
_SAFE_METRIC_EXPR = re.compile(
    r"^(count\(\s*(\*|(distinct\s+)?[a-zA-Z_][a-zA-Z0-9_]{0,63})\s*\)"
    r"|(?:sum|avg|min|max)\(\s*[a-zA-Z_][a-zA-Z0-9_]{0,63}\s*\))$",
    re.IGNORECASE,
)

# order_by accepts "field", "-field" (descending), or "field ASC|DESC".
_ORDER_BY_DIRECTION = re.compile(r"^(asc|desc)$", re.IGNORECASE)

# Opendatasoft dataset ids are URL slugs (letters, digits, -, _, and an
# optional @domain suffix). Interpolated into the request path, so anything
# outside this pattern (slashes, dots, query characters) is rejected.
_SAFE_DATASET_ID = re.compile(
    r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}(@[a-zA-Z0-9_-]{1,63})?$"
)


def _validate_dataset_id(dataset_id: str) -> str:
    """Validate that ``dataset_id`` is a safe URL path segment.

    Args:
        dataset_id: Dataset identifier supplied by the caller.

    Returns:
        The validated dataset id unchanged.

    Raises:
        ValueError: If the id contains characters that could alter the
            request path or query string.
    """
    if not isinstance(dataset_id, str) or not _SAFE_DATASET_ID.match(dataset_id):
        raise ValueError(f"Invalid dataset_id: {dataset_id!r}")
    return dataset_id


def _validate_identifier(name: str) -> None:
    """Validate that ``name`` is a safe ODSQL identifier.

    Args:
        name: Identifier to validate (field name or metric alias).

    Raises:
        ValueError: If ``name`` is not a safe identifier.
    """
    if not isinstance(name, str) or not _SAFE_IDENTIFIER.match(name):
        raise ValueError(f"Invalid identifier: {name!r}")


def _validate_metric_expr(expr: str) -> None:
    """Validate that ``expr`` is a safe ODSQL aggregate expression.

    Args:
        expr: Metric expression (e.g. ``count(*)``, ``avg(field)``,
            ``count(distinct field)``).

    Raises:
        ValueError: If ``expr`` is not an allowed aggregate expression.
    """
    if not isinstance(expr, str) or not _SAFE_METRIC_EXPR.match(expr):
        raise ValueError(f"Invalid metric expression: {expr!r}")


class OpendatasoftPlugin(BaseOpenDataPlugin):
    """Plugin for accessing Opendatasoft-based open data portals.

    Implements the :class:`DataPlugin` interface on top of
    :class:`BaseOpenDataPlugin` using a single HTTP client pointed at the
    portal's Explore API v2.1.
    """

    plugin_name = "opendatasoft"
    plugin_type = PluginType.OPEN_DATA
    plugin_version = "1.0.0"

    config_class = OpendatasoftPluginConfig
    # ODS dataset IDs are slugs, occasionally with '@' domain suffixes.
    id_pattern = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]{0,199}$")
    provider_label = "open data portal (Opendatasoft)"

    async def initialize(self) -> bool:
        """Initialize the Opendatasoft plugin and test connectivity.

        Returns:
            True if initialization succeeded, False otherwise.
        """
        try:
            headers: dict[str, str] = {}
            if self.plugin_config.api_key:
                headers["Authorization"] = f"apikey {self.plugin_config.api_key}"

            self.client = self._create_http_client(
                base_url=f"{self.plugin_config.base_url}{EXPLORE_API_PATH}",
                headers=headers,
                timeout=self.plugin_config.timeout,
            )

            # Test connection with a minimal catalog request.
            await self._call_api("/catalog/datasets", {"limit": 1})

            self._initialized = True
            logger.info(
                f"Opendatasoft plugin initialized successfully for "
                f"{self.plugin_config.city_name}"
            )
            return True

        except Exception as e:
            logger.error(
                f"Failed to initialize Opendatasoft plugin: {e}", exc_info=True
            )
            return False

    @HTTP_RETRY
    async def _call_api(
        self, path: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Call the Explore API v2.1.

        Args:
            path: API path relative to the Explore API root
                (e.g. ``/catalog/datasets``).
            params: Optional query parameters.

        Returns:
            Parsed JSON response.

        Raises:
            RuntimeError: If the plugin is not initialized or the API returns
                an HTTP error status.
        """
        if not getattr(self, "client", None):
            raise RuntimeError("Plugin not initialized")

        try:
            response = await self.client.get(path, params=params or {})
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            self._raise_http_error(e, " Explore API")

        return response.json()

    def get_tools(self) -> list[ToolDefinition]:
        """Get list of tools provided by the Opendatasoft plugin.

        Returns:
            List of tool definitions.
        """
        city = self.plugin_config.city_name
        return [
            ToolDefinition(
                name="search_datasets",
                description=(
                    f"Search for datasets in {city}'s open data portal. "
                    f"Returns dataset IDs needed for get_dataset, get_schema, "
                    f"query_data, and aggregate_data. Limit is optional "
                    f"(default: 10)."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Full-text search query string",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of results (optional, default: 10)",
                            "default": 10,
                        },
                    },
                    "required": ["query"],
                },
            ),
            ToolDefinition(
                name="get_dataset",
                description=(
                    f"Get metadata for a specific dataset from {city}'s open data "
                    f"portal (title, description, theme, keywords, record count). "
                    f"For field names and types, use get_schema instead."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "dataset_id": {
                            "type": "string",
                            "description": "Dataset identifier (e.g., police-calls-for-service)",
                        },
                    },
                    "required": ["dataset_id"],
                },
            ),
            ToolDefinition(
                name="get_schema",
                description=(
                    f"Get the field schema for a dataset in {city}'s open data "
                    f"portal. Returns field names, types and descriptions that "
                    f"are directly usable in ODSQL select/where/group_by "
                    f"clauses. Call before query_data or aggregate_data."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "dataset_id": {
                            "type": "string",
                            "description": "Dataset identifier",
                        },
                    },
                    "required": ["dataset_id"],
                },
            ),
            ToolDefinition(
                name="query_data",
                description=(
                    f"Query records from a dataset in {city}'s open data portal "
                    f"using ODSQL. Use get_schema first to get field names. "
                    f"ODSQL notes: string literals use double quotes "
                    f'(status = "Open"); full-text matching uses search("text"); '
                    f"order_by takes 'field ASC' or 'field DESC'; limit is "
                    f"capped at {MAX_RECORDS_LIMIT} records per call."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "dataset_id": {
                            "type": "string",
                            "description": "Dataset identifier",
                        },
                        "where": {
                            "type": "string",
                            "description": (
                                'ODSQL filter, e.g. status = "Open" and year > 2020, '
                                'or search("noise complaint")'
                            ),
                        },
                        "select": {
                            "type": "string",
                            "description": "Comma-separated fields to return (default: all fields)",
                        },
                        "order_by": {
                            "type": "string",
                            "description": "Sort expression, e.g. 'date DESC'",
                        },
                        "limit": {
                            "type": "integer",
                            "description": (
                                f"Maximum number of records "
                                f"(default: {MAX_RECORDS_LIMIT}, max: {MAX_RECORDS_LIMIT})"
                            ),
                            "default": MAX_RECORDS_LIMIT,
                        },
                    },
                    "required": ["dataset_id"],
                },
            ),
            ToolDefinition(
                name="aggregate_data",
                description=f"""Aggregate records with GROUP BY from {city}'s open data portal.

Prerequisites: get_schema for field names

Examples:
- Count by field: group_by=["neighborhood"], metrics={{"total": "count(*)"}}
- Multiple metrics: metrics={{"total": "count(*)", "avg_amount": "avg(amount)"}}
- With a filter: where='status = "Open"'
- Sorted: order_by="-total" (metric aliases may be used in order_by)

Supports: count(*), count(field), count(distinct field), sum(), avg(), min(), max()
""",
                input_schema={
                    "type": "object",
                    "properties": {
                        "dataset_id": {
                            "type": "string",
                            "description": "Dataset identifier",
                        },
                        "metrics": {
                            "type": "object",
                            "description": (
                                "Mapping of result alias to aggregate expression, "
                                'e.g. {"total": "count(*)"}'
                            ),
                        },
                        "group_by": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Field names to group by",
                        },
                        "where": {
                            "type": "string",
                            "description": "Optional ODSQL filter applied before aggregation",
                        },
                        "order_by": {
                            "type": "string",
                            "description": (
                                "Optional sort: 'field', '-field', or 'field ASC|DESC'. "
                                "Metric aliases are allowed."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of groups (default: 100)",
                            "default": 100,
                        },
                    },
                    "required": ["dataset_id", "metrics"],
                },
            ),
            ToolDefinition(
                name="list_categories",
                description=(
                    f"Typical workflow: list_categories → search_datasets → "
                    f"get_dataset → get_schema → query_data. "
                    f"List dataset themes on {city}'s open data portal with "
                    f"dataset counts. Use results to inform which search terms "
                    f"to pass to search_datasets."
                ),
                input_schema={"type": "object", "properties": {}},
            ),
        ]

    def tool_handlers(self) -> dict[str, ToolHandler]:
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
                    "to get more details."
                ),
            ),
            "get_dataset": ToolHandler(
                handler=self._tool_get_dataset,
                required_args=("dataset_id",),
                guidance=(
                    "Use the get_schema tool with this dataset's ID to get field "
                    "info, then query_data to query records."
                ),
            ),
            "get_schema": ToolHandler(
                handler=self._tool_get_schema, required_args=("dataset_id",)
            ),
            "query_data": ToolHandler(
                handler=self._tool_query_data, required_args=("dataset_id",)
            ),
            "aggregate_data": ToolHandler(
                handler=self._tool_aggregate_data,
                required_args=("dataset_id", "metrics"),
            ),
            "list_categories": ToolHandler(handler=self._tool_list_categories),
        }

    async def _tool_search_datasets(self, arguments: dict[str, Any]) -> ToolResult:
        query = arguments.get("query", "")
        limit = arguments.get("limit", 10)
        result = await self._catalog_search(query, limit)
        datasets = result.get("results", []) or []
        total = result.get("total_count")
        return ToolResult(
            content=[
                {
                    "type": "text",
                    "text": self._format_search_results(
                        datasets, total=total if isinstance(total, int) else None
                    ),
                }
            ],
            success=True,
        )

    async def _tool_get_dataset(self, arguments: dict[str, Any]) -> ToolResult:
        dataset = await self.get_dataset(arguments["dataset_id"])
        return ToolResult(
            content=[{"type": "text", "text": self._format_dataset(dataset)}],
            success=True,
        )

    async def _tool_get_schema(self, arguments: dict[str, Any]) -> ToolResult:
        fields = await self.get_schema(arguments["dataset_id"])
        return ToolResult(
            content=[{"type": "text", "text": self._format_schema(fields)}],
            success=True,
        )

    async def _tool_query_data(self, arguments: dict[str, Any]) -> ToolResult:
        limit = arguments.get("limit", MAX_RECORDS_LIMIT)
        result = await self._query_records(
            dataset_id=arguments["dataset_id"],
            where=arguments.get("where"),
            select=arguments.get("select"),
            order_by=arguments.get("order_by"),
            limit=limit,
        )
        return ToolResult(
            content=[
                {
                    "type": "text",
                    "text": self._format_query_results(
                        result.get("results", []),
                        total_count=result.get("total_count"),
                    ),
                }
            ],
            success=True,
        )

    async def _tool_aggregate_data(self, arguments: dict[str, Any]) -> ToolResult:
        result = await self.aggregate_data(
            dataset_id=arguments["dataset_id"],
            metrics=arguments["metrics"],
            group_by=arguments.get("group_by", []),
            where=arguments.get("where"),
            order_by=arguments.get("order_by"),
            limit=arguments.get("limit", 100),
        )
        if result.get("error"):
            return ToolResult(
                content=[],
                success=False,
                error_message=result.get("message", "Aggregation failed"),
            )
        return ToolResult(
            content=[
                {
                    "type": "text",
                    "text": self._format_aggregate_results(
                        result.get("records", []), result.get("fields", [])
                    ),
                }
            ],
            success=True,
        )

    async def _tool_list_categories(self, arguments: dict[str, Any]) -> ToolResult:
        categories = await self._list_categories()
        return ToolResult(
            content=[{"type": "text", "text": self._format_categories(categories)}],
            success=True,
        )

    async def search_datasets(
        self, query: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Search the portal catalog for datasets matching a query.

        Args:
            query: Full-text search query string.
            limit: Maximum number of results.

        Returns:
            List of catalog dataset dictionaries.
        """
        # ODSQL string literals are double quoted; escape any embedded quotes
        # (and backslashes) so the search term cannot break out of the literal.
        result = await self._catalog_search(query, limit)
        return result.get("results", []) or []

    async def _catalog_search(self, query: str, limit: int) -> dict[str, Any]:
        """Search the catalog and return the full envelope (``total_count`` + ``results``)."""
        # ODSQL string literals are double quoted; escape any embedded quotes
        # (and backslashes) so the search term cannot break out of the literal.
        escaped = query.replace("\\", "\\\\").replace('"', '\\"')
        return (
            await self._call_api(
                "/catalog/datasets",
                {
                    "where": f'search("{escaped}")',
                    "limit": _clamp_limit(limit, default=10),
                },
            )
            or {}
        )

    async def get_dataset(self, dataset_id: str) -> dict[str, Any]:
        """Get full metadata for a specific dataset.

        Args:
            dataset_id: Dataset identifier.

        Returns:
            Dataset metadata dictionary.
        """
        _validate_dataset_id(dataset_id)
        return await self._call_api(f"/catalog/datasets/{dataset_id}")

    async def get_schema(self, dataset_id: str) -> list[dict[str, Any]]:
        """Get the field schema for a dataset.

        Args:
            dataset_id: Dataset identifier.

        Returns:
            List of field metadata dictionaries.
        """
        dataset = await self.get_dataset(dataset_id)
        return dataset.get("fields", []) or []

    async def _query_records(
        self,
        dataset_id: str,
        where: str | None = None,
        select: str | None = None,
        order_by: str | None = None,
        limit: int = MAX_RECORDS_LIMIT,
    ) -> dict[str, Any]:
        """Query records from a dataset with validated ODSQL clauses.

        Args:
            dataset_id: Dataset identifier.
            where: Optional ODSQL filter clause.
            select: Optional ODSQL select clause.
            order_by: Optional ODSQL order_by clause.
            limit: Maximum number of records (capped at
                :data:`MAX_RECORDS_LIMIT`).

        Returns:
            The raw records response (``total_count`` and ``results``).

        Raises:
            ValueError: If a clause fails ODSQL validation.
        """
        params: dict[str, Any] = {"limit": _clamp_limit(limit)}

        validated_where = ODSQLValidator.validate_clause(where or "", "where")
        if validated_where:
            params["where"] = validated_where

        validated_select = ODSQLValidator.validate_clause(select or "", "select")
        if validated_select:
            params["select"] = validated_select

        validated_order = ODSQLValidator.validate_clause(order_by or "", "order_by")
        if validated_order:
            params["order_by"] = validated_order

        _validate_dataset_id(dataset_id)
        return await self._call_api(f"/catalog/datasets/{dataset_id}/records", params)

    async def query_data(
        self,
        resource_id: str,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Query records from a dataset (DataPlugin contract).

        Args:
            resource_id: Dataset identifier.
            filters: Optional filters (field: value pairs) compiled into an
                ODSQL ``where`` clause.
            limit: Maximum number of records.

        Returns:
            List of data records.
        """
        where = self._build_odsql_where(filters) if filters else ""
        result = await self._query_records(resource_id, where=where, limit=limit)
        return result.get("results", [])

    @staticmethod
    def _build_odsql_where(filters: dict[str, Any]) -> str:
        """Build an ODSQL ``where`` clause from a field/value filter dict.

        Unlike the base :meth:`build_where_clause` (SQL convention of doubling
        single quotes), ODSQL string literals are double-quoted with
        backslash escapes, so values are rendered as ``field = "value"``.
        Field names must be safe identifiers.

        Args:
            filters: Mapping of field name to filter value.

        Returns:
            The ``where`` clause body, or an empty string when ``filters``
            is empty.

        Raises:
            ValueError: If a field name is not a safe identifier.
        """
        if not filters:
            return ""
        conditions: list[str] = []
        for field, value in filters.items():
            _validate_identifier(field)
            if isinstance(value, str):
                escaped = value.replace("\\", "\\\\").replace('"', '\\"')
                conditions.append(f'{field} = "{escaped}"')
            elif value is None:
                conditions.append(f"{field} is null")
            elif isinstance(value, bool):
                conditions.append(f"{field} = {str(value).lower()}")
            else:
                conditions.append(f"{field} = {value}")
        return " and ".join(conditions)

    async def aggregate_data(
        self,
        dataset_id: str,
        metrics: dict[str, str],
        group_by: list[str] | None = None,
        where: str | None = None,
        order_by: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Aggregate dataset records with ODSQL group_by.

        Args:
            dataset_id: Dataset identifier.
            metrics: Mapping of result alias to aggregate expression
                (e.g. ``{"total": "count(*)"}``).
            group_by: Optional list of field names to group by.
            where: Optional ODSQL filter applied before aggregation.
            order_by: Optional sort: ``"field"``, ``"-field"``, or
                ``"field ASC|DESC"``. Metric aliases are allowed because
                ODSQL permits ordering by select aliases.
            limit: Maximum number of groups returned.

        Returns:
            Dictionary with ``success``/``records``/``fields``, or
            ``error``/``message`` when validation or the request fails.
        """
        group_by = group_by or []

        # Validate every identifier/expression before assembling ODSQL so
        # nothing can be smuggled in through field names or aliases.
        try:
            if not isinstance(metrics, dict) or not metrics:
                raise ValueError("metrics must be a non-empty object")
            # A bare string is a common client slip for a one-element list;
            # iterating it would validate each character individually.
            if isinstance(group_by, str):
                group_by = [group_by]
            for field in group_by:
                _validate_identifier(field)
            for alias, expr in metrics.items():
                _validate_identifier(alias)
                _validate_metric_expr(expr)

            validated_where = ODSQLValidator.validate_clause(where or "", "where")

            order_clause = ""
            if order_by:
                # Accept "field", "-field" (descending), or "field ASC|DESC".
                parts = order_by.strip().split()
                if len(parts) == 2 and _ORDER_BY_DIRECTION.match(parts[1]):
                    order_field, order_direction = parts[0], parts[1].upper()
                elif len(parts) == 1:
                    order_field = parts[0]
                    order_direction = ""
                    if order_field.startswith("-"):
                        order_field = order_field[1:]
                        order_direction = "DESC"
                else:
                    raise ValueError(
                        f"Invalid order_by: {order_by!r} "
                        "(expected 'field', '-field', or 'field ASC|DESC')"
                    )
                # ODSQL allows ordering by a select alias, so a metric alias
                # is as valid here as a grouped field name.
                _validate_identifier(order_field)
                order_clause = f"{order_field} {order_direction}".strip()
        except ValueError as e:
            return {"error": True, "message": str(e)}

        select_parts = [f"{expr} as {alias}" for alias, expr in metrics.items()]
        params: dict[str, Any] = {
            "select": ", ".join(select_parts),
            # Without group_by the Explore API repeats the global aggregate
            # once per underlying record, so a single row is the whole answer.
            "limit": limit if group_by else 1,
        }
        if group_by:
            params["group_by"] = ",".join(group_by)
        if validated_where:
            params["where"] = validated_where
        if order_clause:
            params["order_by"] = order_clause

        try:
            _validate_dataset_id(dataset_id)
            response = await self._call_api(
                f"/catalog/datasets/{dataset_id}/records", params
            )
        except Exception as e:
            logger.error(f"Aggregation failed: {e}", exc_info=True)
            return {"error": True, "message": str(e)}

        return {
            "success": True,
            "records": response.get("results", []),
            "fields": list(group_by) + list(metrics.keys()),
        }

    async def _list_categories(self) -> list[dict[str, Any]]:
        """List portal themes with dataset counts.

        Returns:
            List of ``{"name": ..., "count": ...}`` dictionaries.
        """
        response = await self._call_api("/catalog/facets", {"facet": "theme"})
        for facet_group in response.get("facets", []) or []:
            if facet_group.get("name") == "theme":
                return [
                    {
                        "name": entry.get("name", "Unknown"),
                        "count": entry.get("count", 0),
                    }
                    for entry in facet_group.get("facets", []) or []
                ]
        return []

    async def health_check(self) -> bool:
        """Check if the Explore API is accessible.

        Returns:
            True if healthy, False otherwise.
        """
        try:
            await self._call_api("/catalog/datasets", {"limit": 1})
            return True
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False

    @staticmethod
    def _dataset_meta(dataset: dict[str, Any]) -> dict[str, Any]:
        """Extract the default metadata block from a catalog dataset entry.

        Args:
            dataset: Catalog or dataset-detail dictionary.

        Returns:
            The ``metas.default`` dictionary, or an empty dict when absent.
        """
        metas = dataset.get("metas") or {}
        default = metas.get("default") if isinstance(metas, dict) else None
        return default if isinstance(default, dict) else {}

    def _format_search_results(
        self, datasets: list[dict[str, Any]], *, total: int | None = None
    ) -> str:
        """Format catalog search results for user display.

        Args:
            datasets: Catalog hits.
            total: Catalog-wide hit count (``total_count``), if known.
        """
        city = self.plugin_config.city_name
        if not datasets:
            return f"No datasets found in {city}'s open data portal."

        lines = [self.format_search_header(total, len(datasets)), ""]

        for i, dataset in enumerate(datasets, 1):
            meta = self._dataset_meta(dataset)
            dataset_id = self.safe_id(
                dataset.get("dataset_id") or meta.get("dataset_id")
            )
            title = self.portal_line(
                meta.get("title") or dataset.get("title"), default="Untitled"
            )
            description = self.portal_line(
                meta.get("description"), max_len=100, default="No description"
            )
            theme = meta.get("theme")
            records_count = meta.get("records_count")
            modified = self.short_date(meta.get("modified"))
            publisher = self.portal_line(meta.get("publisher"))

            lines.append(f"{i}. {title}")
            lines.append(f"   ID: {dataset_id}")
            facts = []
            if publisher:
                facts.append(f"Publisher: {publisher}")
            if modified:
                facts.append(f"Modified: {modified}")
            if records_count is not None:
                facts.append(f"Records: {self.portal_line(records_count)}")
            if facts:
                lines.append("   " + " | ".join(facts))
            lines.append(f"   Description: {description}")
            if theme:
                theme_text = (
                    join_cleaned(theme)
                    if isinstance(theme, list)
                    else self.portal_line(theme)
                )
                lines.append(f"   Theme: {theme_text}")
            if dataset_id != "unknown":
                lines.append(
                    f"   Portal: {self.plugin_config.portal_url}/explore/dataset/{dataset_id}/"
                )
            lines.append("")

        return "\n".join(lines)

    def _format_dataset(self, dataset: dict[str, Any]) -> str:
        """Format dataset metadata for user display."""
        meta = self._dataset_meta(dataset)
        dataset_id = self.safe_id(dataset.get("dataset_id") or meta.get("dataset_id"))
        title = self.portal_line(
            meta.get("title") or dataset.get("title"), default="Untitled"
        )
        description = self.portal_block(
            meta.get("description"), default="No description"
        )
        theme = meta.get("theme")
        keywords = meta.get("keyword")
        records_count = self.portal_line(meta.get("records_count"), default="N/A")
        modified = self.short_date(meta.get("modified")) or "N/A"
        data_processed = self.short_date(meta.get("data_processed"))
        metadata_processed = self.short_date(meta.get("metadata_processed"))
        publisher = self.portal_line(meta.get("publisher"))
        license_name = self.portal_line(meta.get("license"))
        license_url = self.display_portal_url(meta.get("license_url"))
        attributions = meta.get("attributions")
        references = self.display_portal_url(meta.get("references"))
        fields = dataset.get("fields")

        lines = [f"Dataset: {title}", f"ID: {dataset_id}"]
        if publisher:
            lines.append(f"Publisher: {publisher}")
        if license_name or license_url:
            lines.append(
                f"License: {license_name or license_url}"
                + (f" — {license_url}" if license_url and license_name else "")
            )
        if attributions:
            attr_text = (
                join_cleaned(attributions)
                if isinstance(attributions, list)
                else self.portal_line(attributions)
            )
            if attr_text:
                lines.append(f"Attribution: {attr_text}")
        dates = [f"Last modified: {modified}"]
        if data_processed:
            dates.append(f"Data processed: {data_processed}")
        if metadata_processed:
            dates.append(f"Metadata processed: {metadata_processed}")
        lines.append(" | ".join(dates))
        facts = [f"Records: {records_count}"]
        if isinstance(fields, list):
            facts.append(f"Fields: {len(fields)}")
        lines.append(" | ".join(facts))
        if references:
            lines.append(f"References: {references}")
        lines.append(f"Description: {description}")

        if theme:
            theme_text = (
                join_cleaned(theme)
                if isinstance(theme, list)
                else self.portal_line(theme)
            )
            lines.append(f"Theme: {theme_text}")
        if keywords:
            kw_text = (
                join_cleaned(keywords)
                if isinstance(keywords, list)
                else self.portal_line(keywords)
            )
            lines.append(f"Keywords: {kw_text}")

        if dataset_id != "unknown":
            lines.append("")
            lines.append(
                f"Portal URL: {self.plugin_config.portal_url}/explore/dataset/{dataset_id}/"
            )

        return "\n".join(lines)

    def _format_schema(self, fields: list[dict[str, Any]]) -> str:
        """Format field schema for user display."""
        if not fields:
            return "No schema information available."

        lines = ["Schema fields (use these for ODSQL queries):"]
        for field in fields:
            name = self.portal_line(field.get("name"), default="unknown")
            field_type = self.portal_line(field.get("type"), default="unknown")
            label = self.portal_line(field.get("label"))
            description = self.portal_line(field.get("description"))

            lines.append(f"  • {name} ({field_type})")
            if label and label != name:
                lines.append(f"    Label: {label}")
            if description:
                lines.append(f"    {description}")

        return "\n".join(lines)

    def _format_query_results(
        self,
        records: list[dict[str, Any]],
        total_count: int | None = None,
        max_display: int = MAX_RECORDS_LIMIT,
    ) -> str:
        """Format record query results for user display."""
        if not records:
            return "No records found matching the query."

        header = f"Found {len(records)} record(s)"
        if total_count is not None and total_count > len(records):
            header += f" (of {total_count} matching record(s))"
        header += ":"

        return self.format_records(records, max_display=max_display, header=header)

    def _format_aggregate_results(
        self,
        records: list[dict[str, Any]],
        fields: list[str],
        max_display: int = MAX_RECORDS_LIMIT,
    ) -> str:
        """Format aggregation results for user display."""
        if not records:
            return "No records found matching the aggregation."

        header_lines = [f"Aggregation Results: {len(records)} row(s)"]
        if fields:
            header_lines.append(f"Fields: {join_cleaned(fields)}")

        return self.format_records(
            records, max_display=max_display, header="\n".join(header_lines)
        )

    def _format_categories(self, categories: list[Any]) -> str:
        """Format portal themes for user display."""
        city = self.plugin_config.city_name
        if not categories:
            return f"No categories found on {city}'s open data portal."

        lines = [f"Categories on {city}'s open data portal:\n"]

        for i, cat in enumerate(categories, 1):
            if isinstance(cat, dict):
                name = self.portal_line(cat.get("name", cat.get("label", str(cat))))
                count = self.portal_line(cat.get("count", cat.get("value", "")))
                lines.append(f"  {i}. {name}: {count} dataset(s)")
            else:
                lines.append(f"  {i}. {self.portal_line(cat)}")

        return "\n".join(lines)

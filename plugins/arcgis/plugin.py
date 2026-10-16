"""ArcGIS Hub plugin implementation for OpenContext.

This plugin provides access to ArcGIS Hub open data catalogs
via the OGC API - Records (Hub Search API) and ArcGIS Feature Services.
"""

import ipaddress
import json
import logging
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx

from core.base_plugin import HTTP_RETRY, ROW_FORMATS, BaseOpenDataPlugin, ToolHandler
from core.interfaces import PluginType, ToolDefinition, ToolResult
from core.portal_content import html_to_text, join_cleaned
from plugins.arcgis.config_schema import ArcGISPluginConfig
from plugins.arcgis.metadata_xml import (
    MAX_METADATA_BYTES,
    definitions_for_layer,
    item_id_from,
    parse_entities,
)
from plugins.arcgis.where_validator import WhereValidator

logger = logging.getLogger(__name__)

# Feature Service URL shape accepted for auto-trust: an ArcGIS REST services
# path ending in a FeatureServer/MapServer, optionally with a layer index.
_ARCGIS_SERVICE_PATH = re.compile(
    r"/rest/services/.+/(FeatureServer|MapServer)(/\d+)?/?$", re.IGNORECASE
)
# A FeatureServer/MapServer URL split into its service root and optional
# layer id (``.../FeatureServer`` + ``4``).
_SERVICE_ROOT = re.compile(
    r"^(?P<root>.+/(?:FeatureServer|MapServer))(?:/(?P<layer>\d+))?/?$",
    re.IGNORECASE,
)
# Character budget for a dataset description in get_dataset (HUD's Fair
# Market Rents description is about 3,500 characters).
DESCRIPTION_MAX = 12_000
# Coded domain values listed per field in get_schema.
_MAX_DOMAIN_VALUES = 30
# Field definitions shown per field in get_schema (from the item metadata XML).
_MAX_DEFINITION = 600
# Seconds allowed for the optional metadata XML fetch in get_schema.
_METADATA_TIMEOUT = 10.0
# Items whose parsed metadata (or lasting miss) is kept in memory.
_MAX_CACHED_METADATA = 256
# Formal item metadata lives on the ArcGIS Online sharing API, not on the Hub
# site (a Hub domain answers this path with an HTML page).
_ITEM_METADATA_URL = (
    "https://www.arcgis.com/sharing/rest/content/items/{item_id}"
    "/info/metadata/metadata.xml"
)
# Statistic types accepted by aggregate_data (ArcGIS ``statisticType``).
STATISTIC_TYPES = ("count", "sum", "avg", "min", "max", "stddev")
# A plain field name or output alias.
_FIELD_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
# Upper bounds on aggregate_data inputs.
_MAX_STATISTICS = 20
_MAX_GROUP_BY = 10
# Hostname suffixes that are never valid public service hosts.
_BLOCKED_HOST_SUFFIXES = (".local", ".localhost", ".internal", ".localdomain")


class ArcGISPlugin(BaseOpenDataPlugin):
    """Plugin for accessing ArcGIS Hub open data catalogs.

    This plugin implements the DataPlugin interface on top of
    :class:`BaseOpenDataPlugin` and provides tools for searching datasets,
    retrieving dataset metadata, querying Feature Services, and exploring
    catalog aggregations.
    """

    plugin_name = "arcgis"
    plugin_type = PluginType.OPEN_DATA
    plugin_version = "1.2.0"

    config_class = ArcGISPluginConfig
    # Hub item IDs are 32-char hex; allow the underscore/hyphen variants seen
    # in layer references (e.g. abcdef..._0).
    id_pattern = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    provider_label = "ArcGIS Hub catalog"

    QUERYABLE_TYPES = {
        "Feature Layer",
        "Feature Service",
        "Map Service",
        "Table",
    }

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        # service root URL -> resolved layer URL (see _resolve_layer_url)
        self._layer_url_cache: dict[str, str] = {}
        # service root URL -> its layers and tables (see _service_layers)
        self._service_layers_cache: dict[str, list[dict[str, Any]]] = {}
        # layer URL -> layer metadata ({layer_url}?f=json, see _layer_metadata)
        self._layer_metadata_cache: dict[str, dict[str, Any]] = {}
        # item id -> parsed metadata XML entities (see _field_definitions);
        # [] records a missing or unusable document so it is not refetched.
        self._item_metadata_cache: dict[str, list] = {}

    async def initialize(self) -> bool:
        """Initialize ArcGIS Hub plugin and test connection.

        Returns:
            True if initialization succeeded
        """
        try:
            headers = {"Accept": "application/json"}
            feature_headers: dict[str, str] = {}
            if self.plugin_config.token:
                headers["Authorization"] = f"Bearer {self.plugin_config.token}"
                feature_headers["Authorization"] = f"Bearer {self.plugin_config.token}"

            # Create both clients via the shared helper so they are tracked
            # for shutdown by the base class.
            # Follow redirects (a renamed Hub domain keeps working) and drop
            # the bearer token if a hop leaves the trusted hosts. *.arcgis.com
            # and trusted_service_hosts stay trusted (feature services live
            # there); the same allow-list gates feature-service fetching.
            trusted = ("arcgis.com", *self.plugin_config.trusted_service_hosts)
            self.hub_client = self._create_http_client(
                base_url=self.plugin_config.portal_url,
                headers=headers,
                timeout=self.plugin_config.timeout,
                protect_headers=("Authorization",),
                trusted_hosts=trusted,
            )

            self.feature_client = self._create_http_client(
                headers=feature_headers,
                timeout=self.plugin_config.timeout,
                protect_headers=("Authorization",),
                trusted_hosts=trusted,
            )

            await self._call_hub_api("/api/search/v1/collections")

            self._initialized = True
            logger.info(
                f"ArcGIS Hub plugin initialized successfully for "
                f"{self.plugin_config.city_name}"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to initialize ArcGIS Hub plugin: {e}", exc_info=True)
            return False

    @HTTP_RETRY
    async def _call_hub_api(self, path: str, **kwargs: Any) -> httpx.Response:
        """Call the ArcGIS Hub Search API via the hub client (GET).

        Args:
            path: API path.
            **kwargs: Additional request arguments forwarded to httpx.

        Returns:
            The httpx response (caller is responsible for json parsing as
            appropriate).

        Raises:
            RuntimeError: On HTTP status errors.
        """
        if not self.hub_client:
            raise RuntimeError("Plugin not initialized")

        try:
            response = await self.hub_client.get(path, **kwargs)
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            self._raise_http_error(e, " Hub Search API")

        return response

    @HTTP_RETRY
    async def _call_feature_service(
        self, url: str, params: dict[str, Any]
    ) -> httpx.Response:
        """Call an ArcGIS Feature Service endpoint via the feature client (GET).

        Args:
            url: Absolute Feature Service URL.
            params: Query parameters.

        Returns:
            The httpx response (caller is responsible for json parsing).

        Raises:
            RuntimeError: On HTTP status errors.
        """
        if not self.feature_client:
            raise RuntimeError("Plugin not initialized")

        try:
            response = await self.feature_client.get(url, params=params)
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            self._raise_http_error(e, " Feature Service")

        return response

    def get_tools(self) -> list[ToolDefinition]:
        """Get list of tools provided by ArcGIS Hub plugin.

        Returns:
            List of tool definitions
        """
        city = self.plugin_config.city_name
        return [
            ToolDefinition(
                name="search_datasets",
                description=f"Search for datasets in {city}'s ArcGIS Hub catalog",
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Full-text search query",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of results (default: 10)",
                            "default": 10,
                            "minimum": 1,
                            "maximum": 100,
                        },
                    },
                    "required": ["query"],
                },
            ),
            ToolDefinition(
                name="get_dataset",
                description="Get metadata for a specific ArcGIS Hub dataset by ID",
                input_schema={
                    "type": "object",
                    "properties": {
                        "dataset_id": {
                            "type": "string",
                            "description": "32-char hex Hub item ID",
                        },
                    },
                    "required": ["dataset_id"],
                },
            ),
            ToolDefinition(
                name="get_aggregations",
                description=(
                    "Get facet counts for a field across the ArcGIS Hub catalog. "
                    "Useful for exploring available categories, types, or tags."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "field": {
                            "type": "string",
                            "description": (
                                "Field to aggregate. Available fields: "
                                '"type", "tags", "categories", "access"'
                            ),
                        },
                        "query": {
                            "type": "string",
                            "description": "Optional search query to scope the aggregation",
                        },
                    },
                    "required": ["field"],
                },
            ),
            ToolDefinition(
                name="get_schema",
                description=(
                    f"Get field schema for an ArcGIS Feature Service layer in {city}'s "
                    f"ArcGIS Hub catalog. Returns field names, types, and aliases "
                    f"directly usable in query_data where/out_fields, plus field "
                    f"definitions and units when the item's metadata has them. "
                    f"Provide the Hub "
                    f"dataset ID — the plugin resolves the Feature Service URL "
                    f"automatically (two-hop)."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "dataset_id": {
                            "type": "string",
                            "description": "Hub item ID (same as get_dataset)",
                        },
                        "layer": {
                            "type": "integer",
                            "description": (
                                "Layer or table id within the Feature Service, as "
                                "listed by get_dataset. Default: the layer the item "
                                "points to, or the service's first layer."
                            ),
                            "minimum": 0,
                        },
                    },
                    "required": ["dataset_id"],
                },
            ),
            ToolDefinition(
                name="query_data",
                description=(
                    "Query records from an ArcGIS Feature Service. Provide the Hub "
                    "dataset ID — the plugin resolves the Feature Service URL "
                    "automatically (two-hop). Use get_dataset first to confirm the "
                    "dataset has a queryable service URL. The reply gives the "
                    "total number of matching records and, when more remain, the "
                    "offset for the next page. Use format json or csv for rows "
                    "you will compute with, and order_by for stable paging."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "dataset_id": {
                            "type": "string",
                            "description": "Hub item ID (same as get_dataset)",
                        },
                        "layer": {
                            "type": "integer",
                            "description": (
                                "Layer or table id within the Feature Service, as "
                                "listed by get_dataset. Default: the layer the item "
                                "points to, or the service's first layer."
                            ),
                            "minimum": 0,
                        },
                        "where": {
                            "type": "string",
                            "description": "SQL WHERE clause for filtering",
                            "default": "1=1",
                        },
                        "out_fields": {
                            "type": "string",
                            "description": "Comma-separated field names to return",
                            "default": "*",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of records (default: 100)",
                            "default": 100,
                            "minimum": 1,
                            "maximum": 1000,
                        },
                        "offset": {
                            "type": "integer",
                            "description": (
                                "Number of matching records to skip, for paging "
                                "(default: 0)"
                            ),
                            "default": 0,
                            "minimum": 0,
                        },
                        "order_by": {
                            "type": "string",
                            "description": (
                                "Comma-separated field names to sort by, each "
                                'optionally followed by ASC or DESC (e.g. "COUNTY, '
                                'UNITS DESC")'
                            ),
                        },
                        "format": {
                            "type": "string",
                            "enum": list(ROW_FORMATS),
                            "description": (
                                "Output format: text (readable records, default), "
                                "json (array of objects), or csv (header plus one "
                                "line per record)"
                            ),
                            "default": "text",
                        },
                    },
                    "required": ["dataset_id"],
                },
            ),
            ToolDefinition(
                name="aggregate_data",
                description=(
                    "Compute statistics (count, sum, avg, min, max, stddev) on an "
                    "ArcGIS Feature Service layer, optionally grouped by fields, in "
                    "one server-side call. Use it for totals and counts by area "
                    "instead of paging through query_data. Null values are left out "
                    "of every statistic, so a sum over a field with suppressed "
                    "values is a lower bound. Use get_schema first for field names."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "dataset_id": {
                            "type": "string",
                            "description": "Hub item ID (same as get_dataset)",
                        },
                        "layer": {
                            "type": "integer",
                            "description": (
                                "Layer or table id within the Feature Service, as "
                                "listed by get_dataset. Default: the layer the item "
                                "points to, or the service's first layer."
                            ),
                            "minimum": 0,
                        },
                        "statistics": {
                            "type": "array",
                            "description": (
                                "Statistics to compute, e.g. "
                                '[{"type": "sum", "field": "HCV_PUBLIC", "as": '
                                '"vouchers"}, {"type": "count", "field": "GEOID"}]. '
                                "count may omit field to count records."
                            ),
                            "items": {
                                "type": "object",
                                "properties": {
                                    "type": {
                                        "type": "string",
                                        "enum": list(STATISTIC_TYPES),
                                    },
                                    "field": {"type": "string"},
                                    "as": {"type": "string"},
                                },
                                "required": ["type"],
                            },
                            "minItems": 1,
                            "maxItems": _MAX_STATISTICS,
                        },
                        "group_by": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Fields to group by (one row per group)",
                            "maxItems": _MAX_GROUP_BY,
                        },
                        "where": {
                            "type": "string",
                            "description": "SQL WHERE clause applied before grouping",
                            "default": "1=1",
                        },
                        "having": {
                            "type": "string",
                            "description": (
                                "Filter on the computed statistics, e.g. "
                                '"SUM(HCV_PUBLIC) > 100" (only on services that '
                                "support it)"
                            ),
                        },
                        "order_by": {
                            "type": "string",
                            "description": (
                                "Sort by group fields or statistic names, each "
                                'optionally ASC or DESC (e.g. "vouchers DESC")'
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of groups (default: 1000)",
                            "default": 1000,
                            "minimum": 1,
                            "maximum": 5000,
                        },
                        "format": {
                            "type": "string",
                            "enum": list(ROW_FORMATS),
                            "description": "Output format: text, json or csv",
                            "default": "text",
                        },
                    },
                    "required": ["dataset_id", "statistics"],
                },
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
                    "Use the get_dataset tool with a dataset ID from the list "
                    "to get details, then get_schema and query_data."
                ),
            ),
            "get_dataset": ToolHandler(
                handler=self._tool_get_dataset,
                required_args=("dataset_id",),
                guidance=(
                    "Use the get_schema tool with this dataset's ID to list "
                    "fields, then query_data to query features. Pass layer to "
                    "reach a layer or table other than the default."
                ),
            ),
            "get_aggregations": ToolHandler(
                handler=self._tool_get_aggregations, required_args=("field",)
            ),
            "get_schema": ToolHandler(
                handler=self._tool_get_schema, required_args=("dataset_id",)
            ),
            "query_data": ToolHandler(
                handler=self._tool_query_data, required_args=("dataset_id",)
            ),
            "aggregate_data": ToolHandler(
                handler=self._tool_aggregate_data,
                required_args=("dataset_id", "statistics"),
                guidance=(
                    "Statistics ignore null values: where a source suppresses "
                    "small counts as null, a sum is a lower bound and a count of "
                    "a field counts only non-null values."
                ),
            ),
        }

    async def _tool_search_datasets(self, arguments: dict[str, Any]) -> ToolResult:
        query = arguments.get("query", "")
        limit = arguments.get("limit", 10)
        result = await self._search_hub(query, limit)
        return ToolResult(
            content=[
                {
                    "type": "text",
                    "text": self._format_search_results(
                        result["results"], total=result["total"]
                    ),
                }
            ],
            success=True,
        )

    async def _tool_get_dataset(self, arguments: dict[str, Any]) -> ToolResult:
        dataset = await self.get_dataset(arguments["dataset_id"])
        await self._add_service_details(dataset)
        return ToolResult(
            content=[{"type": "text", "text": self._format_dataset(dataset)}],
            success=True,
        )

    async def _tool_get_aggregations(self, arguments: dict[str, Any]) -> ToolResult:
        field = arguments["field"]
        query = arguments.get("query")
        buckets = await self.get_aggregations(field, query)
        return ToolResult(
            content=[
                {"type": "text", "text": self._format_aggregations(field, buckets)}
            ],
            success=True,
        )

    async def _tool_get_schema(self, arguments: dict[str, Any]) -> ToolResult:
        schema = await self.get_schema(
            arguments["dataset_id"], layer=arguments.get("layer")
        )
        return ToolResult(
            content=[{"type": "text", "text": self._format_schema(schema)}],
            success=True,
        )

    async def _tool_query_data(self, arguments: dict[str, Any]) -> ToolResult:
        dataset_id = arguments["dataset_id"]
        where = arguments.get("where", "1=1")
        out_fields = arguments.get("out_fields", "*")
        limit = arguments.get("limit", 100)
        offset = arguments.get("offset", 0)
        order_by = arguments.get("order_by")
        fmt = arguments.get("format") or "text"
        if fmt not in ROW_FORMATS:
            raise ValueError(
                f"format must be one of {', '.join(ROW_FORMATS)} (got {fmt!r})"
            )
        page = await self._query_page(
            dataset_id,
            where,
            out_fields,
            limit,
            offset=offset,
            order_by=order_by,
            layer=arguments.get("layer"),
        )
        return ToolResult(
            content=[
                {
                    "type": "text",
                    "text": self._format_query_results(page, limit, fmt=fmt),
                }
            ],
            success=True,
        )

    async def _tool_aggregate_data(self, arguments: dict[str, Any]) -> ToolResult:
        fmt = arguments.get("format") or "text"
        if fmt not in ROW_FORMATS:
            raise ValueError(
                f"format must be one of {', '.join(ROW_FORMATS)} (got {fmt!r})"
            )
        result = await self.aggregate_features(
            arguments["dataset_id"],
            arguments["statistics"],
            group_by=arguments.get("group_by"),
            where=arguments.get("where", "1=1"),
            having=arguments.get("having"),
            order_by=arguments.get("order_by"),
            limit=arguments.get("limit", 1000),
            layer=arguments.get("layer"),
        )
        return ToolResult(
            content=[{"type": "text", "text": self._format_aggregate(result, fmt=fmt)}],
            success=True,
        )

    # ── DataPlugin abstract method implementations ──────────────────────

    async def search_datasets(
        self, query: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Search for datasets matching a query.

        Args:
            query: Search query string
            limit: Maximum number of results

        Returns:
            List of dataset metadata dictionaries
        """
        return (await self._search_hub(query, limit))["results"]

    async def _search_hub(self, query: str, limit: int) -> dict[str, Any]:
        """Search the Hub catalog; return ``{"results": [...], "total": int | None}``.

        ``total`` is the catalog-wide match count (``numberMatched``).
        """
        response = await self._call_hub_api(
            "/api/search/v1/collections/all/items",
            params={"q": query, "limit": limit},
        )

        data = response.json() or {}
        total = data.get("numberMatched")
        features = data.get("features", []) or []
        results = [
            self._extract_dataset_summary(feature.get("properties", {}) or {})
            for feature in features
        ]
        return {"results": results, "total": total if isinstance(total, int) else None}

    async def get_dataset(self, dataset_id: str) -> dict[str, Any]:
        """Get detailed metadata for a specific dataset.

        Args:
            dataset_id: Hub item ID

        Returns:
            Dataset metadata dictionary
        """
        response = await self._call_hub_api(
            f"/api/search/v1/collections/all/items/{dataset_id}",
        )

        feature = response.json()
        props = feature.get("properties", {})

        result = self._extract_dataset_summary(props)
        result.update(
            {
                "snippet": html_to_text(props.get("snippet")),
                "licenseInfo": html_to_text(props.get("licenseInfo")),
                "spatialReference": props.get("spatialReference", ""),
                "geometryType": props.get("geometryType", ""),
                "additionalResources": props.get("additionalResources", []),
                "numRecords": props.get("numRecords", props.get("recordCount")),
                "service_url": props.get("url", ""),
                "size": props.get("size"),
                "orgName": props.get("orgName", ""),
                "categories": props.get("categories", []),
                "typeKeywords": props.get("typeKeywords", []),
                "lastEditDate": self.short_date(
                    props.get("serviceLastEditDate") or props.get("lastEditDate")
                ),
                "accessInformation": html_to_text(props.get("accessInformation")),
            }
        )
        return result

    async def get_schema(
        self, dataset_id: str, *, layer: int | None = None
    ) -> list[dict[str, Any]]:
        """Get field schema for a dataset's Feature Service layer.

        Resolves the Feature Service URL via :meth:`get_dataset` (two-hop),
        then fetches the layer metadata (``{layer_url}?f=json``) and
        returns the ``fields`` list (name, type, alias). Field definitions
        and units come from the item's metadata XML when it has them (see
        :meth:`_field_definitions`).

        Args:
            dataset_id: Hub item ID
            layer: Layer or table id within the service; ``None`` uses the
                default layer (see :meth:`_resolve_layer_url`).

        Returns:
            List of field definition dictionaries (name, type, alias, plus
            ``length`` for string fields, ``domain`` when the service or the
            metadata defines one, and ``definition``/``units`` from the
            metadata)

        Raises:
            ValueError: If the dataset has no queryable service URL.
        """
        layer_url, dataset = await self._layer_url_for(dataset_id, layer)
        fields, layer_metadata = await self._layer_fields(layer_url)
        definitions = await self._field_definitions(
            dataset.get("id") or dataset_id,
            layer_metadata.get("name"),
            default_layer=layer is None,
        )
        schema = []
        for f in fields:
            entry = {
                "name": f.get("name", ""),
                "type": f.get("type", ""),
                "alias": f.get("alias", ""),
            }
            if isinstance(f.get("length"), int) and f.get("type") == (
                "esriFieldTypeString"
            ):
                entry["length"] = f["length"]
            if isinstance(f.get("domain"), dict):
                entry["domain"] = f["domain"]
            extra = definitions.get(str(entry["name"]).lower(), {})
            for key in ("definition", "units"):
                if extra.get(key):
                    entry[key] = extra[key]
            if "domain" not in entry and extra.get("domain"):
                entry["domain"] = extra["domain"]
            schema.append(entry)
        return schema

    async def _field_definitions(
        self, dataset_id: Any, layer_name: Any, *, default_layer: bool = True
    ) -> dict[str, dict[str, Any]]:
        """Field definitions for a layer from the item's metadata XML.

        Best-effort: a dataset id that is not an ArcGIS item id, a missing
        document, an HTTP error, an oversized or malformed document all give
        ``{}`` and never fail the schema call. Parsed documents and lasting
        misses (4xx, oversized, unparseable) are cached per item; network
        errors and 5xx responses are not, so a blip does not hide an item's
        definitions for the life of a warm Lambda.

        Returns:
            ``{lower-cased field name: {"definition", "units", "domain"}}``
            with whichever keys the document provides.
        """
        item_id = item_id_from(dataset_id)
        if not item_id or not self.feature_client:
            return {}
        entities = self._item_metadata_cache.get(item_id)
        if entities is None:
            try:
                body, lasting = await self._fetch_item_metadata(item_id)
            except Exception as e:  # noqa: BLE001 - metadata is optional
                logger.info("No metadata XML for item %s: %s", item_id, e)
                body, lasting = None, False
            entities = parse_entities(body) if body else []
            if body or lasting:
                if len(self._item_metadata_cache) >= _MAX_CACHED_METADATA:
                    self._item_metadata_cache.pop(next(iter(self._item_metadata_cache)))
                self._item_metadata_cache[item_id] = entities
        return definitions_for_layer(
            entities,
            layer_name if isinstance(layer_name, str) else None,
            default_layer=default_layer,
        )

    async def _fetch_item_metadata(self, item_id: str) -> tuple[bytes | None, bool]:
        """Download an item's metadata XML, capped at ``MAX_METADATA_BYTES``.

        Streams the body so an oversized document is abandoned without
        being held in memory, uses a short timeout, and sends no
        ``Authorization`` header: the document is public for public items,
        and an ArcGIS Enterprise token must not travel to ArcGIS Online.

        Returns:
            ``(body, lasting)``: the document bytes (``None`` when there is
            none to parse) and whether a miss is lasting enough to cache.
        """
        # A bare Request: ``send`` does not merge the client's default
        # headers, so the bearer token is never attached.
        request = httpx.Request(
            "GET",
            _ITEM_METADATA_URL.format(item_id=item_id),
            extensions={"timeout": httpx.Timeout(_METADATA_TIMEOUT).as_dict()},
        )
        response = await self.feature_client.send(request, stream=True)
        try:
            if response.status_code != 200:
                return None, 400 <= response.status_code < 500
            declared = response.headers.get("content-length", "")
            if declared.isdigit() and int(declared) > MAX_METADATA_BYTES:
                return None, True
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_METADATA_BYTES:
                    return None, True
                chunks.append(chunk)
            return b"".join(chunks), True
        finally:
            await response.aclose()

    async def _layer_url_for(
        self, dataset_id: str, layer: int | None, *, queryable_only: bool = False
    ) -> tuple[str, dict[str, Any]]:
        """Resolve a Hub item to a trusted layer URL (two-hop).

        Args:
            dataset_id: Hub item ID.
            layer: Layer or table id, or ``None`` for the default layer.
            queryable_only: Refuse item types outside :attr:`QUERYABLE_TYPES`.

        Returns:
            ``(layer_url, dataset)``: the validated layer URL
            (``.../FeatureServer/<id>``) and the item metadata.

        Raises:
            ValueError: If the item has no service URL, is not a queryable
                type, its host is not trusted, or ``layer`` is invalid.
        """
        dataset = await self.get_dataset(dataset_id)
        service_url = dataset.get("service_url")
        if not service_url:
            raise ValueError(
                f"Dataset {dataset_id} does not have a queryable Feature Service URL"
            )
        ds_type = dataset.get("type", "")
        if queryable_only and ds_type and ds_type not in self.QUERYABLE_TYPES:
            raise ValueError(
                f"Dataset type '{ds_type}' is not queryable. "
                f"query_data only supports: {', '.join(sorted(self.QUERYABLE_TYPES))}."
            )
        service_url = self._validate_feature_url(
            service_url,
            self.plugin_config.portal_url,
            self.plugin_config.trusted_service_hosts,
            auto_trust=self.plugin_config.auto_trust_hub_services,
        )
        return await self._resolve_layer_url(service_url, layer), dataset

    async def _layer_fields(
        self, layer_url: str
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Return a layer's ``fields`` and its raw metadata.

        Reads ``{layer_url}?f=json``; when that body is unusable, the fields
        come from a one-row query and the metadata is ``{}``.
        """
        metadata = await self._layer_metadata(layer_url)
        if metadata is not None:
            return metadata["fields"], metadata
        # Some self-hosted services return HTML or a malformed body on the
        # layer metadata endpoint while the query endpoint works (seen on
        # Columbus and Indianapolis). Derive the schema from a one-row query
        # instead of failing.
        logger.warning(
            "Layer metadata at %s was not usable; deriving schema from a 1-row query",
            layer_url,
        )
        return await self._schema_from_query(layer_url), {}

    async def _layer_metadata(self, layer_url: str) -> dict[str, Any] | None:
        """Read ``{layer_url}?f=json``; ``None`` when the body is unusable.

        Usable bodies (JSON with a ``fields`` list and no error envelope) are
        cached per layer URL, so the data edit date is available to later
        queries without another request.

        Raises:
            RuntimeError: On HTTP errors (via :meth:`_call_feature_service`).
        """
        cached = self._layer_metadata_cache.get(layer_url)
        if cached is not None:
            return cached
        # The format must travel as a query parameter: httpx replaces the
        # URL's own query string with ``params``, so ``{url}?f=json`` plus
        # ``params={}`` sent a bare request and ArcGIS answered with an HTML
        # page ("Expecting value: line 1 column 1" on every dataset).
        response = await self._call_feature_service(layer_url, {"f": "json"})
        if self._fields_from_layer_metadata(response) is None:
            return None
        metadata = response.json()
        self._layer_metadata_cache[layer_url] = metadata
        return metadata

    @staticmethod
    def _fields_from_layer_metadata(response: httpx.Response) -> list | None:
        """Extract ``fields`` from a layer metadata response, or ``None``.

        Returns ``None`` when the body is not JSON, carries an ArcGIS
        ``error`` envelope, or has no ``fields`` list, so the caller can
        fall back to deriving the schema from a query.
        """
        try:
            data = response.json()
        except Exception:  # noqa: BLE001 - any parse failure triggers fallback
            return None
        if not isinstance(data, dict) or data.get("error"):
            return None
        fields = data.get("fields")
        return fields if isinstance(fields, list) else None

    async def _schema_from_query(self, service_url: str) -> list[dict[str, Any]]:
        """Derive a layer's field list from a one-row ``/query`` response.

        Args:
            service_url: Validated layer URL (``.../FeatureServer/<n>``).

        Returns:
            The ``fields`` list reported by the query endpoint.

        Raises:
            ValueError: If the query endpoint also fails to return usable JSON.
            RuntimeError: If the query endpoint reports an ArcGIS error.
        """
        params = {
            "where": "1=1",
            "outFields": "*",
            "resultRecordCount": 1,
            "returnGeometry": "false",
            "f": "json",
        }
        response = await self._call_feature_service(f"{service_url}/query", params)
        try:
            data = response.json()
        except Exception as json_err:
            content_type = response.headers.get("content-type", "")
            raise ValueError(
                "Feature Service returned non-JSON responses on both the layer "
                f"metadata and query endpoints (content-type: {content_type}); "
                "the dataset URL may not point to a queryable ArcGIS Feature "
                "Service."
            ) from json_err
        error_in_body = data.get("error") if isinstance(data, dict) else None
        if error_in_body:
            code = error_in_body.get("code", "unknown")
            msg = error_in_body.get("message", "Unknown error")
            raise RuntimeError(
                f"Feature Service schema query failed (code {code}): {msg}"
            )
        fields = data.get("fields", []) if isinstance(data, dict) else []
        return fields if isinstance(fields, list) else []

    async def query_data(
        self,
        resource_id: str,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Query data from a dataset (DataPlugin contract).

        Compiles ``field: value`` filters into an ArcGIS WHERE clause using
        :meth:`BaseOpenDataPlugin.build_where_clause`, validating each field
        identifier with :meth:`WhereValidator.scan_forbidden_keywords` to
        block SQL injection through field names. Defaults to ``"1=1"`` when
        no filters are supplied.

        Args:
            resource_id: Hub item ID
            filters: Optional field/value pairs compiled to a WHERE clause
            limit: Maximum number of records

        Returns:
            List of data records
        """
        where_clause = "1=1"
        if filters:
            for field in filters:
                forbidden = WhereValidator.scan_forbidden_keywords(field)
                if forbidden:
                    raise ValueError(f"Invalid field name: {forbidden}")
            built = self.build_where_clause(filters)
            if built:
                where_clause = built

        return await self._query_features(resource_id, where_clause, "*", limit)

    async def _query_features(
        self,
        dataset_id: str,
        where: str,
        out_fields: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Query records from an ArcGIS Feature Service (two-hop resolution).

        Args:
            dataset_id: Hub item ID
            where: SQL WHERE clause (validated via :class:`WhereValidator`)
            out_fields: Comma-separated field names to return
            limit: Maximum number of records

        Returns:
            List of feature attribute dicts
        """
        page = await self._query_page(
            dataset_id, where, out_fields, limit, with_count=False
        )
        return page["records"]

    async def _query_page(
        self,
        dataset_id: str,
        where: str,
        out_fields: str,
        limit: int,
        *,
        offset: int = 0,
        order_by: str | None = None,
        with_count: bool = True,
        layer: int | None = None,
    ) -> dict[str, Any]:
        """Query one page of records, with the total match count.

        Resolves the Feature Service URL (two-hop), validates ``where`` and
        ``order_by``, and sends ``resultOffset``/``orderByFields`` when
        given. The total comes from a ``returnCountOnly`` request on the
        same WHERE clause; it is skipped when the page itself proves the
        total (a first page that came back short of ``limit`` with no
        transfer-limit flag).

        Args:
            dataset_id: Hub item ID.
            where: SQL WHERE clause (validated via :class:`WhereValidator`).
            out_fields: Comma-separated field names to return.
            limit: Maximum number of records (capped at 1000).
            offset: Matching records to skip.
            order_by: Sort order (validated via
                :meth:`WhereValidator.validate_order_by`).
            with_count: Whether to look up the total match count.
            layer: Layer or table id within the service; ``None`` uses the
                default layer.

        Returns:
            ``{"records", "offset", "total", "exceeded"}``, where ``total`` is
            ``None`` when unknown and ``exceeded`` mirrors the service's
            ``exceededTransferLimit`` flag.
        """
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError(f"limit must be at least 1 (got {limit})")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError(f"offset must be a non-negative integer (got {offset})")
        sort = WhereValidator.validate_order_by(order_by)
        where_clause = WhereValidator.validate(where)

        layer_url, dataset = await self._layer_url_for(
            dataset_id, layer, queryable_only=True
        )
        query_url = f"{layer_url}/query"
        record_count = min(limit, 1000)
        params: dict[str, Any] = {
            "where": where_clause,
            "outFields": out_fields,
            "resultRecordCount": record_count,
            "f": "json",
            "returnGeometry": "false",
        }
        if offset:
            params["resultOffset"] = offset
        if sort:
            params["orderByFields"] = sort

        data = self._query_json(await self._call_feature_service(query_url, params))
        features = data.get("features") or []
        records = [f.get("attributes", {}) for f in features]
        exceeded = bool(data.get("exceededTransferLimit"))

        total: int | None = None
        if with_count:
            if offset == 0 and len(records) < record_count and not exceeded:
                total = len(records)
            else:
                total = await self._count_features(query_url, where_clause)

        return {
            "records": records,
            "offset": offset,
            "total": total,
            "exceeded": exceeded,
            # Only a cached layer description is used here, so the footer
            # never costs an extra request.
            "source": self._source(
                dataset, layer_url, self._layer_metadata_cache.get(layer_url)
            ),
        }

    @staticmethod
    def _query_json(response: httpx.Response) -> dict[str, Any]:
        """Parse a Feature Service ``/query`` response, raising on errors.

        Raises:
            ValueError: If the body is not JSON.
            RuntimeError: If the body is an ArcGIS error envelope.
        """
        try:
            data = response.json()
        except Exception as json_err:
            content_type = response.headers.get("content-type", "")
            raise ValueError(
                f"Feature Service returned non-JSON response "
                f"(content-type: {content_type}). The dataset URL may not "
                f"point to a queryable ArcGIS Feature Service."
            ) from json_err

        error_in_body = data.get("error") if isinstance(data, dict) else None
        if error_in_body:
            code = error_in_body.get("code", "unknown")
            msg = error_in_body.get("message", "Unknown error")
            details = error_in_body.get("details", [])
            detail_str = "; ".join(details) if details else ""
            raise RuntimeError(
                f"Feature Service query failed (code {code}): {msg}"
                + (f" — {detail_str}" if detail_str else "")
            )
        return data if isinstance(data, dict) else {}

    async def _count_features(self, query_url: str, where: str) -> int | None:
        """Return how many records match ``where``, or ``None`` if unknown.

        A failed count does not fail the query; the page is still returned
        without a total.
        """
        params = {"where": where, "returnCountOnly": "true", "f": "json"}
        try:
            response = await self._call_feature_service(query_url, params)
            count = response.json().get("count")
        except Exception as exc:  # noqa: BLE001 - the count is best-effort
            logger.warning("Record count query failed for %s: %s", query_url, exc)
            return None
        if isinstance(count, bool) or not isinstance(count, int):
            return None
        return count

    async def aggregate_features(
        self,
        dataset_id: str,
        statistics: list[dict[str, Any]],
        *,
        group_by: list[str] | None = None,
        where: str = "1=1",
        having: str | None = None,
        order_by: str | None = None,
        limit: int = 1000,
        layer: int | None = None,
    ) -> dict[str, Any]:
        """Run a server-side statistics query (``outStatistics``).

        Every field named in ``statistics`` and ``group_by`` must exist in the
        layer's schema (matched case-insensitively and sent with the schema's
        spelling). ``where`` and ``having`` go through
        :class:`WhereValidator`, ``order_by`` through
        :meth:`WhereValidator.validate_order_by`. A ``count`` without a field
        counts records using the layer's object-id field.

        Args:
            dataset_id: Hub item ID.
            statistics: ``[{"type", "field", "as"}]``; ``type`` is one of
                :data:`STATISTIC_TYPES`, ``as`` names the output column
                (default ``<type>_<field>``).
            group_by: Fields to group by.
            where: WHERE clause applied before grouping.
            having: Filter on the computed statistics.
            order_by: Sort order over group fields or output names.
            limit: Maximum number of groups returned.
            layer: Layer or table id, or ``None`` for the default layer.

        Returns:
            ``{"rows", "total_groups", "layer_url", "exceeded"}``.

        Raises:
            ValueError: On invalid arguments, unknown fields, or a layer that
                does not support statistics.
            RuntimeError: If the service reports an error.
        """
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError(f"limit must be at least 1 (got {limit})")
        if not isinstance(statistics, list) or not statistics:
            raise ValueError("statistics must be a non-empty list")
        if len(statistics) > _MAX_STATISTICS:
            raise ValueError(f"statistics accepts at most {_MAX_STATISTICS} entries")
        group_by = group_by or []
        if not isinstance(group_by, list) or not all(
            isinstance(g, str) for g in group_by
        ):
            raise ValueError("group_by must be a list of field names")
        if len(group_by) > _MAX_GROUP_BY:
            raise ValueError(f"group_by accepts at most {_MAX_GROUP_BY} fields")
        for name in group_by:
            self._check_field_name(name, "group_by field")
        requested = [self._parse_statistic(stat) for stat in statistics]
        where_clause = WhereValidator.validate(where)
        having_clause = (
            WhereValidator.validate(having)
            if isinstance(having, str) and having.strip()
            else None
        )
        if having is not None and not isinstance(having, str):
            raise ValueError("having must be a string")
        sort = WhereValidator.validate_order_by(order_by)

        layer_url, dataset = await self._layer_url_for(
            dataset_id, layer, queryable_only=True
        )
        fields, metadata = await self._layer_fields(layer_url)
        capabilities = metadata.get("advancedQueryCapabilities") or {}
        if capabilities.get("supportsStatistics") is False:
            raise ValueError(
                "This layer does not support server-side statistics; use "
                "query_data and aggregate the rows instead."
            )

        by_name = {
            str(f.get("name", "")).lower(): f for f in fields if isinstance(f, dict)
        }
        object_id = next(
            (f.get("name") for f in fields if f.get("type") == "esriFieldTypeOID"),
            None,
        )

        def schema_name(name: str, role: str) -> str:
            field = by_name.get(name.lower())
            if field is None:
                raise ValueError(
                    f"Unknown {role} {name!r}; use get_schema to list this "
                    "layer's fields"
                )
            return str(field["name"])

        groups = [schema_name(g, "group_by field") for g in group_by]
        out_statistics = []
        out_names: set[str] = {g.lower() for g in groups}
        for stat_type, field, alias in requested:
            if field is None:
                if stat_type != "count":
                    raise ValueError(f"{stat_type} needs a field")
                if not object_id:
                    raise ValueError(
                        "count without a field needs an object-id field; name "
                        "a field to count"
                    )
                field = object_id
            else:
                field = schema_name(field, "statistics field")
            alias = alias or f"{stat_type}_{field}"
            if alias.lower() in out_names:
                raise ValueError(
                    f"Output name {alias!r} is used twice; give each statistic "
                    "a distinct 'as'"
                )
            out_names.add(alias.lower())
            out_statistics.append(
                {
                    "statisticType": stat_type,
                    "onStatisticField": field,
                    "outStatisticFieldName": alias,
                }
            )

        params: dict[str, Any] = {
            "where": where_clause,
            "outStatistics": json.dumps(out_statistics),
            "returnGeometry": "false",
            "f": "json",
        }
        if groups:
            params["groupByFieldsForStatistics"] = ",".join(groups)
        if having_clause:
            params["havingClause"] = having_clause
        if sort:
            params["orderByFields"] = sort

        data = self._query_json(
            await self._call_feature_service(f"{layer_url}/query", params)
        )
        rows = [f.get("attributes", {}) for f in data.get("features") or []]
        return {
            "rows": rows[:limit],
            "total_groups": len(rows),
            "layer_url": layer_url,
            "exceeded": bool(data.get("exceededTransferLimit")),
            "source": self._source(dataset, layer_url, metadata),
        }

    @staticmethod
    def _check_field_name(name: Any, role: str) -> str:
        """Require a plain field name or output alias."""
        if not isinstance(name, str) or not _FIELD_NAME.match(name):
            raise ValueError(
                f"Invalid {role} {str(name)[:80]!r}: use a plain field name "
                "(letters, digits, underscores)"
            )
        return name

    def _parse_statistic(self, stat: Any) -> tuple[str, str | None, str | None]:
        """Validate one ``statistics`` entry; return ``(type, field, as)``."""
        if not isinstance(stat, dict):
            raise ValueError(
                'Each statistic must be an object like {"type": "sum", '
                '"field": "UNITS"}'
            )
        stat_type = str(stat.get("type", "")).lower()
        if stat_type not in STATISTIC_TYPES:
            raise ValueError(
                f"Unknown statistic type {str(stat.get('type'))[:40]!r}; use one "
                f"of {', '.join(STATISTIC_TYPES)}"
            )
        field = stat.get("field")
        if field in (None, "", "*"):
            field = None
        else:
            self._check_field_name(field, "statistics field")
        alias = stat.get("as")
        if alias in (None, ""):
            alias = None
        else:
            self._check_field_name(alias, "statistic name")
        return stat_type, field, alias

    # ── Aggregations (standalone helper, not a DataPlugin method) ───────

    async def get_aggregations(
        self, field: str, q: str | None = None
    ) -> list[dict[str, Any]]:
        """Get facet counts for a field across the ArcGIS Hub catalog.

        Args:
            field: Field to aggregate (e.g. "type", "tags").
            q: Optional search query to scope the aggregation.

        Returns:
            List of ``{"key", "doc_count"}`` buckets.
        """
        params: dict[str, Any] = {}
        if q:
            params["q"] = q

        try:
            response = await self._call_hub_api(
                "/api/search/v1/collections/all/aggregations", params=params
            )
        except RuntimeError as e:
            logger.warning(f"Hub Aggregations API error: {e}")
            return []

        data = response.json()
        logger.debug(f"Aggregations raw response: {data}")

        aggregations = data.get("aggregations", {})
        terms = aggregations.get("terms", []) if isinstance(aggregations, dict) else []

        for term_group in terms:
            if term_group.get("field") == field:
                raw_buckets = term_group.get("aggregations", [])
                return [
                    {"key": b.get("label", ""), "doc_count": b.get("value", 0)}
                    for b in raw_buckets
                ]

        return []

    # ── Health check ────────────────────────────────────────────────────

    async def health_check(self) -> bool:
        """Check if the ArcGIS Hub API is accessible.

        Returns:
            True if healthy
        """
        try:
            await self._call_hub_api("/api/search/v1/collections")
            return True
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False

    # ── Private helpers ─────────────────────────────────────────────────

    @staticmethod
    def _validate_feature_url(
        service_url: str,
        portal_url: str,
        trusted_hosts: tuple[str, ...] | list[str] = (),
        *,
        auto_trust: bool = False,
    ) -> str:
        """Restrict Feature Service URLs to trusted hosts.

        Parses ``service_url`` and requires the scheme to be http/https and
        the host to end with ``.arcgis.com``, equal the configured portal
        host, or match one of ``trusted_hosts`` (exact host or subdomain,
        case-insensitive). This prevents a crafted dataset record from
        steering Feature Service queries to arbitrary hosts (SSRF). Ported
        from thealphacubicle/OpenContext (Feature/security update #37).

        Hub catalogs commonly reference services self-hosted on city
        domains (``gis.charlottenc.gov``, ``maps2.dcgis.dc.gov``, ...).
        With ``auto_trust`` (the ``auto_trust_hub_services`` config, on by
        default) such a host is accepted when the URL is https, the host is
        a public DNS name (not an IP literal, not a single label, not an
        internal suffix), and the path is an ArcGIS REST service path
        (``/rest/services/.../FeatureServer|MapServer[/<layer>]``). The
        plugin never sends its bearer token to auto-trusted hosts (see
        ``protect_headers`` in :meth:`initialize`). Operators who want a
        strict allow-list set ``auto_trust_hub_services: false`` and list
        hosts in ``trusted_service_hosts``.

        Args:
            service_url: Feature Service URL resolved from a dataset record.
            portal_url: Configured portal URL (its host is the allow-listed
                fallback for self-hosted ArcGIS portals).
            trusted_hosts: Extra hostnames from ``trusted_service_hosts``
                config.
            auto_trust: Accept Hub-referenced ArcGIS service URLs on hosts
                that are not explicitly listed (see above).

        Returns:
            The validated ``service_url`` (surrounding whitespace stripped).

        Raises:
            ValueError: If the scheme is not http/https or the host is not
                trusted. The message starts with ``untrusted_service_host:``
                and names the host so callers and agents can act on it.
        """
        service_url = (service_url or "").strip()
        parsed = urlparse(service_url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(
                f"Feature Service URL must use http or https (got: {parsed.scheme!r})"
            )
        host = (parsed.hostname or "").lower()
        portal_host = (urlparse(portal_url).hostname or "").lower()
        if not host:
            raise ValueError("Feature Service URL must include a hostname")
        if host == portal_host or host.endswith(".arcgis.com"):
            return service_url
        for trusted in trusted_hosts:
            trusted = trusted.lower().lstrip(".")
            if host == trusted or host.endswith(f".{trusted}"):
                return service_url

        if auto_trust:
            reason = ArcGISPlugin._auto_trust_refusal(parsed, host)
            if reason is None:
                logger.info(
                    "Auto-trusting Hub-referenced Feature Service host %r "
                    "(auto_trust_hub_services=true)",
                    host,
                )
                return service_url
            hint = f"auto-trust refused: {reason}; "
        else:
            hint = "auto_trust_hub_services is off; "

        raise ValueError(
            f"untrusted_service_host: {host!r} — Feature Service URL host is "
            f"not trusted ({hint}must end with '.arcgis.com', match portal host "
            f"{portal_host!r}, or be listed in trusted_service_hosts). Add "
            f"{host!r} to trusted_service_hosts in config.yaml to allow it."
        )

    @staticmethod
    def _auto_trust_refusal(parsed: Any, host: str) -> str | None:
        """Why a Hub-referenced service URL may not be auto-trusted, or None.

        Args:
            parsed: ``urlparse`` result for the service URL.
            host: Lowercased hostname from the URL.

        Returns:
            A short reason string when the URL fails the auto-trust rules,
            ``None`` when it qualifies.
        """
        if parsed.scheme != "https":
            return "only https URLs qualify"
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            return "IP literals are never trusted"
        if "." not in host or host.endswith(_BLOCKED_HOST_SUFFIXES):
            return "host is not a public DNS name"
        if not _ARCGIS_SERVICE_PATH.search(parsed.path or ""):
            return "path is not an ArcGIS REST FeatureServer/MapServer path"
        return None

    async def _resolve_layer_url(
        self, service_url: str, layer: int | None = None
    ) -> str:
        """Return the layer URL to query for a service URL.

        With ``layer``, the service's layers and tables are read and the id
        must be one of them (any layer index already in ``service_url`` is
        replaced). Without it, a URL that already names a layer
        (``.../FeatureServer/4``) is returned as is; for a service root the
        first layer is used, falling back to the first table, then to ``0``.
        Services whose only layer is not id 0 (HUD's "Low to Moderate
        Income Population by Tract" is layer 4, "Opportunity Zones" is
        layer 13) were unreachable when ``/0`` was assumed. Default
        resolutions are cached per service URL.

        Args:
            service_url: Validated Feature Service or layer URL.
            layer: Explicit layer or table id, or ``None`` for the default.

        Returns:
            A layer URL (``.../FeatureServer/<id>``).

        Raises:
            ValueError: If ``layer`` is not a non-negative integer, the URL is
                not a FeatureServer/MapServer URL, or the service does not
                list that id.
        """
        stripped = service_url.rstrip("/")
        match = _SERVICE_ROOT.match(stripped)

        if layer is not None:
            if isinstance(layer, bool) or not isinstance(layer, int) or layer < 0:
                raise ValueError(
                    f"layer must be a non-negative integer (got {layer!r})"
                )
            if not match:
                raise ValueError(
                    "layer can only be used with a FeatureServer or MapServer "
                    "service URL"
                )
            root = match.group("root")
            listed = await self._service_layers(root)
            # An unreadable layer list is not proof the id is wrong; let the
            # service answer for it.
            if listed and layer not in {entry["id"] for entry in listed}:
                raise ValueError(
                    f"layer {layer} is not in this service. Available: "
                    + "; ".join(self._describe_layer(entry) for entry in listed)
                )
            return f"{root}/{layer}"

        if not match or match.group("layer") is not None:
            return stripped

        cached = self._layer_url_cache.get(stripped)
        if cached is not None:
            return cached

        listed = await self._service_layers(stripped)
        # Layers are listed before tables, so the first entry is the first
        # layer, or the first table when there are no layers.
        layer_id = listed[0]["id"] if listed else 0
        resolved = f"{stripped}/{layer_id}"
        self._layer_url_cache[stripped] = resolved
        return resolved

    async def _service_layers(self, service_root: str) -> list[dict[str, Any]]:
        """List a service's layers, then tables, from ``{root}?f=json``.

        Each entry is ``{"id", "name", "kind", "geometry"}`` where ``kind`` is
        ``"layer"`` or ``"table"``. Returns ``[]`` (and logs a warning) when
        the service description cannot be read; successful reads are cached.

        Args:
            service_root: Validated ``.../FeatureServer`` or ``.../MapServer`` URL.
        """
        cached = self._service_layers_cache.get(service_root)
        if cached is not None:
            return cached
        try:
            response = await self._call_feature_service(service_root, {"f": "json"})
            data = response.json() or {}
        except Exception as exc:  # noqa: BLE001 - metadata unreachable: no list
            logger.warning(
                "Could not read layer list for %s (%s); assuming layer 0",
                service_root,
                exc,
            )
            return []

        entries: list[dict[str, Any]] = []
        for kind, key in (("layer", "layers"), ("table", "tables")):
            for item in data.get(key) or []:
                if not isinstance(item, dict):
                    continue
                layer_id = item.get("id")
                if isinstance(layer_id, bool) or not isinstance(layer_id, int):
                    continue
                entries.append(
                    {
                        "id": layer_id,
                        "name": item.get("name", ""),
                        "kind": kind,
                        "geometry": item.get("geometryType", ""),
                    }
                )
        self._service_layers_cache[service_root] = entries
        return entries

    async def _add_service_details(self, dataset: dict[str, Any]) -> None:
        """Add ``layers`` and the default layer's edit dates, for ``get_dataset``.

        Sets ``dataset["layers"]`` (see :meth:`_dataset_layers`) and, when the
        default layer's description can be read, ``dataLastEditDate`` and
        ``schemaLastEditDate`` from its ``editingInfo``. Every lookup is
        best-effort: ``get_dataset`` never fails because of them.
        """
        dataset["layers"] = await self._dataset_layers(dataset)
        default = next((e for e in dataset["layers"] if e.get("default")), None)
        if default is None:
            return
        layer_url = f"{default['root']}/{default['id']}"
        try:
            metadata = await self._layer_metadata(layer_url)
        except Exception as exc:  # noqa: BLE001 - dates are optional
            logger.warning("Could not read layer metadata for %s: %s", layer_url, exc)
            return
        editing = (metadata or {}).get("editingInfo") or {}
        dataset["dataLastEditDate"] = self.short_date(
            editing.get("dataLastEditDate") or editing.get("lastEditDate")
        )
        dataset["schemaLastEditDate"] = self.short_date(
            editing.get("schemaLastEditDate")
        )

    async def _dataset_layers(self, dataset: dict[str, Any]) -> list[dict[str, Any]]:
        """Layers and tables of a dataset's service, for ``get_dataset``.

        Marks the entry that queries use by default with ``"default": True``
        and records the service root in ``"root"``. Returns ``[]`` when the
        dataset has no service URL, the host is not trusted, or the service
        description cannot be read; ``get_dataset`` still succeeds.
        """
        service_url = dataset.get("service_url")
        if not service_url:
            return []
        try:
            service_url = self._validate_feature_url(
                service_url,
                self.plugin_config.portal_url,
                self.plugin_config.trusted_service_hosts,
                auto_trust=self.plugin_config.auto_trust_hub_services,
            )
        except ValueError:
            return []
        match = _SERVICE_ROOT.match(service_url.rstrip("/"))
        if not match:
            return []
        listed = await self._service_layers(match.group("root"))
        if not listed:
            return []
        default_id = (
            int(match.group("layer"))
            if match.group("layer") is not None
            else listed[0]["id"]
        )
        root = match.group("root")
        return [
            {**entry, "default": entry["id"] == default_id, "root": root}
            for entry in listed
        ]

    def _describe_layer(self, entry: dict[str, Any]) -> str:
        """Render one layer/table entry as ``1: SAFMR_table (table)``."""
        name = self.portal_line(entry.get("name"), default="unnamed")
        if entry.get("kind") == "table":
            kind = "table"
        else:
            geometry = str(entry.get("geometry") or "")
            shape = geometry.removeprefix("esriGeometry").lower()
            kind = f"{self.portal_line(shape)} layer" if shape else "layer"
        return f"{entry.get('id')}: {name} ({kind})"

    @staticmethod
    def _ensure_layer_url(service_url: str) -> str:
        """Append /0 if the URL points at a FeatureServer or MapServer root
        without a layer index (e.g. .../FeatureServer -> .../FeatureServer/0).
        Kept for callers that cannot await; :meth:`_resolve_layer_url` is
        preferred.
        """
        stripped = service_url.rstrip("/")
        if re.search(r"/(FeatureServer|MapServer)$", stripped, re.IGNORECASE):
            return f"{stripped}/0"
        return stripped

    @staticmethod
    def _epoch_ms_to_iso(epoch_ms: Any) -> str:
        """Render an ArcGIS epoch-millisecond timestamp as ``YYYY-MM-DD``."""
        return BaseOpenDataPlugin.short_date(epoch_ms)

    @staticmethod
    def _extract_dataset_summary(props: dict[str, Any]) -> dict[str, Any]:
        """Summarize a Hub item; the description is converted to plain text.

        The description is kept whole: search results show a 300-character
        excerpt and ``get_dataset`` the full text (up to
        :data:`DESCRIPTION_MAX`), both applied at display time.
        """
        description = html_to_text(props.get("description"))

        return {
            "id": props.get("id", ""),
            "title": props.get("title", ""),
            "description": description,
            "type": props.get("type", ""),
            "url": props.get("url", ""),
            "access": props.get("access", ""),
            "owner": props.get("owner", ""),
            "created": ArcGISPlugin._epoch_ms_to_iso(props.get("created")),
            "modified": ArcGISPlugin._epoch_ms_to_iso(props.get("modified")),
            "tags": props.get("tags", []),
            "extent": props.get("extent", []),
            "recordCount": props.get("recordCount", props.get("numRecords")),
        }

    def _format_search_results(
        self, datasets: list[dict[str, Any]], *, total: int | None = None
    ) -> str:
        """Format Hub search hits.

        Args:
            datasets: Summaries from :meth:`_extract_dataset_summary`.
            total: Catalog-wide match count (``numberMatched``), if known.
        """
        if not datasets:
            return "No datasets found."

        lines = [self.format_search_header(total, len(datasets)), ""]

        for i, ds in enumerate(datasets, 1):
            tags = join_cleaned(ds.get("tags", [])) if ds.get("tags") else "None"
            lines.append(
                f"{i}. {self.portal_line(ds.get('title'), default='Untitled')}"
            )
            lines.append(f"   ID: {self.safe_id(ds.get('id'))}")
            lines.append(
                f"   Type: {self.portal_line(ds.get('type'), default='unknown')}"
            )
            lines.append(
                f"   Access: {self.portal_line(ds.get('access'), default='unknown')}"
            )
            facts = []
            owner = self.portal_line(ds.get("owner"))
            if owner:
                facts.append(f"Owner: {owner}")
            created = self.short_date(ds.get("created"))
            modified = self.short_date(ds.get("modified"))
            if created:
                facts.append(f"Created: {created}")
            if modified:
                facts.append(f"Modified: {modified}")
            records = ds.get("recordCount")
            if isinstance(records, int):
                facts.append(f"Records: {records}")
            if facts:
                lines.append("   " + " | ".join(facts))
            lines.append(
                f"   Description: {self.portal_line(ds.get('description'), max_len=300, default='No description')}"
            )
            lines.append(f"   URL: {self._display_url(ds.get('url'))}")
            lines.append(f"   Tags: {tags}")
            lines.append("")

        return "\n".join(lines)

    def _format_dataset(self, dataset: dict[str, Any]) -> str:
        """Format Hub item metadata; empty values are omitted."""
        line = self.portal_line
        lines = [
            f"Dataset: {line(dataset.get('title'), default='Untitled')}",
            f"ID: {self.safe_id(dataset.get('id'))}",
            f"Type: {line(dataset.get('type'), default='unknown')}",
            f"Access: {line(dataset.get('access'), default='unknown')}",
        ]

        def add(label: str, value: str) -> None:
            if value:
                lines.append(f"{label}: {value}")

        owner = line(dataset.get("owner"))
        org = line(dataset.get("orgName"))
        if owner or org:
            lines.append(
                "Owner: " + (f"{owner} ({org})" if owner and org else owner or org)
            )
        dates = []
        for label, key in (
            ("Created", "created"),
            ("Modified", "modified"),
            ("Last edit", "lastEditDate"),
            ("Data last edited", "dataLastEditDate"),
            ("Schema last edited", "schemaLastEditDate"),
        ):
            value = self.short_date(dataset.get(key))
            if value:
                dates.append(f"{label}: {value}")
        if dates:
            lines.append(" | ".join(dates))
        facts = []
        records = dataset.get("numRecords")
        if records is None:
            records = dataset.get("recordCount")
        if records is not None:
            facts.append(f"Records: {line(records)}")
        size = self.human_size(dataset.get("size"))
        if size:
            facts.append(f"Size: {size}")
        if facts:
            lines.append(" | ".join(facts))
        add("License", self.portal_block(dataset.get("licenseInfo"), max_len=2000))
        add(
            "Access information",
            self.portal_block(dataset.get("accessInformation"), max_len=2000),
        )
        add("Snippet", line(dataset.get("snippet")))
        description = self.portal_block(
            dataset.get("description"),
            max_len=DESCRIPTION_MAX,
            default="No description",
        )
        lines.append(f"Description: {description}")
        add("Spatial Reference", line(dataset.get("spatialReference")))
        add("Geometry Type", line(dataset.get("geometryType")))
        add("Tags", join_cleaned(dataset.get("tags") or []))
        add("Categories", join_cleaned(dataset.get("categories") or []))
        add("Type keywords", join_cleaned(dataset.get("typeKeywords") or []))
        extent = dataset.get("extent")
        if extent:
            add("Extent", line(extent))
        additional = dataset.get("additionalResources")
        if additional:
            add("Additional Resources", line(additional, max_len=1000))
        add("URL", self._display_url(dataset.get("url")))
        add("Service URL", self._display_url(dataset.get("service_url")))
        layers = dataset.get("layers") or []
        if layers:
            lines.append(
                "Layers and tables (pass the id as layer to get_schema or query_data):"
            )
            for entry in layers:
                marker = " [default]" if entry.get("default") else ""
                lines.append(f"  {self._describe_layer(entry)}{marker}")
        return "\n".join(lines)

    def _display_url(self, url: Any) -> str:
        """Render a portal-supplied URL only if its host is trusted.

        Delegates to :meth:`BaseOpenDataPlugin.display_portal_url` with the
        same allow-list that gates *fetching* (portal host, ``*.arcgis.com``,
        ``trusted_service_hosts``), so an attacker cannot plant a link to an
        arbitrary host in a dataset record.
        """
        return self.display_portal_url(
            url, extra_hosts=("arcgis.com", *self.plugin_config.trusted_service_hosts)
        )

    def _format_schema(self, fields: list[dict[str, Any]]) -> str:
        """Format schema information for user display."""
        if not fields:
            return "No schema information available."

        lines = ["Schema fields:"]
        for field in fields:
            name = self.portal_line(field.get("name"), default="unknown")
            ftype = self.portal_line(field.get("type"), default="unknown")
            alias = self.portal_line(field.get("alias"))
            length = field.get("length")
            detail = f"{ftype}, length {length}" if isinstance(length, int) else ftype
            lines.append(f"  • {name} ({detail})")
            if alias and alias != name:
                lines.append(f"    Alias: {alias}")
            definition = self.portal_line(
                html_to_text(field.get("definition")), max_len=_MAX_DEFINITION
            )
            if definition and definition not in (name, alias):
                lines.append(f"    Definition: {definition}")
            units = self.portal_line(field.get("units"), max_len=80)
            if units:
                lines.append(f"    Units: {units}")
            domain = self._describe_domain(field.get("domain"))
            if domain:
                lines.append(f"    {domain}")

        return "\n".join(lines)

    def _describe_domain(self, domain: Any) -> str:
        """Render a coded-value or range domain as one line (or ``""``)."""
        if not isinstance(domain, dict):
            return ""
        if domain.get("type") == "range":
            low, high = (list(domain.get("range") or []) + [None, None])[:2]
            if low is None and high is None:
                return ""
            if high is None:
                return f"Range: ≥ {self.portal_line(low)}"
            if low is None:
                return f"Range: ≤ {self.portal_line(high)}"
            return f"Range: {self.portal_line(low)} to {self.portal_line(high)}"
        values = domain.get("codedValues")
        if not isinstance(values, list) or not values:
            return ""
        shown = []
        for item in values[:_MAX_DOMAIN_VALUES]:
            if not isinstance(item, dict):
                continue
            code = self.portal_line(item.get("code"), max_len=60)
            label = self.portal_line(item.get("name"), max_len=100)
            shown.append(f"{code} = {label}" if label and label != code else code)
        text = "Values: " + "; ".join(shown)
        if len(values) > _MAX_DOMAIN_VALUES:
            text += f"; … and {len(values) - _MAX_DOMAIN_VALUES} more"
        return text

    def _source(
        self,
        dataset: dict[str, Any],
        layer_url: str,
        metadata: dict[str, Any] | None,
    ) -> dict[str, str]:
        """Collect the citation facts for a query result's source block."""
        item_id = self.safe_id(dataset.get("id"))
        editing = (metadata or {}).get("editingInfo") or {}
        return {
            "title": self.portal_line(dataset.get("title")),
            "item_url": (
                f"{self.plugin_config.portal_url.rstrip('/')}/datasets/{item_id}"
                if dataset.get("id") and item_id == dataset.get("id")
                else ""
            ),
            # Validated by _validate_feature_url before it was queried.
            "layer_url": self.portal_line(layer_url, max_len=500),
            "data_edited": self.short_date(
                editing.get("dataLastEditDate") or editing.get("lastEditDate")
            ),
            "license": self.portal_line(dataset.get("licenseInfo"), max_len=200),
            "retrieved": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        }

    @staticmethod
    def _format_source(source: dict[str, str] | None) -> str:
        """Render the source block appended to query and aggregate results."""
        if not source:
            return ""
        lines = ["Source:"]
        title = source.get("title")
        item_url = source.get("item_url")
        if title or item_url:
            lines.append(
                "  Dataset: "
                + (f"{title} ({item_url})" if title and item_url else title or item_url)
            )
        for label, key in (
            ("Layer queried", "layer_url"),
            ("Data last edited", "data_edited"),
            ("License", "license"),
            ("Retrieved", "retrieved"),
        ):
            if source.get(key):
                lines.append(f"  {label}: {source[key]}")
        return "\n".join(lines)

    def _format_query_results(
        self, page: dict[str, Any], limit: int, *, fmt: str = "text"
    ) -> str:
        """Format a :meth:`_query_page` result with totals and paging hints.

        Args:
            page: ``{"records", "offset", "total", "exceeded"}``.
            limit: The requested ``limit``.
            fmt: Output format, one of :data:`ROW_FORMATS`.
        """
        records = page.get("records") or []
        offset = page.get("offset", 0)
        total = page.get("total")
        if not records:
            if total:
                return (
                    f"No records returned at offset {offset}; "
                    f"{total} record(s) match in total."
                )
            return "No records returned."

        window = f"(offset: {offset}, limit: {limit})"
        if total is not None:
            header = f"Returned {len(records)} of {total} matching record(s) {window}:"
        else:
            header = f"Returned {len(records)} record(s) {window}:"

        # ArcGIS records have no internal _id key to skip.
        text, shown = self.render_rows(
            records, fmt, header=header, skip_keys=frozenset()
        )
        next_offset = offset + shown
        if total is not None:
            more = next_offset < total
        else:
            more = shown < len(records) or bool(page.get("exceeded"))
        if more:
            text += (
                f"\n\nMore records match. Next page: offset={next_offset} "
                "with the same where, out_fields and order_by."
            )
        source = self._format_source(page.get("source"))
        return f"{text}\n\n{source}" if source else text

    def _format_aggregate(self, result: dict[str, Any], *, fmt: str = "text") -> str:
        """Format an :meth:`aggregate_features` result."""
        rows = result.get("rows") or []
        if not rows:
            return "No groups matched."
        total = result.get("total_groups", len(rows))
        header = (
            f"Aggregated {total} group(s):"
            if total == len(rows)
            else f"Aggregated {total} group(s); showing the first {len(rows)} (limit):"
        )
        text, _ = self.render_rows(rows, fmt, header=header, skip_keys=frozenset())
        if result.get("exceeded"):
            text += (
                "\n\nThe service hit its transfer limit, so some groups are "
                "missing. Narrow where or group by fewer fields."
            )
        source = self._format_source(result.get("source"))
        return f"{text}\n\n{source}" if source else text

    def _format_aggregations(self, field: str, buckets: list[dict[str, Any]]) -> str:
        if not buckets:
            return f"No aggregation results for '{field}'."

        lines = [f"Aggregations for '{field}':\n"]
        for bucket in buckets:
            lines.append(
                f"  {self.portal_line(bucket.get('key'), default='unknown')}: "
                f"{self.portal_line(bucket.get('doc_count', bucket.get('count', 0)))} dataset(s)"
            )

        return "\n".join(lines)

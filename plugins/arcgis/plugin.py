"""ArcGIS Hub plugin implementation for OpenContext.

This plugin provides access to ArcGIS Hub open data catalogs
via the OGC API - Records (Hub Search API) and ArcGIS Feature Services.
"""

import logging
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from core.base_plugin import HTTP_RETRY, BaseOpenDataPlugin, ToolHandler
from core.interfaces import PluginType, ToolDefinition, ToolResult
from core.portal_content import join_cleaned
from plugins.arcgis.config_schema import ArcGISPluginConfig
from plugins.arcgis.where_validator import WhereValidator

logger = logging.getLogger(__name__)


class ArcGISPlugin(BaseOpenDataPlugin):
    """Plugin for accessing ArcGIS Hub open data catalogs.

    This plugin implements the DataPlugin interface on top of
    :class:`BaseOpenDataPlugin` and provides tools for searching datasets,
    retrieving dataset metadata, querying Feature Services, and exploring
    catalog aggregations.
    """

    plugin_name = "arcgis"
    plugin_type = PluginType.OPEN_DATA
    plugin_version = "1.0.0"

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
            self.hub_client = self._create_http_client(
                base_url=self.plugin_config.portal_url,
                headers=headers,
                timeout=self.plugin_config.timeout,
            )

            self.feature_client = self._create_http_client(
                headers=feature_headers,
                timeout=self.plugin_config.timeout,
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
                    f"directly usable in query_data where/out_fields. Provide the Hub "
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
                    "dataset has a queryable service URL."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "dataset_id": {
                            "type": "string",
                            "description": "Hub item ID (same as get_dataset)",
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
                    },
                    "required": ["dataset_id"],
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
                    "fields, then query_data to query features."
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
        schema = await self.get_schema(arguments["dataset_id"])
        return ToolResult(
            content=[{"type": "text", "text": self._format_schema(schema)}],
            success=True,
        )

    async def _tool_query_data(self, arguments: dict[str, Any]) -> ToolResult:
        dataset_id = arguments["dataset_id"]
        where = arguments.get("where", "1=1")
        out_fields = arguments.get("out_fields", "*")
        limit = arguments.get("limit", 100)
        records = await self._query_features(dataset_id, where, out_fields, limit)
        return ToolResult(
            content=[
                {"type": "text", "text": self._format_query_results(records, limit)}
            ],
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
                "snippet": props.get("snippet", ""),
                "licenseInfo": props.get("licenseInfo", ""),
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
                "accessInformation": props.get("accessInformation", ""),
            }
        )
        return result

    async def get_schema(self, dataset_id: str) -> list[dict[str, Any]]:
        """Get field schema for a dataset's Feature Service layer.

        Resolves the Feature Service URL via :meth:`get_dataset` (two-hop),
        then fetches the layer metadata (``{service_url}/0?f=json``) and
        returns the ``fields`` list (name, type, alias).

        Args:
            dataset_id: Hub item ID

        Returns:
            List of field definition dictionaries (name, type, alias)

        Raises:
            ValueError: If the dataset has no queryable service URL.
        """
        dataset = await self.get_dataset(dataset_id)
        service_url = dataset.get("service_url")
        if not service_url:
            raise ValueError(
                f"Dataset {dataset_id} does not have a queryable Feature Service URL"
            )

        service_url = self._validate_feature_url(
            service_url,
            self.plugin_config.portal_url,
            self.plugin_config.trusted_service_hosts,
        )
        service_url = self._ensure_layer_url(service_url)
        meta_url = f"{service_url}?f=json"

        response = await self._call_feature_service(meta_url, {})

        data = response.json()
        fields = data.get("fields", [])
        return [
            {
                "name": f.get("name", ""),
                "type": f.get("type", ""),
                "alias": f.get("alias", ""),
            }
            for f in fields
        ]

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
        if limit < 1:
            raise ValueError(f"limit must be at least 1 (got {limit})")

        dataset = await self.get_dataset(dataset_id)
        service_url = dataset.get("service_url")
        ds_type = dataset.get("type", "")
        if not service_url:
            raise ValueError(
                f"Dataset {dataset_id} does not have a queryable Feature Service URL"
            )

        if ds_type and ds_type not in self.QUERYABLE_TYPES:
            raise ValueError(
                f"Dataset type '{ds_type}' is not queryable. "
                f"query_data only supports: {', '.join(sorted(self.QUERYABLE_TYPES))}."
            )

        where_clause = WhereValidator.validate(where)
        service_url = self._validate_feature_url(
            service_url,
            self.plugin_config.portal_url,
            self.plugin_config.trusted_service_hosts,
        )
        service_url = self._ensure_layer_url(service_url)
        query_url = f"{service_url}/query"
        record_count = min(limit, 1000)
        params = {
            "where": where_clause,
            "outFields": out_fields,
            "resultRecordCount": record_count,
            "f": "json",
            "returnGeometry": "false",
        }

        response = await self._call_feature_service(query_url, params)

        try:
            data = response.json()
        except Exception as json_err:
            content_type = response.headers.get("content-type", "")
            raise ValueError(
                f"Feature Service returned non-JSON response "
                f"(content-type: {content_type}). The dataset URL may not "
                f"point to a queryable ArcGIS Feature Service."
            ) from json_err

        error_in_body = data.get("error")
        if error_in_body:
            code = error_in_body.get("code", "unknown")
            msg = error_in_body.get("message", "Unknown error")
            details = error_in_body.get("details", [])
            detail_str = "; ".join(details) if details else ""
            raise RuntimeError(
                f"Feature Service query failed (code {code}): {msg}"
                + (f" — {detail_str}" if detail_str else "")
            )

        features = data.get("features", [])
        if not features:
            return []

        return [f.get("attributes", {}) for f in features]

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
    ) -> str:
        """Restrict Feature Service URLs to trusted hosts.

        Parses ``service_url`` and requires the scheme to be http/https and
        the host to end with ``.arcgis.com``, equal the configured portal
        host, or match one of ``trusted_hosts`` (exact host or subdomain,
        case-insensitive). This prevents a crafted dataset record from
        steering Feature Service queries to arbitrary hosts (SSRF). Ported
        from thealphacubicle/OpenContext (Feature/security update #37).

        Hub catalogs commonly reference services self-hosted on city
        domains; those hosts must be listed in the plugin's
        ``trusted_service_hosts`` config to be queryable.

        Args:
            service_url: Feature Service URL resolved from a dataset record.
            portal_url: Configured portal URL (its host is the allow-listed
                fallback for self-hosted ArcGIS portals).
            trusted_hosts: Extra hostnames from ``trusted_service_hosts``
                config.

        Returns:
            The validated ``service_url`` unchanged.

        Raises:
            ValueError: If the scheme is not http/https or the host is not
                trusted.
        """
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
        raise ValueError(
            f"Feature Service URL host {host!r} is not trusted "
            f"(must end with '.arcgis.com', match portal host {portal_host!r}, "
            f"or be listed in trusted_service_hosts)"
        )

    @staticmethod
    def _ensure_layer_url(service_url: str) -> str:
        """Append /0 if the URL points at a FeatureServer or MapServer root
        without a layer index (e.g. .../FeatureServer -> .../FeatureServer/0).
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
        description = props.get("description", "") or ""
        if len(description) > 300:
            description = description[:300] + "..."

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
        add("License", self.portal_block(dataset.get("licenseInfo"), max_len=1000))
        add("Access information", line(dataset.get("accessInformation")))
        add("Snippet", line(dataset.get("snippet")))
        lines.append(
            f"Description: {self.portal_block(dataset.get('description'), default='No description')}"
        )
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
            lines.append(f"  • {name} ({ftype})")
            if alias and alias != name:
                lines.append(f"    Alias: {alias}")

        return "\n".join(lines)

    def _format_query_results(self, records: list[dict[str, Any]], limit: int) -> str:
        if not records:
            return "No records returned."

        # ArcGIS records have no internal _id key to skip.
        return self.format_records(
            records,
            header=f"Returned {len(records)} record(s) (limit: {limit}):",
            skip_keys=frozenset(),
        )

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

"""Socrata plugin implementation for OpenContext.

This plugin provides access to Socrata-based open data portals (e.g., Chicago,
NYC, Seattle) using the Discovery API for catalog search and SODA3 for data access.
"""

import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from core.base_plugin import BaseOpenDataPlugin, HTTP_RETRY, ToolHandler
from core.interfaces import PluginType, ToolDefinition, ToolResult
from core.portal_content import join_cleaned
from plugins.socrata.config_schema import SocrataPluginConfig
from plugins.socrata.soql_validator import SoQLValidator

logger = logging.getLogger(__name__)

DISCOVERY_API_BASE = "https://api.us.socrata.com"


class SocrataPlugin(BaseOpenDataPlugin):
    """Plugin for accessing Socrata-based open data portals.

    Uses two HTTP clients: Discovery API (catalog search) and SODA3 (data access).
    """

    plugin_name = "socrata"
    plugin_type = PluginType.OPEN_DATA
    plugin_version = "1.0.0"

    config_class = SocrataPluginConfig
    # Socrata dataset IDs are "four-by-four" identifiers (e.g. abcd-1234).
    id_pattern = re.compile(r"^[a-z0-9]{4}-[a-z0-9]{4}$")
    provider_label = "open data portal (Socrata)"

    def _get_domain(self) -> str:
        """Extract hostname from base_url for Discovery API domains parameter."""
        parsed = urlparse(self.plugin_config.base_url)
        return parsed.netloc or parsed.path or ""

    async def initialize(self) -> bool:
        """Initialize Socrata plugin and test connection.

        Returns:
            True if initialization succeeded
        """
        try:
            headers = {"X-App-Token": self.plugin_config.app_token}

            self.discovery_client = self._create_http_client(
                base_url=DISCOVERY_API_BASE,
                headers=headers,
                timeout=self.plugin_config.timeout,
            )

            self.soda_client = self._create_http_client(
                base_url=self.plugin_config.portal_url,
                headers=headers,
                timeout=self.plugin_config.timeout,
            )

            # Test connectivity via health check
            if await self.health_check():
                self._initialized = True
                logger.info(
                    f"Socrata plugin initialized successfully for {self.plugin_config.city_name}"
                )
                return True
            else:
                logger.error("Socrata API connection test failed")
                return False

        except Exception as e:
            logger.error(f"Failed to initialize Socrata plugin: {e}", exc_info=True)
            return False

    @HTTP_RETRY
    async def _call_discovery_api(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Call Socrata Discovery API.

        Args:
            params: Query parameters (domains is always included)

        Returns:
            Discovery API response

        Raises:
            RuntimeError: On HTTP errors
        """
        if not self.discovery_client:
            raise RuntimeError("Plugin not initialized")

        domain = self._get_domain()
        params = {**params, "domains": domain, "search_context": domain}

        try:
            response = await self.discovery_client.get("/api/catalog/v1", params=params)
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            self._raise_http_error(e, " Discovery API")

        return response.json()

    @HTTP_RETRY
    async def _call_soda_api(
        self, method: str, path: str, **kwargs: Any
    ) -> Dict[str, Any]:
        """Call SODA3 API on portal domain.

        Args:
            method: HTTP method (GET or POST)
            path: API path (e.g., /api/views/{id}.json)
            **kwargs: Additional request arguments

        Returns:
            JSON response

        Raises:
            RuntimeError: On HTTP errors
        """
        if not self.soda_client:
            raise RuntimeError("Plugin not initialized")

        try:
            if method.upper() == "GET":
                response = await self.soda_client.get(path, **kwargs)
            else:
                response = await self.soda_client.post(path, **kwargs)
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            self._raise_http_error(e)

        return response.json()

    def get_tools(self) -> List[ToolDefinition]:
        """Get list of tools provided by Socrata plugin.

        Returns:
            List of tool definitions
        """
        return [
            ToolDefinition(
                name="search_datasets",
                description=(
                    f"Search for datasets in {self.plugin_config.city_name}'s open data portal. "
                    f"Returns dataset IDs needed for get_dataset, get_schema, and query_dataset. "
                    f"Limit is optional (default: 10)."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query string",
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
                    f"Get metadata for a specific dataset from {self.plugin_config.city_name}'s open data portal "
                    f"(name, description, row count, etc.). Returns metadata only—no column info. "
                    f"For column names and types, use get_schema instead."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "dataset_id": {
                            "type": "string",
                            "description": "Dataset 4x4 ID (e.g., wc4w-4jew)",
                        },
                    },
                    "required": ["dataset_id"],
                },
            ),
            ToolDefinition(
                name="get_schema",
                description=(
                    f"Get column schema for a dataset in {self.plugin_config.city_name}'s open data portal. "
                    f"Returns column field names (not display names), data types, and descriptions—all directly usable in SoQL. "
                    f"Call before query_dataset to construct valid SoQL. "
                    f"Note: schemas may include computed region columns (:@computed_region_...) at the end; these are noisy and generally not useful for queries."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "dataset_id": {
                            "type": "string",
                            "description": "Dataset 4x4 ID",
                        },
                    },
                    "required": ["dataset_id"],
                },
            ),
            ToolDefinition(
                name="query_dataset",
                description=(
                    f"Query data from a dataset in {self.plugin_config.city_name}'s open data portal using SoQL. "
                    f"Use get_schema first to get column names. "
                    f"SoQL gotchas: GROUP BY is required whenever using COUNT() or any aggregation; "
                    f"LIMIT caps returned rows (can affect aggregation results); "
                    f"boolean fields use = true / = false, not = 'Y' or = 1; "
                    f"for conditional counts use SUM(CASE WHEN col = true THEN 1 ELSE 0 END)."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "dataset_id": {
                            "type": "string",
                            "description": "Dataset 4x4 ID",
                        },
                        "soql_query": {
                            "type": "string",
                            "description": (
                                "SoQL query. Examples: "
                                "Simple filter: SELECT * WHERE year > 2020 LIMIT 50; "
                                "Aggregation (GROUP BY required): SELECT category, COUNT(*) GROUP BY category; "
                                "Multi-column: SELECT region, SUM(amount) GROUP BY region; "
                                "Conditional aggregation: SELECT type, SUM(CASE WHEN arrest = true THEN 1 ELSE 0 END) GROUP BY type"
                            ),
                        },
                    },
                    "required": ["dataset_id", "soql_query"],
                },
            ),
            ToolDefinition(
                name="list_categories",
                description=(
                    f"Typical workflow: list_categories → search_datasets → get_dataset → get_schema → query_dataset. "
                    f"List all dataset categories on {self.plugin_config.city_name}'s open data portal. "
                    f"Categories include dataset counts per category. Use results to inform which search terms to pass to search_datasets."
                ),
                input_schema={"type": "object", "properties": {}},
            ),
            ToolDefinition(
                name="execute_sql",
                description="""Execute raw SoQL query (advanced). Similar to CKAN execute_sql.
Use for complex SoQL queries. Security: Only SELECT allowed.
Use get_schema first for column names.""",
                input_schema={
                    "type": "object",
                    "properties": {
                        "dataset_id": {
                            "type": "string",
                            "description": "Dataset 4x4 ID",
                        },
                        "soql": {
                            "type": "string",
                            "description": "SoQL SELECT statement",
                        },
                    },
                    "required": ["dataset_id", "soql"],
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
                    "to get more details."
                ),
            ),
            "get_dataset": ToolHandler(
                handler=self._tool_get_dataset,
                required_args=("dataset_id",),
                guidance=(
                    "Use the get_schema tool with this dataset's ID to get "
                    "column info, then query_dataset to query data."
                ),
            ),
            "get_schema": ToolHandler(
                handler=self._tool_get_schema, required_args=("dataset_id",)
            ),
            "query_dataset": ToolHandler(
                handler=self._tool_query_dataset,
                required_args=("dataset_id", "soql_query"),
            ),
            "list_categories": ToolHandler(handler=self._tool_list_categories),
            "execute_sql": ToolHandler(
                handler=self._tool_execute_sql, required_args=("dataset_id", "soql")
            ),
        }

    async def _tool_search_datasets(self, arguments: Dict[str, Any]) -> ToolResult:
        query = arguments.get("query", "")
        limit = arguments.get("limit", 10)
        result = await self._discovery_search({"q": query, "limit": limit})
        datasets = result.get("results", []) or []
        total = result.get("resultSetSize")
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

    async def _tool_get_dataset(self, arguments: Dict[str, Any]) -> ToolResult:
        dataset = await self.get_dataset(arguments["dataset_id"])
        return ToolResult(
            content=[{"type": "text", "text": self._format_dataset(dataset)}],
            success=True,
        )

    async def _tool_get_schema(self, arguments: Dict[str, Any]) -> ToolResult:
        schema = await self.get_schema(arguments["dataset_id"])
        return ToolResult(
            content=[{"type": "text", "text": self._format_schema(schema)}],
            success=True,
        )

    async def _tool_query_dataset(self, arguments: Dict[str, Any]) -> ToolResult:
        data = await self._query_dataset(
            arguments["dataset_id"], arguments["soql_query"]
        )
        display_limit = self._parse_soql_limit(arguments["soql_query"], default=100)
        return ToolResult(
            content=[
                {
                    "type": "text",
                    "text": self._format_query_results(data, limit=display_limit),
                }
            ],
            success=True,
        )

    async def _tool_list_categories(self, arguments: Dict[str, Any]) -> ToolResult:
        categories = await self._list_categories()
        return ToolResult(
            content=[{"type": "text", "text": self._format_categories(categories)}],
            success=True,
        )

    async def _tool_execute_sql(self, arguments: Dict[str, Any]) -> ToolResult:
        result = await self.execute_sql(arguments["dataset_id"], arguments["soql"])
        if result.get("error"):
            return ToolResult(
                content=[],
                success=False,
                error_message=result.get("message", "SoQL execution failed"),
            )
        records = result.get("records", [])
        fields = result.get("fields", [])
        formatted_text = self._format_sql_results(records, fields)
        return ToolResult(
            content=[{"type": "text", "text": formatted_text}],
            success=True,
        )

    async def search_datasets(
        self, query: str, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Search for datasets matching a query.

        Args:
            query: Search query string
            limit: Maximum number of results

        Returns:
            List of dataset metadata dictionaries
        """
        result = await self._discovery_search({"q": query, "limit": limit})
        return result.get("results", []) or []

    async def _discovery_search(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Call the Discovery API and return the full envelope.

        The envelope carries ``resultSetSize`` (catalog-wide hit count) in
        addition to ``results``.
        """
        return await self._call_discovery_api(params) or {}

    async def get_dataset(self, dataset_id: str) -> Dict[str, Any]:
        """Get detailed metadata for a specific dataset.

        Args:
            dataset_id: Dataset 4x4 ID

        Returns:
            Dataset metadata dictionary
        """
        return await self._call_soda_api("GET", f"/api/views/{dataset_id}.json")

    async def get_schema(self, dataset_id: str) -> List[Dict[str, Any]]:
        """Get column schema for a dataset.

        Args:
            dataset_id: Dataset 4x4 ID

        Returns:
            List of column definitions
        """
        metadata = await self._call_soda_api("GET", f"/api/views/{dataset_id}.json")
        return metadata.get("columns", [])

    def _parse_soql_limit(
        self, soql_query: str, default: int = 100, max_val: Optional[int] = None
    ) -> int:
        """Parse LIMIT value from SoQL query."""
        if "LIMIT" not in soql_query.upper():
            return default
        parts = soql_query.upper().split("LIMIT")
        if len(parts) < 2:
            return default
        try:
            val = int(parts[-1].strip().split()[0])
            if max_val is not None:
                val = min(val, max_val)
            return max(1, val)
        except (ValueError, IndexError):
            return default

    async def _query_dataset(
        self, dataset_id: str, soql_query: str
    ) -> List[Dict[str, Any]]:
        """Query data using SoQL.

        Args:
            dataset_id: Dataset 4x4 ID
            soql_query: SoQL query string

        Returns:
            List of row objects
        """
        page_size = self._parse_soql_limit(soql_query, default=100, max_val=50000)

        body = {
            "query": soql_query,
            "page": {"pageNumber": 1, "pageSize": page_size},
        }
        result = await self._call_soda_api(
            "POST",
            f"/api/v3/views/{dataset_id}/query.json",
            json=body,
        )
        if isinstance(result, list):
            return result
        rows = result.get("rows", result.get("results", []))
        return rows if isinstance(rows, list) else []

    async def execute_sql(self, dataset_id: str, soql: str) -> Dict[str, Any]:
        """Execute raw SoQL query with security validation.

        Args:
            dataset_id: Dataset 4x4 ID
            soql: SoQL SELECT statement

        Returns:
            Dictionary with success flag, records, fields, or error message
        """
        is_valid, error = SoQLValidator.validate_query(soql)
        if not is_valid:
            return {"error": True, "message": error}

        logger.info("Executing SoQL", extra={"soql": soql[:500]})

        try:
            records = await self._query_dataset(dataset_id, soql)
            fields: List[Dict[str, Any]] = []
            if records:
                for key in records[0].keys():
                    if key != "_id":
                        fields.append({"id": key})

            return {
                "success": True,
                "records": records,
                "fields": fields,
            }
        except Exception as e:
            logger.error(f"SoQL execution failed: {e}", exc_info=True)
            return {"error": True, "message": str(e)}

    async def _list_categories(self) -> List[Dict[str, Any]]:
        """List categories with dataset counts.

        The Socrata Discovery API's facets parameter does not return facets for
        many portals (e.g., Chicago). Fall back to deriving categories from
        domain_category in each result's classification.
        """
        response = await self._call_discovery_api({"facets": "categories"})
        facets = response.get("facets", {})
        categories = facets.get("categories", [])
        if isinstance(categories, list) and categories:
            return categories
        if isinstance(categories, dict) and categories:
            return list(categories.items())

        # Fallback: derive from domain_category in search results
        # (facets are often empty for domain-scoped catalog requests)
        category_counts: Dict[str, int] = {}
        offset = 0
        limit = 500
        max_pages = 20
        page_count = 0
        while True:
            page_count += 1
            if page_count > max_pages:
                logger.warning(
                    f"Category pagination exceeded {max_pages} pages ({max_pages * limit} results); stopping early."
                )
                break
            page = await self._call_discovery_api({"limit": limit, "offset": offset})
            results = page.get("results", [])
            if not results:
                break
            for item in results:
                classification = item.get("classification", {})
                domain_cat = classification.get("domain_category")
                if domain_cat and isinstance(domain_cat, str):
                    category_counts[domain_cat] = category_counts.get(domain_cat, 0) + 1
            if len(results) < limit:
                break
            offset += limit

        return [
            {"name": name, "count": count}
            for name, count in sorted(category_counts.items())
        ]

    async def query_data(
        self,
        resource_id: str,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Query data from a dataset (DataPlugin contract).

        Args:
            resource_id: Dataset 4x4 ID
            filters: Optional filters (field: value pairs) compiled to SoQL WHERE
            limit: Maximum number of records

        Returns:
            List of data records
        """
        where_clause = self.build_where_clause(filters) if filters else ""
        soql = f"SELECT * LIMIT {limit}"
        if where_clause:
            soql = f"SELECT * WHERE {where_clause} LIMIT {limit}"
        return await self._query_dataset(resource_id, soql)

    async def health_check(self) -> bool:
        """Check if Socrata API is accessible.

        Returns:
            True if healthy
        """
        try:
            await self._call_discovery_api({"limit": 1})
            return True
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False

    def _format_search_results(
        self, datasets: List[Dict[str, Any]], *, total: Optional[int] = None
    ) -> str:
        """Format search results for user display.

        Args:
            datasets: Discovery API hits.
            total: Catalog-wide hit count (``resultSetSize``), if known.
        """
        if not datasets:
            return f"No datasets found in {self.plugin_config.city_name}'s open data portal."

        lines = [self.format_search_header(total, len(datasets)), ""]

        for i, item in enumerate(datasets, 1):
            resource = item.get("resource", item)
            classification = item.get("classification") or {}
            name = self.portal_line(resource.get("name"), default="Untitled")
            dataset_id = self.safe_id(resource.get("id"))
            description = self.portal_line(
                resource.get("description"), max_len=100, default="No description"
            )
            category = self.portal_line(
                resource.get("category") or classification.get("domain_category")
            )
            modified = self.short_date(resource.get("updatedAt"))
            columns = resource.get("columns_name")
            downloads = resource.get("download_count")
            attribution = self.portal_line(resource.get("attribution"))

            lines.append(f"{i}. {name}")
            lines.append(f"   ID: {dataset_id}")
            facts = []
            if modified:
                facts.append(f"Modified: {modified}")
            if isinstance(columns, list):
                facts.append(f"Columns: {len(columns)}")
            if isinstance(downloads, int):
                facts.append(f"Downloads: {downloads}")
            if attribution:
                facts.append(f"Source: {attribution}")
            if facts:
                lines.append("   " + " | ".join(facts))
            lines.append(f"   Description: {description}")
            if category:
                lines.append(f"   Category: {category}")
            if dataset_id != "unknown":
                # Build the link from config + validated ID rather than echoing
                # the portal-supplied permalink.
                lines.append(
                    f"   Portal: {self.plugin_config.portal_url}/d/{dataset_id}"
                )
            lines.append("")

        return "\n".join(lines)

    def _format_dataset(self, dataset: Dict[str, Any]) -> str:
        """Format dataset metadata for user display."""
        name = self.portal_line(dataset.get("name"), default="Untitled")
        dataset_id = self.safe_id(dataset.get("id", dataset.get("viewId")))
        description = self.portal_block(
            dataset.get("description"), default="No description"
        )
        row_count = self.portal_line(dataset.get("rowCount"))
        rows_updated = self.short_date(
            dataset.get("rowsUpdatedAt", dataset.get("metadata_updated_at"))
        )
        created = self.short_date(dataset.get("createdAt"))
        published = self.short_date(dataset.get("publicationDate"))
        view_modified = self.short_date(dataset.get("viewLastModified"))
        tags = dataset.get("tags", [])
        category = self.portal_line(dataset.get("category"))
        license_info = dataset.get("license") or {}
        if isinstance(license_info, dict):
            license_name = self.portal_line(license_info.get("name"))
            license_link = self.display_portal_url(license_info.get("termsLink"))
        else:
            license_name = self.portal_line(license_info)
            license_link = ""
        license_id = self.portal_line(dataset.get("licenseId"), max_len=100)
        attribution = self.portal_line(dataset.get("attribution"))
        attribution_link = self.display_portal_url(dataset.get("attributionLink"))
        columns = dataset.get("columns")
        downloads = dataset.get("downloadCount")
        views = dataset.get("viewCount")
        provenance = self.portal_line(dataset.get("provenance"), max_len=40)

        lines = [f"Dataset: {name}", f"ID: {dataset_id}"]
        if attribution:
            if attribution_link.startswith("("):
                # Untrusted host renders as "(external: host)" already.
                lines.append(f"Source: {attribution} {attribution_link}")
            elif attribution_link:
                lines.append(f"Source: {attribution} ({attribution_link})")
            else:
                lines.append(f"Source: {attribution}")
        if license_name or license_id:
            license_line = f"License: {license_name or license_id}"
            if license_id and license_name:
                license_line += f" ({license_id})"
            if license_link:
                license_line += f" — {license_link}"
            lines.append(license_line)
        dates = []
        if created:
            dates.append(f"Created: {created}")
        if published:
            dates.append(f"Published: {published}")
        if view_modified:
            dates.append(f"Metadata modified: {view_modified}")
        if rows_updated:
            dates.append(f"Rows updated: {rows_updated}")
        if dates:
            lines.append(" | ".join(dates))
        facts = []
        if row_count:
            facts.append(f"Rows: {row_count}")
        if isinstance(columns, list):
            facts.append(f"Columns: {len(columns)}")
        if isinstance(downloads, int):
            facts.append(f"Downloads: {downloads}")
        if isinstance(views, int):
            facts.append(f"Views: {views}")
        if provenance:
            facts.append(f"Provenance: {provenance}")
        if facts:
            lines.append(" | ".join(facts))
        if category:
            lines.append(f"Category: {category}")
        if tags:
            tag_text = (
                join_cleaned(tags) if isinstance(tags, list) else self.portal_line(tags)
            )
            lines.append(f"Tags: {tag_text}")
        lines.append(f"Description: {description}")
        if dataset_id != "unknown":
            lines.append("")
            lines.append(f"Portal URL: {self.plugin_config.portal_url}/d/{dataset_id}")

        return "\n".join(lines)

    def _format_schema(self, columns: List[Dict[str, Any]]) -> str:
        """Format schema information for user display."""
        if not columns:
            return "No schema information available."

        lines = ["Schema fields (use these for SoQL queries):"]
        for col in columns:
            field_name = self.portal_line(
                col.get("fieldName", col.get("id", col.get("name"))), default="unknown"
            )
            display_name = self.portal_line(col.get("name", col.get("displayName")))
            data_type = self.portal_line(
                col.get("dataTypeName", col.get("type")), default="unknown"
            )
            description = self.portal_line(col.get("description"))

            lines.append(f"  • {field_name} ({data_type})")
            if display_name and display_name != field_name:
                lines.append(f"    Display: {display_name}")
            if description:
                lines.append(f"    {description}")

        return "\n".join(lines)

    def _format_query_results(
        self, records: List[Dict[str, Any]], limit: int = 100
    ) -> str:
        """Format query results for user display."""
        if not records:
            return "No records found matching the query."

        return self.format_records(
            records,
            max_display=limit,
            header=f"Found {len(records)} record(s) (showing first {min(limit, len(records))}):",
        )

    def _format_sql_results(
        self, records: List[Dict[str, Any]], fields: List[Dict[str, Any]]
    ) -> str:
        """Format SQL/SoQL query results for user display.

        Args:
            records: List of record dictionaries
            fields: List of field metadata dictionaries

        Returns:
            Formatted string representation of results
        """
        if not records:
            return "No records found matching the SoQL query."

        header_lines = [f"SQL Query Results: {len(records)} record(s)"]
        if fields:
            field_names = [field.get("id", "unknown") for field in fields]
            header_lines.append(f"Fields: {join_cleaned(field_names)}")

        header = "\n".join(header_lines)
        return self.format_records(records, max_display=10, header=header)

    def _format_categories(self, categories: List[Any]) -> str:
        """Format categories for user display."""
        if not categories:
            return f"No categories found on {self.plugin_config.city_name}'s open data portal."

        lines = [f"Categories on {self.plugin_config.city_name}'s open data portal:\n"]

        for i, cat in enumerate(categories, 1):
            if isinstance(cat, dict):
                name = self.portal_line(cat.get("name", cat.get("label", str(cat))))
                count = self.portal_line(cat.get("count", cat.get("value", "")))
                lines.append(f"  {i}. {name}: {count} dataset(s)")
            else:
                lines.append(f"  {i}. {self.portal_line(cat)}")

        return "\n".join(lines)

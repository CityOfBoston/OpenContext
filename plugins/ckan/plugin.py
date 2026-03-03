"""CKAN plugin implementation for OpenContext.

This plugin provides access to CKAN-based open data portals.
"""

import logging
from typing import Any, Dict, List, Optional

import httpx
from tenacity import (
    retry,
    retry_if_not_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from core.interfaces import DataPlugin, PluginType, ToolDefinition, ToolResult
from plugins.ckan.config_schema import CKANPluginConfig
from plugins.ckan.sql_validator import SQLValidator

logger = logging.getLogger(__name__)


class CKANPlugin(DataPlugin):
    """Plugin for accessing CKAN-based open data portals.

    This plugin implements the DataPlugin interface and provides tools for
    searching datasets, retrieving dataset metadata, and querying data.
    """

    plugin_name = "ckan"
    plugin_type = PluginType.OPEN_DATA
    plugin_version = "1.0.0"

    def __init__(self, config: Dict[str, Any]) -> None:
        """Initialize CKAN plugin with configuration.

        Args:
            config: Plugin configuration dictionary
        """
        super().__init__(config)
        self.plugin_config = CKANPluginConfig(**config)
        self.client: Optional[httpx.AsyncClient] = None

    async def initialize(self) -> bool:
        """Initialize CKAN plugin and test connection.

        Returns:
            True if initialization succeeded
        """
        try:
            # Create HTTP client
            headers = {}
            if self.plugin_config.api_key:
                headers["Authorization"] = self.plugin_config.api_key

            self.client = httpx.AsyncClient(
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

    async def shutdown(self) -> None:
        """Shutdown plugin and close HTTP client."""
        if self.client:
            await self.client.aclose()
            self.client = None
        self._initialized = False
        logger.info("CKAN plugin shut down")

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

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_not_exception_type((RuntimeError, httpx.HTTPStatusError)),
    )
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
                description=f"Get detailed information about a specific dataset from {self.plugin_config.city_name}'s open data portal",
                input_schema={
                    "type": "object",
                    "properties": {
                        "dataset_id": {
                            "type": "string",
                            "description": "Dataset ID or name",
                        },
                    },
                    "required": ["dataset_id"],
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

    async def execute_tool(
        self, tool_name: str, arguments: Dict[str, Any]
    ) -> ToolResult:
        """Execute a tool by name.

        Args:
            tool_name: Name of the tool
            arguments: Tool arguments

        Returns:
            ToolResult with content and success flag
        """
        try:
            if tool_name == "search_datasets":
                query = arguments.get("query", "")
                limit = arguments.get("limit", 20)
                datasets = await self.search_datasets(query, limit)
                return ToolResult(
                    content=[
                        {
                            "type": "text",
                            "text": self._format_search_results(datasets),
                        }
                    ],
                    success=True,
                )

            elif tool_name == "get_dataset":
                dataset_id = arguments.get("dataset_id")
                if not dataset_id:
                    return ToolResult(
                        content=[],
                        success=False,
                        error_message="dataset_id is required",
                    )
                dataset = await self.get_dataset(dataset_id)
                return ToolResult(
                    content=[
                        {
                            "type": "text",
                            "text": self._format_dataset(dataset),
                        }
                    ],
                    success=True,
                )

            elif tool_name == "query_data":
                resource_id = arguments.get("resource_id")
                if not resource_id:
                    return ToolResult(
                        content=[],
                        success=False,
                        error_message="resource_id is required",
                    )
                filters = arguments.get("filters", {})
                limit = arguments.get("limit", 100)
                data = await self.query_data(resource_id, filters, limit)
                return ToolResult(
                    content=[
                        {
                            "type": "text",
                            "text": self._format_query_results(data, limit),
                        }
                    ],
                    success=True,
                )

            elif tool_name == "get_schema":
                resource_id = arguments.get("resource_id")
                if not resource_id:
                    return ToolResult(
                        content=[],
                        success=False,
                        error_message="resource_id is required",
                    )
                schema = await self.get_schema(resource_id)
                return ToolResult(
                    content=[
                        {
                            "type": "text",
                            "text": self._format_schema(schema),
                        }
                    ],
                    success=True,
                )

            elif tool_name == "execute_sql":
                sql = arguments.get("sql")
                if not sql:
                    return ToolResult(
                        content=[],
                        success=False,
                        error_message="sql parameter is required",
                    )
                result = await self.execute_sql(sql)
                if result.get("error"):
                    return ToolResult(
                        content=[],
                        success=False,
                        error_message=result.get("message", "SQL execution failed"),
                    )
                # Format SQL results
                records = result.get("records", [])
                fields = result.get("fields", [])
                formatted_text = self._format_sql_results(records, fields)
                return ToolResult(
                    content=[{"type": "text", "text": formatted_text}],
                    success=True,
                )

            elif tool_name == "aggregate_data":
                resource_id = arguments.get("resource_id")
                if not resource_id:
                    return ToolResult(
                        content=[],
                        success=False,
                        error_message="resource_id parameter is required",
                    )
                metrics = arguments.get("metrics", {})
                if not metrics:
                    return ToolResult(
                        content=[],
                        success=False,
                        error_message="metrics parameter is required",
                    )
                result = await self.aggregate_data(
                    resource_id=resource_id,
                    group_by=arguments.get("group_by", []),
                    metrics=metrics,
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
                return ToolResult(
                    content=[{"type": "text", "text": formatted}], success=True
                )

            else:
                return ToolResult(
                    content=[],
                    success=False,
                    error_message=f"Unknown tool: {tool_name}",
                )

        except Exception as e:
            logger.error(f"Error executing tool {tool_name}: {e}", exc_info=True)
            return ToolResult(
                content=[],
                success=False,
                error_message=str(e) if str(e) else "Tool execution failed",
            )

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
        response = await self._call_ckan_api(
            "package_search", {"q": query, "rows": limit}
        )
        return response.get("result", {}).get("results", [])

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
            having: Optional HAVING clause filters (expression: value pairs)
            order_by: Optional field to order by
            limit: Maximum number of results

        Returns:
            Dictionary with success flag, records, fields, or error message
        """
        # SELECT
        select_fields = ", ".join(group_by) if group_by else ""
        select_metrics = ", ".join(
            [f"{expr} as {name}" for name, expr in metrics.items()]
        )
        select_clause = (
            f"{select_fields}, {select_metrics}" if select_fields else select_metrics
        )

        # WHERE
        where_clause = ""
        if filters:
            conditions = []
            for field, value in filters.items():
                if isinstance(value, str):
                    # Escape single quotes in SQL strings
                    escaped_value = value.replace("'", "''")
                    conditions.append(f"{field} = '{escaped_value}'")
                elif value is None:
                    conditions.append(f"{field} IS NULL")
                else:
                    conditions.append(f"{field} = {value}")
            where_clause = "WHERE " + " AND ".join(conditions)

        # GROUP BY
        group_clause = f"GROUP BY {', '.join(group_by)}" if group_by else ""

        # HAVING
        having_clause = ""
        if having:
            conditions = [f"{expr} > {value}" for expr, value in having.items()]
            having_clause = "HAVING " + " AND ".join(conditions)

        # ORDER BY
        order_clause = f"ORDER BY {order_by}" if order_by else ""

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

    def _format_search_results(self, datasets: List[Dict[str, Any]]) -> str:
        """Format search results for user display."""
        if not datasets:
            return f"No datasets found in {self.plugin_config.city_name}'s open data portal."

        lines = [
            f"Found {len(datasets)} dataset(s) in {self.plugin_config.city_name}'s open data portal:\n"
        ]

        for i, dataset in enumerate(datasets, 1):
            title = dataset.get("title", "Untitled")
            dataset_id = dataset.get("id", "unknown")
            notes = (
                dataset.get("notes", "")[:100] + "..."
                if dataset.get("notes")
                else "No description"
            )

            lines.append(f"{i}. {title}")
            lines.append(f"   ID: {dataset_id}")
            lines.append(f"   Description: {notes}")
            lines.append(
                f"   Portal: {self.plugin_config.portal_url}/dataset/{dataset_id}"
            )
            lines.append("")

        lines.append(
            f"View all datasets at: {self.plugin_config.portal_url}\n"
            f"Use get_dataset tool with a dataset ID to get more details."
        )

        return "\n".join(lines)

    def _format_dataset(self, dataset: Dict[str, Any]) -> str:
        """Format dataset metadata for user display."""
        title = dataset.get("title", "Untitled")
        dataset_id = dataset.get("id", "unknown")
        notes = dataset.get("notes", "No description")
        organization = dataset.get("organization", {}).get("title", "Unknown")
        resources = dataset.get("resources", [])

        lines = [
            f"Dataset: {title}",
            f"ID: {dataset_id}",
            f"Organization: {organization}",
            f"Description: {notes}",
            "",
            f"Portal URL: {self.plugin_config.portal_url}/dataset/{dataset_id}",
            "",
        ]

        if resources:
            lines.append(f"Resources ({len(resources)}):")
            for i, resource in enumerate(resources, 1):
                res_name = resource.get("name", "Unnamed")
                res_id = resource.get("id", "unknown")
                res_format = resource.get("format", "unknown")
                lines.append(f"  {i}. {res_name} ({res_format})")
                lines.append(f"     Resource ID: {res_id}")
                lines.append(
                    f"     Use query_data tool with resource_id='{res_id}' to query this data"
                )
        else:
            lines.append("No resources available for this dataset.")

        return "\n".join(lines)

    def _format_query_results(self, records: List[Dict[str, Any]], limit: int) -> str:
        """Format query results for user display."""
        if not records:
            return "No records found matching the query."

        lines = [f"Found {len(records)} record(s) (showing up to {limit}):\n"]

        # Show first few records as examples
        for i, record in enumerate(records[:5], 1):
            lines.append(f"Record {i}:")
            for key, value in record.items():
                if key != "_id":  # Skip internal ID
                    lines.append(f"  {key}: {value}")
            lines.append("")

        if len(records) > 5:
            lines.append(f"... and {len(records) - 5} more record(s)")

        return "\n".join(lines)

    def _format_schema(self, fields: List[Dict[str, Any]]) -> str:
        """Format schema information for user display."""
        if not fields:
            return "No schema information available."

        lines = ["Schema fields:"]
        for field in fields:
            field_id = field.get("id", "unknown")
            field_type = field.get("type", "unknown")
            field_info = field.get("info", {})
            description = field_info.get("label", "") if field_info else ""

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

        lines = [f"SQL Query Results: {len(records)} record(s)\n"]

        # Show field names if available
        if fields:
            field_names = [field.get("id", "unknown") for field in fields]
            lines.append(f"Fields: {', '.join(field_names)}\n")

        # Show first few records as examples
        for i, record in enumerate(records[:10], 1):
            lines.append(f"Record {i}:")
            for key, value in record.items():
                if key != "_id":  # Skip internal ID
                    lines.append(f"  {key}: {value}")
            lines.append("")

        if len(records) > 10:
            lines.append(f"... and {len(records) - 10} more record(s)")

        return "\n".join(lines)

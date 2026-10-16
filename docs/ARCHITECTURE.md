# OpenContext Architecture

## Overview

OpenContext is a plugin-based framework. Each deployment runs **one** server with **one** plugin. This keeps deployments simple, independently scalable, and easy to maintain.

## One Fork = One Server

**Enforcement:**
- `opencontext deploy` validates config before deployment
- `plugin_manager.py` fails at startup if multiple plugins are enabled

**Multiple servers:** Fork again per plugin, deploy each separately.

## Components

```
core/
├── interfaces.py       # Contracts: MCPPlugin, DataPlugin, ToolDefinition
├── base_plugin.py      # BaseOpenDataPlugin, ToolHandler, HTTP_RETRY
├── config_base.py      # BasePluginConfig (shared pydantic config + URL validation)
├── query_validator.py  # BaseQueryValidator (shared SQL/SoQL safety checks)
├── plugin_manager.py   # Discovery, loading, routing
├── mcp_server.py       # MCP JSON-RPC handler
├── validators.py       # Config validation
└── logging_utils.py   # Structured logging

server/
├── adapters/
│   ├── aws_lambda.py      # AWS Lambda entry point
│   └── gcp_functions.py   # GCP Cloud Functions gen2 entry point
└── http_handler.py        # HTTP request handling

plugins/                # Built-in plugins
├── ckan/               # CKAN open data portals
│   ├── plugin.py
│   ├── config_schema.py
│   └── sql_validator.py
├── arcgis/             # ArcGIS Hub portals
│   ├── plugin.py
│   ├── config_schema.py
│   └── where_validator.py
├── socrata/            # Socrata open data portals
│   ├── plugin.py
│   ├── config_schema.py
│   └── soql_validator.py
└── opendatasoft/       # Opendatasoft Explore v2.1 portals
    ├── plugin.py
    ├── config_schema.py
    └── odsql_validator.py

cli/                    # Typer CLI (opencontext command)
├── main.py             # Command registration
├── commands/           # One file per command group
└── utils.py            # Shared helpers

custom_plugins/         # User plugins (auto-discovered)
├── template/
│   └── plugin_template.py

examples/               # Example configs per city
├── boston/
├── chicago/
├── seattle/
└── ...

client/                 # Go stdio-to-HTTP client (optional)
tests/                  # Unit tests
```

### Request Flow

**AWS (`--cloud aws`):**

```
Claude / MCP Client
    → Claude Connectors (HTTPS) or Go stdio client
API Gateway (REST, Regional)
    → Lambda (server.adapters.aws_lambda.lambda_handler)
    → MCP Server (core/mcp_server.py)
    → Plugin Manager (core/plugin_manager.py)
    → Plugin (CKAN / ArcGIS / Socrata / Opendatasoft / custom)
    → External API

Logs & traces:
    Lambda → CloudWatch Logs (/aws/lambda/<function-name>)
    Lambda → X-Ray (active tracing)
    Failed async invocations → SQS Dead Letter Queue
```

**GCP (`--cloud gcp`):**

```
Claude / MCP Client
    → Claude Connectors (HTTPS)
HTTPS (Cloud Functions gen2 / Cloud Run)
    → server.adapters.gcp_functions.mcp_http
    → MCP Server → Plugin Manager → Plugin → External API

Logs: Cloud Logging (gcloud / opencontext logs --cloud gcp)
```

Local dev uses `opencontext serve` (aiohttp) with the same MCP handler stack; no cloud ingress.

## Shared Plugin Base Layer

Provider plugins are decoupled from infrastructure through three shared base
modules, so each plugin contains only provider-specific logic:

- **`core/base_plugin.py` — `BaseOpenDataPlugin`**: HTTP client lifecycle
  (`_create_http_client` + automatic cleanup in `shutdown()`), retry policy
  (`HTTP_RETRY`), portal-error translation (`_raise_http_error`),
  declarative tool dispatch (`tool_handlers()` returning `ToolHandler`s with
  `required_args` enforcement — plugins do not write `execute_tool`), capped
  record formatting (`format_records`), and safe `WHERE`-clause construction
  (`build_where_clause`, which validates field identifiers).
- **`core/config_base.py` — `BasePluginConfig`**: shared pydantic model
  (`enabled`, `city_name`, `timeout`, `extra="forbid"`) plus a reusable
  `validate_url` field validator.
- **`core/query_validator.py` — `BaseQueryValidator`**: length cap,
  `SELECT`-only prefix, forbidden-keyword scan, and multi-statement checks;
  provider validators subclass it (CKAN SQL, Socrata SoQL) or reuse the
  keyword scan for WHERE fragments (ArcGIS).

The Lambda adapter (`server/adapters/aws_lambda.py`) keeps one event loop
across warm invocations, so plugin HTTP clients are created once per
container rather than once per request.

See [Custom Plugins Guide](CUSTOM_PLUGINS.md) for how to extend this layer.

## Plugins

Each deployment enables **exactly one** plugin.

### Built-in: CKAN

For CKAN-based open data portals (e.g., data.gov, data.gov.uk).

**Configuration:**

```yaml
plugins:
  ckan:
    enabled: true
    base_url: "https://data.yourcity.gov"
    portal_url: "https://data.yourcity.gov"
    city_name: "Your City"
    timeout: 120
    api_key: "${CKAN_API_KEY}"  # Optional
```

**Tools:**

| Tool | Description |
|------|-------------|
| `ckan__search_datasets(query, limit)` | Free-text search with catalog-wide match count |
| `ckan__list_datasets(query, filters…, sort, limit, offset)` | Browse the catalog by organization/tag/format/license/group, sorted and paged |
| `ckan__get_catalog_stats(facets, query, filters…, limit)` | Dataset counts overall and per facet value |
| `ckan__get_dataset(dataset_id, max_resources)` | Full dataset metadata incl. license, dates, tags, and per-resource dates/URLs |
| `ckan__query_data(resource_id, filters, limit)` | Query data from a resource |
| `ckan__get_schema(resource_id)` | Get schema for a resource |
| `ckan__execute_sql(sql)` | Execute PostgreSQL SELECT queries (advanced) |
| `ckan__aggregate_data(resource_id, metrics, group_by, …)` | GROUP BY aggregations without writing SQL |

**SQL execution:** Only SELECT is allowed. Resource IDs must be valid UUIDs in double quotes: `FROM "uuid-here"`. See [CKAN API docs](https://docs.ckan.org/en/latest/api/) for details.

### Built-in: ArcGIS Hub

For ArcGIS Hub open data portals (e.g., hub.arcgis.com, data-yourcity.hub.arcgis.com).

**Configuration:**

```yaml
plugins:
  arcgis:
    enabled: true
    portal_url: "https://hub.arcgis.com"
    city_name: "Your City"
    timeout: 120
    token: "${ARCGIS_TOKEN}"  # Optional: bearer token for private items
    # trusted_service_hosts: ["maps2.dcgis.dc.gov"]  # explicit extra hosts
    # auto_trust_hub_services: true  # accept Hub-referenced service URLs (default)
```

**Tools:**

| Tool | Description |
|------|-------------|
| `arcgis__search_datasets(query, limit)` | Search the Hub catalog |
| `arcgis__get_dataset(dataset_id)` | Get metadata for a Hub item: full description, data edit dates, and the service's layers and tables |
| `arcgis__get_aggregations(field, query)` | Facet counts for type, tags, categories, or access |
| `arcgis__get_schema(dataset_id, layer)` | Field names, types, aliases, lengths and domains for a layer or table |
| `arcgis__query_data(dataset_id, layer, where, out_fields, limit, offset, order_by, format)` | Query a Feature Service; total match count, next-page offset, text/json/csv output |
| `arcgis__aggregate_data(dataset_id, statistics, group_by, where, having, order_by, layer, limit, format)` | Server-side count/sum/avg/min/max/stddev, grouped by fields |

See [BUILT_IN_PLUGINS.md](BUILT_IN_PLUGINS.md#arcgis-hub-plugin) for argument details.

### Built-in: Socrata

For Socrata-based open data portals (e.g., data.cityofchicago.org, data.seattle.gov).

**Configuration:**

```yaml
plugins:
  socrata:
    enabled: true
    base_url: "https://data.yourcity.gov"
    portal_url: "https://data.yourcity.gov"
    city_name: "Your City"
    app_token: "${SOCRATA_APP_TOKEN}"  # Optional (recommended); register at dev.socrata.com
    timeout: 30
```

**Tools:**

| Tool | Description |
|------|-------------|
| `socrata__search_datasets(query, limit)` | Search the portal catalog |
| `socrata__get_dataset(dataset_id)` | Get metadata for a dataset (4x4 ID) |
| `socrata__get_schema(dataset_id)` | Get column schema for constructing SoQL queries |
| `socrata__query_dataset(dataset_id, soql_query)` | Query data using SoQL |
| `socrata__list_categories()` | List all categories with dataset counts |
| `socrata__execute_sql(dataset_id, soql)` | Execute raw SoQL SELECT (advanced) |

### Built-in: Opendatasoft

For Opendatasoft portals using the Explore API v2.1 (e.g., data.longbeach.gov, public.opendatasoft.com).

**Configuration:**

```yaml
plugins:
  opendatasoft:
    enabled: true
    base_url: "https://data.yourcity.gov"
    portal_url: "https://data.yourcity.gov"
    city_name: "Your City"
    timeout: 30
    api_key: "${ODS_API_KEY}"  # Optional: private datasets only
```

**Tools:**

| Tool | Description |
|------|-------------|
| `opendatasoft__search_datasets(query, limit)` | Search the portal catalog |
| `opendatasoft__get_dataset(dataset_id)` | Dataset metadata (publisher, license, dates, counts) |
| `opendatasoft__get_schema(dataset_id)` | Field names and types for ODSQL clauses |
| `opendatasoft__query_data(dataset_id, where, select, order_by, limit)` | Query records with ODSQL |
| `opendatasoft__aggregate_data(dataset_id, metrics, group_by, …)` | GROUP BY aggregations |
| `opendatasoft__list_categories()` | Portal themes with dataset counts |

### Custom Plugins

Add your own plugins in `custom_plugins/`. They are auto-discovered.

**Quick start:**

```bash
mkdir -p custom_plugins/my_plugin
cp custom_plugins/template/plugin_template.py custom_plugins/my_plugin/plugin.py
```

Edit the plugin, add config to `config.yaml` (create from `config-example.yaml` if needed), then `opencontext deploy --env staging`.

**Structure:**
- Inherit from `MCPPlugin` (or `DataPlugin` for data sources)
- Set: `plugin_name`, `plugin_type`, `plugin_version`
- Place in: `custom_plugins/your_plugin_name/plugin.py`
- Tool names: no prefix—Plugin Manager adds it (e.g., `my_plugin__search`)

**Required methods:**

```python
def __init__(self, config: Dict[str, Any]) -> None
async def initialize() -> bool
async def shutdown() -> None
def get_tools() -> List[ToolDefinition]
async def execute_tool(tool_name, arguments) -> ToolResult
async def health_check() -> bool
```

**DataPlugin:** For data sources, inherit from `DataPlugin` and implement `search_datasets`, `get_dataset`, and `query_data`.

**Best practices:** Return `ToolResult(success=False, error_message=...)` on failure. Use `logging.getLogger(__name__)`. Validate config in `initialize()`.

**Reference:**
- [Plugin template](../custom_plugins/template/plugin_template.py)
- [CKAN plugin](../plugins/ckan/) – Full implementation
- [Examples](../examples/) – Per-city configuration examples

## Plugin Interface

```python
class MCPPlugin(ABC):
    plugin_name: str
    plugin_type: PluginType
    plugin_version: str

    async def initialize() -> bool
    async def shutdown() -> None
    def get_tools() -> List[ToolDefinition]
    async def execute_tool(tool_name, arguments) -> ToolResult
    async def health_check() -> bool
```

## Endpoints

| Endpoint | Auth | Use |
|----------|------|-----|
| API Gateway | Throttling + usage plan quota | Production |
| Lambda Function URL | None | Testing only |

## Configuration

Single `config.yaml`; JSON-encoded and injected as the `OPENCONTEXT_CONFIG` Lambda environment variable at deploy time. Validated at deploy and runtime.

## Security & Scalability

- **API Gateway:** Configurable throttling (default: 10 burst / 5 sustained req/s) and daily quota via `api_quota_limit`, `api_burst_limit`, `api_rate_limit` Terraform variables
- **Lambda URL:** Public — testing only; use API Gateway for production
- **X-Ray:** Active tracing on all Lambda invocations and API Gateway stage
- **SQS DLQ:** Failed async invocations written to `<function-name>-dlq` for inspection
- **Stateless:** No shared state; Lambda auto-scales
- **Logging:** CloudWatch Logs, structured JSON, 14-day retention

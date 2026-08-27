# OpenContext Architecture

## Overview

OpenContext is a plugin-based framework. Each deployment runs **one** server with **one** plugin. This keeps deployments simple, independently scalable, and easy to maintain.

## One Fork = One Server

**Enforcement:**
- `scripts/deploy.sh` validates config before deployment
- `plugin_manager.py` fails if multiple plugins are enabled

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
│   └── aws_lambda.py   # Lambda handler entry point (persistent event loop)
└── http_handler.py     # HTTP request handling

plugins/                # Built-in providers on the shared base
├── ckan/
│   ├── plugin.py
│   ├── config_schema.py
│   └── sql_validator.py
├── socrata/
│   ├── plugin.py
│   ├── config_schema.py
│   └── soql_validator.py
├── arcgis/
│   ├── plugin.py
│   ├── config_schema.py
│   └── where_validator.py

custom_plugins/         # User plugins (auto-discovered)
├── template/
│   └── plugin_template.py

examples/               # Example configs and plugins
├── boston-opendata/
└── custom-plugin/

client/                 # Go stdio-to-HTTP client (optional)
tests/                  # Unit tests
```

### Request Flow

```
Claude Desktop / App
    → stdio bridge (npx) or Go client
Lambda / Local Server
    → server.adapters.aws_lambda or scripts/local_server.py
    → MCP Server (core/mcp_server.py)
    → Plugin Manager
    → Plugin (e.g., CKAN)
    → External API
```

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

The Lambda adapter (`server/adapters/aws_lambda.py`) maintains a persistent
event loop across warm invocations, so plugin HTTP clients are created once
per container rather than once per request.

See [Custom Plugins Guide](CUSTOM_PLUGINS.md) for how to extend this layer.

## Plugins

Each deployment enables **exactly one** plugin.

### Built-in: CKAN

For CKAN-based open data portals (e.g., data.boston.gov, data.gov, data.gov.uk).

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

**SQL execution:** The `execute_sql` tool allows complex PostgreSQL queries (CTEs, window functions, joins). Only SELECT is allowed. INSERT, UPDATE, DELETE, DROP, and other destructive operations are blocked. Resource IDs must be valid UUIDs in double quotes: `FROM "uuid-here"`. See [CKAN API docs](https://docs.ckan.org/en/latest/api/) for details.

### Custom Plugins

Add your own plugins in `custom_plugins/`. They are auto-discovered.

**Quick start:**

```bash
mkdir -p custom_plugins/my_plugin
cp custom_plugins/template/plugin_template.py custom_plugins/my_plugin/plugin.py
```

Edit the plugin, add config to `config.yaml` (create from `config-example.yaml` if needed), then `./scripts/deploy.sh`.

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
- [Examples](../examples/custom-plugin/) – Custom plugin example

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
| API Gateway | Rate limit, quota | Production |
| Lambda Function URL | None | Testing |

## Configuration

Single `config.yaml`; passed to Lambda via `OPENCONTEXT_CONFIG`. Validated at deploy and runtime.

## Security & Scalability

- **API Gateway:** Rate limiting (100 burst, 50 sustained/s), configurable daily quota
- **Lambda URL:** Public—testing only
- **Stateless:** No shared state; Lambda auto-scales
- **Logging:** CloudWatch, structured JSON, request IDs
- **Untrusted portal content:** every tool result is framed, normalized, and size-capped before it reaches the model; see [Security](SECURITY.md)

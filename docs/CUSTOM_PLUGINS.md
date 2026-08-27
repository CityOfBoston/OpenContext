# Custom Plugins Guide

Learn how to create custom plugins for OpenContext.

## Overview

Custom plugins allow you to integrate OpenContext with your own APIs, databases, or data sources. Plugins are added to the `custom_plugins/` directory and automatically discovered by the Plugin Manager.

## Quick Start

1. Copy the template:
   ```bash
   cp custom_plugins/template/plugin_template.py custom_plugins/my_plugin/plugin.py
   ```

2. Edit `custom_plugins/my_plugin/plugin.py`:
   - Replace `MyCustomPlugin` with your class name
   - Set `plugin_name` to your plugin name
   - Implement all TODO sections

3. Add configuration to `config.yaml`:
   ```yaml
   plugins:
     my_plugin:
       enabled: true
       api_url: "https://api.example.com"
       api_key: "${MY_API_KEY}"
   ```

4. Deploy: `./scripts/deploy.sh`

## Plugin Structure

All plugins must:

1. Inherit from `MCPPlugin` (or `DataPlugin` for data sources, or `BaseOpenDataPlugin` for the shared base)
2. Set class attributes: `plugin_name`, `plugin_type`, `plugin_version`
3. Implement all required methods
4. Be placed in `custom_plugins/your_plugin_name/plugin.py`

> **Tip:** The recommended starting point for new open-data providers is `BaseOpenDataPlugin` (see the [plugin template](../custom_plugins/template/plugin_template.py)). It bundles HTTP client lifecycle, retry policy, error translation, and tool dispatch so you only fill in the provider-specific logic.

## The Decoupled Base Layer (Recommended)

The plugin architecture is split into three layers so provider plugins stay
small and share hardened infrastructure instead of re-implementing it:

```
core/interfaces.py        # Contracts: MCPPlugin, DataPlugin, ToolDefinition, ToolResult
core/base_plugin.py       # BaseOpenDataPlugin + ToolHandler: HTTP, retry, dispatch, formatting
core/config_base.py       # BasePluginConfig: shared pydantic config + URL validation
core/query_validator.py   # BaseQueryValidator: shared SQL/SoQL safety checks
plugins/*, custom_plugins/*   # Provider-specific logic only
```

A plugin built on this layer never writes its own `execute_tool` dispatch,
HTTP client bookkeeping, retry loop, or record formatting — it declares tools
and implements provider calls. The built-in CKAN, Socrata, and ArcGIS plugins
are all written this way.

### What `BaseOpenDataPlugin` gives you

| Facility | What it does |
|---|---|
| `tool_handlers()` | Declare `{tool_name: ToolHandler(handler, required_args=(...))}`; the base's `execute_tool` routes calls, rejects missing/empty required arguments, and translates exceptions into failed `ToolResult`s |
| `_create_http_client(**kwargs)` | Creates an `httpx.AsyncClient` that the base tracks and closes for you in `shutdown()` |
| `HTTP_RETRY` | Decorator adding exponential-backoff retries (3 attempts) for transient HTTP errors |
| `_raise_http_error(exc, context)` | Translates `httpx.HTTPStatusError` into a user-readable `RuntimeError`, extracting portal error messages when present |
| `format_records(records, max_display=10, header=None, skip_keys=...)` | Renders query results in the standard `Record N:` style, capped with `... and X more record(s)` |
| `build_where_clause(filters)` | Builds a SQL `WHERE` body from a filter dict; escapes string values and **validates field names as plain identifiers** so SQL cannot be smuggled in through keys |

### Minimal example

```python
from typing import Any

from core.base_plugin import HTTP_RETRY, BaseOpenDataPlugin, ToolHandler
from core.interfaces import PluginType, ToolDefinition, ToolResult


class MyPortalPlugin(BaseOpenDataPlugin):
    plugin_name = "my_portal"
    plugin_type = PluginType.CUSTOM_API
    plugin_version = "1.0.0"

    async def initialize(self) -> bool:
        # Tracked client: closed automatically by the base's shutdown()
        self.client = self._create_http_client(
            base_url=self.config["api_url"],
            timeout=self.config.get("timeout", 30.0),
        )
        self._initialized = True
        return True

    def get_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="search_datasets",
                description="Search the portal catalog",
                input_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            ),
        ]

    def tool_handlers(self) -> dict[str, ToolHandler]:
        # No execute_tool() needed: the base dispatches and enforces
        # required_args before your handler runs.
        return {
            "search_datasets": ToolHandler(
                handler=self._tool_search, required_args=("query",)
            ),
        }

    async def _tool_search(self, arguments: dict[str, Any]) -> ToolResult:
        results = await self._search(arguments["query"])
        return ToolResult(
            content=[{"type": "text", "text": self.format_records(results)}],
            success=True,
        )

    @HTTP_RETRY
    async def _search(self, query: str) -> list[dict[str, Any]]:
        response = await self.client.get("/search", params={"q": query})
        response.raise_for_status()
        return response.json()["results"]
```

### Shared config schema: `BasePluginConfig`

Subclass it for `enabled`, `city_name`, and `timeout` for free, and reuse the
shared URL validator instead of writing your own:

```python
from pydantic import Field, field_validator

from core.config_base import BasePluginConfig


class MyPortalConfig(BasePluginConfig):
    api_url: str = Field(..., description="API base URL")

    _validate_urls = field_validator("api_url")(BasePluginConfig.validate_url)
```

`validate_url` enforces http/https and a hostname, and strips trailing
slashes. `extra="forbid"` is on by default, so config typos fail fast.

### Shared query safety: `BaseQueryValidator`

If your plugin accepts SQL-ish input, subclass `BaseQueryValidator` rather
than writing a validator from scratch. It enforces a length cap, a
`SELECT`-only prefix (`ALLOWED_PREFIXES`), forbidden keywords
(`FORBIDDEN_KEYWORDS`), and multi-statement/dangerous-pattern checks.
Override `extra_checks()` for provider-specific rules, or call
`scan_forbidden_keywords()` alone for WHERE-clause-style fragments (see
`plugins/arcgis/where_validator.py`, which also strips quoted string literals
first so legitimate values like `status = 'SET'` pass).

## Required Methods

### `__init__(config)`

Initialize plugin with configuration from `config.yaml`.

```python
def __init__(self, config: Dict[str, Any]) -> None:
    super().__init__(config)
    self.api_url = config.get("api_url")
    self.api_key = config.get("api_key")
```

### `async initialize() -> bool`

Set up connections, test connectivity, validate configuration.

```python
async def initialize(self) -> bool:
    self.client = httpx.AsyncClient(base_url=self.api_url)
    response = await self.client.get("/health")
    response.raise_for_status()
    self._initialized = True
    return True
```

### `async shutdown() -> None`

Clean up resources.

```python
async def shutdown(self) -> None:
    if self.client:
        await self.client.aclose()
    self._initialized = False
```

### `get_tools() -> List[ToolDefinition]`

Return list of tools your plugin provides.

```python
def get_tools(self) -> List[ToolDefinition]:
    return [
        ToolDefinition(
            name="search",
            description="Search for items",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                },
                "required": ["query"],
            },
        ),
    ]
```

**Important:** Tool names should NOT include plugin prefix. The Plugin Manager adds it automatically using double underscores (e.g., `my_plugin__search`).

### `async execute_tool(tool_name, arguments) -> ToolResult`

Execute a tool by name.

```python
async def execute_tool(self, tool_name: str, arguments: Dict[str, Any]) -> ToolResult:
    if tool_name == "search":
        query = arguments.get("query")
        results = await self._search(query)
        return ToolResult(
            content=[{"type": "text", "text": self._format_results(results)}],
            success=True,
        )
    else:
        return ToolResult(
            content=[],
            success=False,
            error_message=f"Unknown tool: {tool_name}",
        )
```

### `async health_check() -> bool`

Check if plugin is healthy.

```python
async def health_check(self) -> bool:
    try:
        response = await self.client.get("/health")
        return response.status_code == 200
    except:
        return False
```

## DataPlugin Interface

If your plugin provides data operations, inherit from `DataPlugin` instead:

```python
from core.interfaces import DataPlugin

class MyDataPlugin(DataPlugin):
    async def search_datasets(self, query: str, limit: int = 20):
        # Implement dataset search
        pass

    async def get_dataset(self, dataset_id: str):
        # Implement dataset retrieval
        pass

    async def query_data(self, resource_id: str, filters: Optional[Dict] = None, limit: int = 100):
        # Implement data querying
        pass
```

## Best Practices

### Error Handling

Always handle errors gracefully:

```python
try:
    result = await self._call_api()
    return ToolResult(content=[...], success=True)
except Exception as e:
    logger.error(f"Error: {e}", exc_info=True)
    return ToolResult(
        content=[],
        success=False,
        error_message=f"Operation failed: {str(e)}",
    )
```

### Logging

Use structured logging:

```python
import logging

logger = logging.getLogger(__name__)

logger.info("Plugin initialized")
logger.error("Error occurred", exc_info=True)
```

### Configuration Validation

Validate configuration in `initialize()`:

```python
async def initialize(self) -> bool:
    if not self.api_url:
        raise ValueError("api_url is required")
    # ...
```

### User-Friendly Output

Format results for clarity:

```python
def _format_results(self, data: List[Dict]) -> str:
    lines = [f"Found {len(data)} results:\n"]
    for item in data:
        lines.append(f"- {item['name']}: {item['description']}")
    return "\n".join(lines)
```

## Example: Simple API Plugin

```python
from core.interfaces import MCPPlugin, PluginType, ToolDefinition, ToolResult
import httpx

class MyAPIPlugin(MCPPlugin):
    plugin_name = "my_api"
    plugin_type = PluginType.CUSTOM_API
    plugin_version = "1.0.0"

    def __init__(self, config: Dict[str, Any]) -> None:
        super().__init__(config)
        self.api_url = config["api_url"]
        self.client = None

    async def initialize(self) -> bool:
        self.client = httpx.AsyncClient(base_url=self.api_url)
        self._initialized = True
        return True

    async def shutdown(self) -> None:
        if self.client:
            await self.client.aclose()
        self._initialized = False

    def get_tools(self) -> List[ToolDefinition]:
        return [
            ToolDefinition(
                name="get_item",
                description="Get an item by ID",
                input_schema={
                    "type": "object",
                    "properties": {
                        "item_id": {"type": "string"},
                    },
                    "required": ["item_id"],
                },
            ),
        ]

    async def execute_tool(self, tool_name: str, arguments: Dict[str, Any]) -> ToolResult:
        if tool_name == "get_item":
            item_id = arguments["item_id"]
            response = await self.client.get(f"/items/{item_id}")
            data = response.json()
            return ToolResult(
                content=[{"type": "text", "text": f"Item: {data['name']}"}],
                success=True,
            )
        return ToolResult(content=[], success=False, error_message="Unknown tool")

    async def health_check(self) -> bool:
        try:
            response = await self.client.get("/health")
            return response.status_code == 200
        except:
            return False
```

## Testing

Test your plugin locally before deploying:

```python
# test_my_plugin.py
import asyncio
from custom_plugins.my_plugin.plugin import MyAPIPlugin

async def test():
    plugin = MyAPIPlugin({"api_url": "https://api.example.com"})
    await plugin.initialize()

    tools = plugin.get_tools()
    print(f"Tools: {[t.name for t in tools]}")

    result = await plugin.execute_tool("get_item", {"item_id": "123"})
    print(f"Result: {result.success}")

asyncio.run(test())
```

## Configuration Schema

For complex plugins, create a Pydantic schema:

```python
# custom_plugins/my_plugin/config_schema.py
from pydantic import BaseModel

class MyPluginConfig(BaseModel):
    enabled: bool = False
    api_url: str
    api_key: Optional[str] = None
    timeout: int = 120
```

Use in plugin:

```python
from custom_plugins.my_plugin.config_schema import MyPluginConfig

def __init__(self, config: Dict[str, Any]) -> None:
    super().__init__(config)
    self.plugin_config = MyPluginConfig(**config)
```

## Reference

- [Plugin Template](../custom_plugins/template/plugin_template.py)
- [Shared Base Plugin](../core/base_plugin.py) - `BaseOpenDataPlugin`, `ToolHandler`, `HTTP_RETRY`
- [Shared Config Base](../core/config_base.py) - `BasePluginConfig`
- [Shared Query Validator](../core/query_validator.py) - `BaseQueryValidator`
- [CKAN Plugin](../plugins/ckan/plugin.py) - Example implementation
- [Core Interfaces](../core/interfaces.py) - API reference

## Getting Help

- [FAQ](FAQ.md)
- [GitHub Issues](https://github.com/thealphacubicle/OpenContext/issues)
- [Architecture Guide](ARCHITECTURE.md)

# Testing Guide

This guide covers three ways to test your OpenContext server locally.

## Prerequisites

Before testing:

1. Create `config.yaml` from `config-example.yaml` and enable exactly one plugin
2. Install dependencies: `pip install aiohttp`
3. Start the server: `python3 scripts/local_server.py`

The server runs at `http://localhost:8000/mcp`. Keep it running while you test.

---

## Method 1: Terminal (cURL)

Use the terminal to send requests directly to the server.

**Ping:**

```bash
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"ping"}'
```

**List tools:**

```bash
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
```

**Call a tool:**

```bash
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"ckan__search_datasets","arguments":{"query":"housing","limit":3}}}'
```

For a full test (initialize, list tools, call tool), run:

```bash
./scripts/test_streamable_http.sh
```

---

## Method 2: Claude Desktop

1. Add the server to your Claude Desktop config (see [Getting Started](GETTING_STARTED.md))
2. Point the URL to `http://localhost:8000/mcp`
3. Restart Claude Desktop
4. Ask Claude to search your data or list available tools

---

## Method 3: MCP Inspector

MCP Inspector is a web-based tool for testing MCP servers.

1. With the server running, open a new terminal
2. Run: `npx @modelcontextprotocol/inspector`
3. The Inspector UI opens in your browser (typically `http://localhost:6274`)
4. In the Inspector, select **streamable-http** as the transport
5. Enter the URL: `http://localhost:8000/mcp`
6. Use the Tools tab to list and call tools

---

## Quick Checks

Optional checks before starting the server.

**Config validation:**

```bash
python3 -c "
import yaml
from core.validators import load_and_validate_config
config = load_and_validate_config('config.yaml')
print('Config valid:', config['server_name'])
"
```

**Plugin loading:**

```bash
python3 -c "
import asyncio, yaml
from core.plugin_manager import PluginManager
async def t():
    with open('config.yaml') as f: config = yaml.safe_load(f)
    pm = PluginManager(config)
    await pm.load_plugins()
    print('Tools:', [t['name'] for t in pm.get_all_tools()])
    await pm.shutdown()
asyncio.run(t())
"
```

---

## Unit Tests

```bash
pip install pytest pytest-asyncio sqlparse
pytest
pytest tests/test_plugin_manager.py -v
pytest --cov=core --cov=plugins
```

---

## Testing Against Production

To test a deployed server, use the Lambda URL or API Gateway URL:

```bash
LAMBDA_URL="https://your-lambda-url.lambda-url.us-east-1.on.aws"
curl -X POST $LAMBDA_URL/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"ping"}'
```

See [Deployment](DEPLOYMENT.md) for how to get the URL.

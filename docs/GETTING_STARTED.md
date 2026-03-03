# Getting Started

Get your OpenContext server running in under 10 minutes.

OpenContext uses the Model Context Protocol (MCP), which connects AI assistants to external data. Your server exposes tools that AI assistants can call to search and query your open data.

## Prerequisites

- Python 3.11+
- Node.js and npm (required for connecting Claude Desktop via Streamable HTTP)
- Terraform >= 1.0 (for deployment)
- AWS CLI configured (for deployment)

## Quick Path: Local Testing

Test the server locally before deploying.

### 1. Configure Your Plugin

Create `config.yaml` from the template and enable **exactly one** plugin:

```bash
cp config-example.yaml config.yaml
```

Edit `config.yaml`. For CKAN:

```yaml
plugins:
  ckan:
    enabled: true
    base_url: "https://data.boston.gov"
    portal_url: "https://data.boston.gov"
    city_name: "Boston"
    timeout: 120
```

Each deployment connects to one data source. To connect another source, deploy a separate server. See [Architecture](ARCHITECTURE.md) for details.

### 2. Start the Local Server

```bash
pip install aiohttp
python3 scripts/local_server.py
```

The server runs at `http://localhost:8000/mcp`. Keep this terminal open.

### 3. Connect Claude Desktop

Add to your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

**Option A: Streamable HTTP (recommended—no binary)**

```json
{
  "mcpServers": {
    "OpenContext Local": {
      "command": "npx",
      "args": [
        "-y",
        "@modelcontextprotocol/server-stdio-to-http",
        "--transport",
        "streamable-http",
        "http://localhost:8000/mcp"
      ]
    }
  }
}
```

**Option B: Go client binary**

Build the client: `cd client && make build`. Then:

```json
{
  "mcpServers": {
    "OpenContext Local": {
      "command": "/path/to/opencontext-client",
      "args": ["http://localhost:8000"]
    }
  }
}
```

The Go client auto-appends `/mcp`. Restart Claude Desktop after config changes.

### 4. Verify

```bash
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"ping"}'
```

You can also test with Claude by asking it to search your data, or use [Testing](TESTING.md) for more options (MCP Inspector, full test script).

---

## Production Deployment

### 1. Fork & Configure

1. Fork the [OpenContext repository](https://github.com/thealphacubicle/OpenContext)
2. Clone your fork
3. Create config: `cp config-example.yaml config.yaml`
4. Edit `config.yaml` with **exactly one** plugin enabled

### 2. Deploy to AWS

```bash
./scripts/deploy.sh
```

The script validates config, packages code, and deploys to AWS Lambda. You'll receive:
- **Lambda Function URL** – for testing (no auth)
- **API Gateway URL** – for production (API key, rate limiting)

AWS creates: Lambda function, Function URL, API Gateway, IAM role, CloudWatch Log Group. Cost is roughly $1/month for 100K requests. See [Deployment](DEPLOYMENT.md) for details.

### 3. Connect Claude Desktop (Production)

**Production (API Gateway—recommended):**

```bash
cd terraform/aws
terraform output -raw api_gateway_url
```

Use the full URL from `terraform output` (it already includes `/mcp`):

```json
{
  "mcpServers": {
    "Boston OpenData": {
      "command": "npx",
      "args": [
        "-y",
        "@modelcontextprotocol/server-stdio-to-http",
        "--transport",
        "streamable-http",
        "https://YOUR-API-GATEWAY-URL"
      ]
    }
  }
}
```

**Testing (Lambda URL—no auth):**

```json
{
  "mcpServers": {
    "Boston OpenData": {
      "command": "npx",
      "args": [
        "-y",
        "@modelcontextprotocol/server-stdio-to-http",
        "--transport",
        "streamable-http",
        "https://your-lambda-url.lambda-url.us-east-1.on.aws/mcp"
      ]
    }
  }
}
```

### 4. Updating

To update config or code: edit `config.yaml` or your code, then run `./scripts/deploy.sh` again.

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `ModuleNotFoundError: aiohttp` | `pip install aiohttp` |
| "Multiple Plugins Enabled" | Enable only one plugin in `config.yaml` |
| Claude can't connect | Verify URL includes `/mcp`, restart Claude Desktop |
| Lambda 500 error | Check CloudWatch logs, validate config |
| Plugin init fails | Check API URLs, keys, and network connectivity |

---

## Next Steps

- [Architecture](ARCHITECTURE.md) – System design, built-in plugins, custom plugins
- [Deployment](DEPLOYMENT.md) – AWS details, monitoring, cost
- [Testing](TESTING.md) – Local testing (Terminal, Claude, MCP Inspector)

---

## Support

[GitHub Issues](https://github.com/thealphacubicle/OpenContext/issues)

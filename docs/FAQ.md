# Frequently Asked Questions

## General

### What is OpenContext?

OpenContext is an extensible MCP framework template that governments can fork to deploy MCP servers for their civic data platforms. Each fork deploys exactly ONE MCP server with ONE plugin enabled.

### Why "One Fork = One MCP Server"?

This architecture keeps deployments simple, independently scalable, and easy to maintain. See [Architecture Guide](ARCHITECTURE.md) for details.

### Can I deploy multiple plugins?

No. Each fork must deploy exactly ONE plugin. To deploy multiple plugins, fork the repository multiple times (one fork per plugin).

### What is MCP?

MCP (Model Context Protocol) is a protocol for connecting AI assistants to external data sources. Learn more at [modelcontextprotocol.io](https://modelcontextprotocol.io).

## Configuration

### How do I enable a plugin?

Edit `config.yaml` and set `enabled: true` for ONE plugin:

```yaml
plugins:
  ckan:
    enabled: true # Only ONE plugin should be enabled
```

### Can I use environment variables in config.yaml?

Yes, but they must be resolved before deployment. Terraform will set the final config as a Lambda environment variable.

### What if I need to change configuration after deployment?

Edit `config.yaml` and run `opencontext deploy --env staging` again (add `--cloud gcp` if you deployed to GCP). Terraform updates the runtime configuration (`OPENCONTEXT_CONFIG`).

## Plugins

### What plugins are available?

Built-in plugins:

- **CKAN** — For CKAN-based portals (e.g., data.gov, data.gov.uk)
- **ArcGIS Hub** — For ArcGIS Hub portals (e.g., hub.arcgis.com)
- **Socrata** — For Socrata portals (e.g., data.cityofchicago.org)
- **Opendatasoft** — For Opendatasoft Explore v2.1 portals (e.g., data.longbeach.gov)

See [Built-in Plugins](BUILT_IN_PLUGINS.md) for each plugin's tools and configuration.

You can also create custom plugins in `custom_plugins/`.

### How do I create a custom plugin?

See [Custom Plugins Guide](CUSTOM_PLUGINS.md) for detailed instructions.

### Can I modify built-in plugins?

No. Built-in plugins are part of the core framework. Create a custom plugin instead.

## Deployment

### What cloud resources are created?

**AWS (`--cloud aws`, default):**

- Lambda function
- API Gateway (REST)
- IAM role and policies
- CloudWatch Log Group
- SQS dead-letter queue (async failures)

**GCP (`--cloud gcp`):**

- Cloud Functions gen2 (HTTP)
- GCS bucket for deployment artifacts
- IAM for public HTTPS invoke (Cloud Run invoker)

### How much does it cost?

Typical costs: ~$1/month for 100K requests. See [Deployment Guide](DEPLOYMENT.md) for details.

### Can I deploy to a different cloud provider?

**AWS** and **GCP** are supported via `--cloud aws` (default) or `--cloud gcp` on `authenticate`, `configure`, `validate`, `deploy`, `status`, `logs`, and `destroy`. See [Deployment Guide](DEPLOYMENT.md) and [terraform/gcp/README.md](../terraform/gcp/README.md). Azure Terraform is a placeholder only.

### How do I update an existing deployment?

Run `opencontext deploy --env staging` again. Terraform will update the Lambda function.

## Usage

### How do I connect to it with Claude?

Connect via **Claude Connectors** (same steps on both Claude.ai and Claude Desktop):

1. Go to **Settings** → **Connectors** (or **Customize** → **Connectors** on claude.ai)
2. Click **Add custom connector**
3. Enter a name and your API Gateway URL (from `terraform output -raw api_gateway_url`)

Enable the connector in your conversation by clicking "+" → Connectors → toggle it on.

### Can I use it without Claude Desktop?

Yes! Call the Lambda URL directly via HTTP POST with MCP JSON-RPC format.

### How do I test my deployment?

```bash
curl -X POST https://your-lambda-url \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

## Troubleshooting

### Deploy script fails: "Multiple Plugins Enabled"

**Solution:** Enable only ONE plugin in `config.yaml`. Disable all others.

### Lambda returns 500 error

**Check:**

1. CloudWatch logs for errors
2. Configuration is valid
3. Plugin initialization succeeded

### Claude can't connect

**Check:**

1. API Gateway or Lambda URL is correct (includes `/mcp`)
2. Connector is added in Settings → Connectors
3. Connector is enabled for the conversation (click "+" → Connectors → toggle on)

### Plugin initialization fails

**Check:**

1. API URLs are correct
2. API keys are valid (if required)
3. Network connectivity from Lambda
4. CloudWatch logs for specific errors

## Development

### How do I test locally?

**Option 1: Use the local server**

```bash
# Start local server
opencontext serve

# In another terminal, test with curl
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

**Option 2: Test plugins directly**

Create a test script that imports and initializes your plugin:

```python
import asyncio
from plugins.ckan.plugin import CKANPlugin

async def test():
    plugin = CKANPlugin({
        "base_url": "https://data.yourcity.gov",
        "portal_url": "https://data.yourcity.gov",
        "city_name": "Your City",
        "timeout": 120,
    })
    await plugin.initialize()
    tools = plugin.get_tools()
    print(f"Tools: {[t.name for t in tools]}")
    await plugin.shutdown()

asyncio.run(test())
```

**Option 3: Run unit tests**

```bash
uv sync --all-extras
uv run pytest tests/
```

### Can I contribute?

Yes! Contributions are welcome. Please open an issue or pull request.

### Where is the code?

[GitHub Repository](https://github.com/sgarcese/OpenContext)

## Support

### Where can I get help?

- [GitHub Issues](https://github.com/sgarcese/OpenContext/issues)
- [Documentation](.)
- [FAQ](FAQ.md) (this page)

### How do I report a bug?

Open an issue on GitHub with:

- Description of the problem
- Steps to reproduce
- Error messages/logs
- Configuration (redact secrets)

### How do I request a feature?

Open an issue on GitHub with:

- Description of the feature
- Use case
- Proposed implementation (if you have one)

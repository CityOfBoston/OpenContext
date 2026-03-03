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

Edit `config.yaml` and run `./scripts/deploy.sh` again. Terraform will update the Lambda environment variable.

## Plugins

### What plugins are available?

Built-in plugins:

- **CKAN** - For CKAN-based portals (data.boston.gov, data.gov, data.gov.uk)

You can also create custom plugins in `custom_plugins/`.

### How do I create a custom plugin?

See [Custom Plugins Guide](CUSTOM_PLUGINS.md) for detailed instructions.

### Can I modify built-in plugins?

No. Built-in plugins are part of the core framework. Create a custom plugin instead.

## Deployment

### What AWS resources are created?

- Lambda function
- Lambda Function URL
- IAM role and policies
- CloudWatch Log Group

### How much does it cost?

Typical costs: ~$1/month for 100K requests. See [Deployment Guide](DEPLOYMENT.md) for details.

### Can I deploy to a different cloud provider?

The current implementation is AWS-specific. Contributions for other providers are welcome!

### How do I update an existing deployment?

Run `./scripts/deploy.sh` again. Terraform will update the Lambda function.

## Usage

### How do I use it with Claude Desktop?

**First, download the client binary** from [GitHub Releases](https://github.com/thealphacubicle/OpenContext/releases) and make it executable.

Then add to your Claude Desktop config:

```json
{
  "mcpServers": {
    "my-server": {
      "command": "/path/to/opencontext-client",
      "args": ["https://your-lambda-url"]
    }
  }
}
```

**Note:** Use the full path to the binary, or ensure it's in your PATH.

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

### Claude Desktop can't connect

**Check:**

1. Lambda URL is correct
2. `opencontext-client` binary is downloaded and executable
3. Path to binary in config is correct (use full path)
4. Claude Desktop config JSON is valid
5. Restart Claude Desktop after config changes

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
# Install aiohttp if needed
pip install aiohttp

# Start local server
python3 local_server.py

# In another terminal, test with curl
curl -X POST http://localhost:8000 \
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
        "base_url": "https://data.boston.gov",
        "portal_url": "https://data.boston.gov",
        "city_name": "Boston",
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
pip install pytest pytest-asyncio
pytest tests/
```

### Can I contribute?

Yes! Contributions are welcome. Please open an issue or pull request.

### Where is the code?

[GitHub Repository](https://github.com/thealphacubicle/OpenContext)

## Support

### Where can I get help?

- [GitHub Issues](https://github.com/thealphacubicle/OpenContext/issues)
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

# Quick Start Guide

Get your OpenContext MCP server running in 5 minutes.

## Prerequisites

- Python 3.11+
- Terraform >= 1.0
- AWS CLI configured with credentials
- GitHub account (to fork repository)

## Step 1: Fork Repository

1. Go to [OpenContext repository](https://github.com/thealphacubicle/OpenContext)
2. Click "Fork"
3. Clone your fork locally

```bash
git clone https://github.com/your-org/opencontext.git
cd opencontext
```

## Step 2: Configure Plugin

Edit `config.yaml` and enable **ONE** plugin:

### For CKAN (e.g., data.boston.gov):

```yaml
server_name: "Boston OpenData"
plugins:
  ckan:
    enabled: true
    base_url: "https://data.boston.gov"
    portal_url: "https://data.boston.gov"
    city_name: "Boston"
    timeout: 120
```

**Important:** Enable only ONE plugin. The deploy script will reject multiple plugins.

## Step 3: Deploy

Run the deployment script:

```bash
./scripts/deploy.sh
```

The script will:

1. Validate configuration (ensures ONE plugin enabled)
2. Package Lambda code
3. Deploy to AWS Lambda
4. Output Lambda URL

You'll see output like:

```
✅ Deployment complete!

Lambda Function URL:
https://abc123.lambda-url.us-east-1.on.aws

To use with Claude Desktop, add to your Claude Desktop config:
...
```

## Step 4: Download Client Binary

Download the `opencontext-client` binary for your platform from [GitHub Releases](https://github.com/thealphacubicle/OpenContext/releases):

- **macOS (Intel):** `opencontext-client-darwin-amd64`
- **macOS (Apple Silicon):** `opencontext-client-darwin-arm64`
- **Linux:** `opencontext-client-linux-amd64`
- **Windows:** `opencontext-client-windows-amd64.exe`

Make it executable and move to a convenient location:

```bash
chmod +x opencontext-client-darwin-arm64  # Adjust for your platform
mv opencontext-client-darwin-arm64 ~/bin/opencontext-client  # Or another location in PATH
```

**Or build from source:**

```bash
cd client
make build
```

## Step 5: Use with Claude Desktop

Add to your Claude Desktop configuration file:

**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "boston-opendata": {
      "command": "/path/to/opencontext-client",
      "args": ["https://your-lambda-url.lambda-url.us-east-1.on.aws"]
    }
  }
}
```

**Note:** Use the full path to the `opencontext-client` binary, or ensure it's in your PATH.

Restart Claude Desktop to load the new server.

## Step 6: Test

**Test locally first (optional):**

```bash
# Start local server
python3 local_server.py

# In another terminal, test with curl
curl -X POST http://localhost:8000 \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"ping"}'
```

**Test in Claude Desktop:**

Try asking Claude:

```
Search for datasets about housing in Boston
```

Claude will use your MCP server to search the CKAN portal.

## Troubleshooting

### Deploy Script Fails: "Multiple Plugins Enabled"

**Solution:** Enable only ONE plugin in `config.yaml`. Disable all others.

### Lambda URL Not Working

**Check:**

1. Lambda function exists in AWS Console
2. Function URL is enabled
3. Configuration is correct

### Claude Desktop Can't Connect

**Check:**

1. Lambda URL is correct
2. `opencontext-client` binary is downloaded and executable
3. Path to `opencontext-client` in config is correct (use full path)
4. Claude Desktop config JSON is valid
5. Restart Claude Desktop after config changes

## Next Steps

- Read [Architecture Guide](ARCHITECTURE.md)
- Create [Custom Plugin](CUSTOM_PLUGINS.md)
- See [Examples](../examples/)

## Getting Help

- [FAQ](FAQ.md)
- [GitHub Issues](https://github.com/thealphacubicle/OpenContext/issues)
- [Documentation](.)

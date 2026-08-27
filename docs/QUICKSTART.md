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

API Gateway URL (use for Claude Connectors):
https://xxx.execute-api.us-east-1.amazonaws.com/staging/mcp
```

## Step 4: Connect via Claude Connectors

Connect using **Claude Connectors** (same steps on both Claude.ai and Claude Desktop):

1. Go to **Settings** → **Connectors** (or **Customize** → **Connectors** on claude.ai)
2. Click **Add custom connector**
3. Enter a name (e.g. "Boston OpenData") and your API Gateway URL

Get the URL from the deploy output, or run:

```bash
cd terraform/aws
terraform output -raw api_gateway_url
```

## Step 5: Test

**Test locally first (optional):**

```bash
# Start local server
python3 scripts/local_server.py

# In another terminal, test with curl
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"ping"}'
```

**Test in Claude:**

Enable your connector in the chat (click "+" → Connectors → toggle on), then ask:

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

### Claude Can't Connect

**Check:**

1. API Gateway or Lambda URL is correct (includes `/mcp`)
2. Connector is added in Settings → Connectors
3. Connector is enabled for the conversation (click "+" → Connectors → toggle on)

## Next Steps

- Read [Architecture Guide](ARCHITECTURE.md)
- Create [Custom Plugin](CUSTOM_PLUGINS.md)
- See [Examples](../examples/)

## Getting Help

- [FAQ](FAQ.md)
- [GitHub Issues](https://github.com/thealphacubicle/OpenContext/issues)
- [Documentation](.)

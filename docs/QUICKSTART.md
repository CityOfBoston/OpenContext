# Quick Start Guide

Get your OpenContext MCP server running in 5 minutes.

## Prerequisites

- Python 3.11+
- Terraform >= 1.0
- AWS CLI configured with credentials
- GitHub account (to fork repository)

## Step 1: Fork Repository

1. Go to [OpenContext repository](https://github.com/sgarcese/OpenContext)
2. Click "Fork"
3. Clone your fork locally

```bash
git clone https://github.com/your-org/opencontext.git
cd opencontext
```

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) if needed, then install dependencies and the `opencontext` CLI:

```bash
uv sync --extra cli
```

## Step 2: Check Prerequisites

```bash
uv run opencontext authenticate
```

This checks Python 3.11+, `uv`, AWS CLI, AWS credentials, and Terraform — and auto-installs `uv` and `awscli` if missing.

## Step 3: Configure

```bash
uv run opencontext configure
```

The interactive wizard prompts for:
- Organization name and city
- Plugin (CKAN, ArcGIS, or Socrata) and data source URL
- AWS region and Lambda settings
- Optional custom domain

It writes `config.yaml`, the Terraform `.tfvars` file, and initializes your Terraform workspace.

**Important:** Only one plugin can be enabled per deployment.

## Step 4: Deploy

```bash
uv run opencontext deploy --env staging
```

The command:
1. Validates configuration
2. Packages Lambda code
3. Runs `terraform plan` and shows a summary
4. Asks for confirmation before applying
5. Prints the API Gateway URL on success

```
✅ Deployment complete!

API Gateway URL (use for Claude Connectors):
https://xxx.execute-api.us-east-1.amazonaws.com/staging/mcp
```

## Step 5: Connect via Claude Connectors

Connect using **Claude Connectors** (same steps on both Claude.ai and Claude Desktop):

1. Go to **Settings** → **Connectors** (or **Customize** → **Connectors** on claude.ai)
2. Click **Add custom connector**
3. Enter a name (e.g. "Your City OpenData") and your API Gateway URL

To retrieve the URL later:

```bash
uv run opencontext status --env staging
```

## Step 6: Test

**Test locally first (optional):**

```bash
# Start local server
uv run opencontext serve

# In another terminal, test with curl
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"ping"}'
```

**Test deployed server:**

```bash
uv run opencontext test --env staging
```

**Test in Claude:**

Enable your connector in the chat (click "+" → Connectors → toggle on), then ask:

```
Search for datasets about housing
```

Claude will use your MCP server to search the data portal.

## Troubleshooting

### Prerequisites fail

Run `uv run opencontext authenticate` and follow the instructions for any failing check.

### Deploy Script Fails: "Multiple Plugins Enabled"

**Solution:** Enable only ONE plugin in `config.yaml`, or re-run `uv run opencontext configure`.

### Lambda URL Not Working

**Check:**

1. Lambda function exists in AWS Console
2. Function URL is enabled
3. Configuration is correct

### Claude Can't Connect

**Check:**

1. API Gateway URL is correct (includes `/mcp`)
2. Connector is added in Settings → Connectors
3. Connector is enabled for the conversation (click "+" → Connectors → toggle on)

## Next Steps

- Read [Architecture Guide](ARCHITECTURE.md)
- Create [Custom Plugin](CUSTOM_PLUGINS.md)
- See [Getting Started](GETTING_STARTED.md) for the full CLI reference

## Getting Help

- [FAQ](FAQ.md)
- [GitHub Issues](https://github.com/sgarcese/OpenContext/issues)
- [Documentation](.)

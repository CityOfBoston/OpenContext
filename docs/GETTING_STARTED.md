# Getting Started

Get your OpenContext server running in under 10 minutes.

OpenContext uses the Model Context Protocol (MCP), which connects AI assistants to external data. Your server exposes tools that AI assistants can call to search and query your open data.

## Prerequisites

- Python 3.11+
- Terraform >= 1.0 (for deployment)
- **AWS:** AWS CLI configured (`aws configure` or SSO)
- **GCP:** `gcloud` and Application Default Credentials (`gcloud auth application-default login`)

Run `opencontext authenticate` to check prerequisites for AWS (default). Use `opencontext authenticate --cloud gcp` before a GCP deploy.

---

## Installing the CLI Locally

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) if you do not have it yet.

Clone the repository and install the project plus CLI extras:

```bash
git clone https://github.com/sgarcese/OpenContext.git
cd OpenContext
uv sync --extra cli
```

`uv sync` installs the package into `.venv` from `pyproject.toml` and the lockfile (editable-style layout: local changes are used on the next run). The `cli` extra pulls in `typer`, `questionary`, and `rich`.

Verify the install (use the venv or `uv run`):

```bash
uv run opencontext --help
```

To also install development dependencies (pytest, ruff, pip-audit, etc.):

```bash
uv sync --all-extras
```

**Optional (pip-compatible install):** If you need a traditional editable install, use:

```bash
uv pip install -e ".[cli]"
```

---

## Using uv with requirements.txt

- **Daily use:** Prefer `uv sync --extra cli` or `uv sync --all-extras`. Run tools with `uv run <command>` (for example `uv run pytest`) so they use the project `.venv`.
- **Why `requirements.txt` exists:** It is used by **Lambda deployment** (`opencontext deploy` bundles dependencies with `uv pip install … -r requirements.txt`) and by **CI** for vulnerability scanning (`uv run pip-audit -r requirements.txt`). You do not need `pip install -r requirements.txt` for normal local development unless you are reproducing those exact flows.

---

## Quick Path: Local Testing

Test the server locally before deploying.

### 1. Configure Your Plugin

Run the interactive wizard:

```bash
opencontext configure
```

This walks you through selecting a plugin, setting the data source URL, and cloud settings (AWS by default; use `--cloud gcp` for GCP). It writes `config.yaml` and `terraform/<cloud>/<env>.tfvars`.

If you prefer to configure manually, copy the template and edit it:

```bash
cp config-example.yaml config.yaml
```

For CKAN:

```yaml
plugins:
  ckan:
    enabled: true
    base_url: "https://data.yourcity.gov"
    portal_url: "https://data.yourcity.gov"
    city_name: "Your City"
    timeout: 120
```

Each deployment connects to one data source. To connect another source, deploy a separate server. See [Architecture](ARCHITECTURE.md) for details.

### 2. Start the Local Server

```bash
opencontext serve
```

The server runs at `http://localhost:8000/mcp`. Keep this terminal open.

### 3. Connect via Claude Connectors

Connect using **Claude Connectors** (same steps on both Claude.ai and Claude Desktop):

1. Go to **Settings** → **Connectors** (or **Customize** → **Connectors** on claude.ai)
2. Click **Add custom connector**
3. Enter a name (e.g. "OpenContext Local") and URL: `http://localhost:8000/mcp`

**Note:** Local servers (`localhost`) only work with Claude Desktop, since the connection runs from your machine. For Claude.ai (web), use the MCP Inspector or deploy to production first (see below).

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

1. Fork the [OpenContext repository](https://github.com/sgarcese/OpenContext)
2. Clone your fork
3. Check prerequisites:

```bash
opencontext authenticate
```

4. Run the configuration wizard:

```bash
opencontext configure
```

This prompts for your organization name, city, plugin, AWS region, Lambda name, and optional custom domain. It creates `config.yaml`, the Terraform `.tfvars` file, and initializes the Terraform workspace.

### 2. Deploy

**AWS (default):**

```bash
opencontext deploy --env staging
```

**GCP:**

```bash
opencontext configure --cloud gcp   # if not done yet
opencontext deploy --cloud gcp --env staging
```

The command validates config, packages code, runs `terraform plan`, asks for confirmation, then applies. On success you get a connector URL:

- **AWS:** `api_gateway_url` (e.g. `https://xxx.execute-api.us-east-1.amazonaws.com/staging/mcp`)
- **GCP:** `mcp_endpoint_url` (Cloud Functions HTTPS URL ending in `/mcp`)

See [Deployment](DEPLOYMENT.md) for permissions, bootstrap, monitoring, and costs per cloud.

### 3. Connect via Claude Connectors (Production)

Connect using **Claude Connectors** (same steps on both Claude.ai and Claude Desktop):

1. Go to **Settings** → **Connectors** (or **Customize** → **Connectors** on claude.ai)
2. Click **Add custom connector**
3. Enter a name (e.g. "Your City OpenData") and your deployment URL (API Gateway on AWS, `mcp_endpoint_url` on GCP)

To retrieve the URL later:

```bash
opencontext status --env staging
opencontext status --cloud gcp --env staging
```

Or from Terraform:

```bash
cd terraform/aws && terraform output -raw api_gateway_url
cd terraform/gcp && terraform output -raw mcp_endpoint_url
```

Outputs include the `/mcp` path. Use that URL for all testing and production traffic.

### 4. Updating

To update config or code: edit `config.yaml` or your code, then run:

```bash
opencontext deploy --env staging
```

---

## CLI Reference

See [CLI Guide](CLI.md) for full flag documentation.

| Command | Description |
|---------|-------------|
| `opencontext authenticate [--cloud aws\|gcp]` | Check prerequisites for the selected cloud |
| `opencontext configure [--cloud aws\|gcp]` | Wizard: `config.yaml`, `terraform/<cloud>/*.tfvars`, workspace |
| `opencontext serve` | Start local dev server at `http://localhost:8000/mcp` (no cloud account required) |
| `opencontext deploy [--cloud aws\|gcp] --env <env>` | Package artifact, plan, confirm, deploy |
| `opencontext status [--cloud aws\|gcp] --env <env>` | Deployment status and endpoint URLs |
| `opencontext validate [--cloud aws\|gcp] --env <env>` | Pre-deployment checks without deploying |
| `opencontext test --env <env>` | Test the deployed MCP server endpoints |
| `opencontext logs [--cloud aws\|gcp] --env <env>` | Tail logs (CloudWatch or `gcloud functions logs`) |
| `opencontext domain --env <env>` | Check custom domain and certificate status |
| `opencontext architecture` | Show AWS architecture diagram in the terminal |
| `opencontext plugin list` | List all plugins and their enabled/disabled status |
| `opencontext security` | Run a pip-audit vulnerability scan (`--export` to save report) |
| `opencontext cost --env <env>` | Estimate AWS costs from CloudWatch metrics (`--days` to adjust window) |
| `opencontext upgrade` | Merge updates from the upstream OpenContext template |
| `opencontext destroy [--cloud aws\|gcp] --env <env>` | Tear down deployed resources for that cloud |

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "Multiple Plugins Enabled" | Enable only one plugin in `config.yaml` |
| Claude can't connect | Verify URL includes `/mcp`, check connector is enabled in the chat |
| Lambda 500 error | Check CloudWatch logs: `opencontext logs --env staging` |
| Plugin init fails | Check API URLs, keys, and network connectivity |
| Missing `.tfvars` file | Run `opencontext configure` to generate it |

---

## Next Steps

- [CLI Reference](CLI.md) — All commands and flags in detail
- [Architecture](ARCHITECTURE.md) — System design, built-in plugins, custom plugins
- [Built-in Plugins](BUILT_IN_PLUGINS.md) — CKAN, ArcGIS Hub, and Socrata tool reference
- [Deployment](DEPLOYMENT.md) — AWS & GCP (`--cloud`), monitoring, cost
- [Testing](TESTING.md) — Local testing (Terminal, Claude, MCP Inspector)

---

## Support

[GitHub Issues](https://github.com/sgarcese/OpenContext/issues)

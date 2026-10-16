# OpenContext — Claude Code Guide

Extensible MCP (Model Context Protocol) framework for civic data platforms. Connects AI assistants to public open data sources (CKAN, ArcGIS, Socrata) via a plugin system, deployed as AWS Lambda.

## Key Architectural Rule: One Fork = One MCP Server

Each repo fork runs **exactly one** plugin. `core/validators.py` and `core/plugin_manager.py` will hard-fail if 0 or 2+ plugins are enabled in `config.yaml`. This is intentional — don't work around it.

## Setup

```bash
uv sync --all-extras        # install all deps including cli + dev
cp config-example.yaml config.yaml   # then edit for your data source
pre-commit install          # set up git hooks
```

## Common Commands

```bash
# Run local dev server (http://localhost:8000/mcp)
opencontext serve

# Test the running server
opencontext test --url http://localhost:8000/mcp

# Tests
pytest tests/ -n auto --cov=core --cov=plugins --cov-fail-under=80

# Lint + format (matches CI)
ruff check core/ plugins/ server/ tests/ --fix --unsafe-fixes
ruff format core/ plugins/ server/ tests/

# CLI
opencontext validate --env staging    # validate config + Terraform before deploy
opencontext deploy --env staging
opencontext status --env staging
opencontext logs --env staging
opencontext plugin list               # list enabled/disabled plugins
opencontext security                  # pip-audit vulnerability scan
opencontext architecture              # print AWS infra diagram in terminal
```

## Project Layout

```
core/           # Framework kernel — interfaces, MCP server, plugin manager, validators
plugins/        # Built-in plugins: ckan/, arcgis/, socrata/, opendatasoft/
custom_plugins/ # Drop user plugins here — auto-discovered at startup
cli/            # Typer CLI (opencontext command)
server/         # Shared HTTP handler (http_handler.py) + cloud adapters
server/adapters/ # AWS Lambda (aws_lambda.py) and GCP Cloud Functions (gcp_functions.py) entry points
tests/          # pytest suite (80% coverage required)
terraform/aws/  # Lambda + API Gateway + IAM IaC
terraform/gcp/  # Cloud Functions gen2 + GCS + IAM IaC (see terraform/gcp/README.md)
docs/           # Architecture, deployment, plugin authoring guides
```

Key files:
- `core/interfaces.py` — `MCPPlugin` base class; implement this for any new plugin
- `core/base_plugin.py` — `BaseOpenDataPlugin`, `ToolHandler`, `HTTP_RETRY`, record rendering; built-in plugins subclass it
- `core/query_validator.py` — `BaseQueryValidator` shared by the SQL / SoQL / ODSQL / ArcGIS WHERE validators
- `core/portal_content.py` — guardrails for untrusted portal text (framing, `safe_id`, `html_to_text`); see `docs/SECURITY.md`
- `core/mcp_server.py` — JSON-RPC handler (initialize / tools/list / tools/call)
- `core/plugin_manager.py` — discovery, loading, one-plugin enforcement
- `core/validators.py` — config validation; enforces the one-plugin rule
- `server/adapters/aws_lambda.py` — Lambda entry point
- `server/adapters/gcp_functions.py` — GCP Cloud Functions gen2 entry point
- `cli/commands/serve.py` — aiohttp dev server (started via `opencontext serve`)

## Plugin System

Tools are auto-discovered and namespaced: `plugin_name__tool_name` (e.g. `ckan__search_datasets`).

To add a custom plugin:
1. Create `custom_plugins/my_plugin/plugin.py` inheriting `MCPPlugin`
2. Implement: `initialize`, `shutdown`, `get_tools`, `execute_tool`, `health_check`
3. Enable in `config.yaml` under `plugins.my_plugin.enabled: true`

See `docs/CUSTOM_PLUGINS.md` for full interface contract.

## Config (`config.yaml`)

```yaml
server_name: "My City OpenData MCP"
organization: "City of X"

plugins:
  ckan:                        # exactly ONE plugin enabled
    enabled: true
    base_url: "https://data.example.gov/"
    city_name: "Example City"
    timeout: 120

aws:
  region: "us-east-1"
  lambda_memory: 512
  lambda_timeout: 120

logging:
  level: "INFO"    # DEBUG for local dev
  format: "json"
```

`config.yaml` is gitignored. Never commit it — use `config-example.yaml` as the template.

## CI (GitHub Actions)

- **`ci.yml`**: ruff lint, pip-audit CVE scan, gofmt, pytest (80% coverage gate)
- **`infra.yml`**: terraform fmt/validate, tflint, tfsec — triggers on `terraform/**` changes
- **`release.yml`**: builds Go binaries (macOS/Linux/Windows) + Lambda ZIP on version tags

Replicate CI locally:
```bash
uv run ruff check core/ plugins/ server/ tests/
uv run pip-audit -r requirements.txt
uv run pytest tests/ -n auto --cov=core --cov=plugins --cov-fail-under=80
```

## Gotchas

- **Multiple plugins enabled** → hard crash at startup. Only one `enabled: true` allowed.
- **Tool prefix required** → call `ckan__search_datasets`, not `search_datasets`.
- **`config.yaml` is gitignored** → changes to it won't be committed. Use `config-example.yaml` for template changes.
- **Coverage < 80%** → CI fails. New code needs tests; check gaps with `--cov-report=html`.
- **Lambda size limit** → 250 MB max. `opencontext deploy` validates package size before uploading.
- **Python 3.11+ required** → match this in any new tooling or containers.
- **Go client** (`client/`) is an optional stdio-to-HTTP bridge for tools that only speak stdio MCP.

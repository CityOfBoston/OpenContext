# CLI Reference

The `opencontext` CLI manages the full lifecycle of an OpenContext MCP server — from initial setup through deployment, monitoring, and teardown.

## Installation

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) first.

```bash
git clone https://github.com/sgarcese/OpenContext.git
cd OpenContext
uv sync --extra cli
```

For development tooling (tests, linters, pip-audit), use `uv sync --all-extras`.

Verify:

```bash
uv run opencontext --help
```

## Global behavior

- Commands that modify infrastructure (`deploy`, `destroy`) require a TTY and prompt for confirmation.
- Multi-cloud commands support `--cloud aws|gcp` (default: `aws`).
- All commands that interact with Terraform respect the environment set by `--env`.
- `--env` defaults to `staging` on every command that accepts it.

---

## Commands

### `opencontext authenticate`

Check all prerequisites and print a status table. Supports cloud-specific checks via `--cloud`.

- AWS mode auto-installs `uv` and `awscli` when possible (`uv` first; pip fallback for missing tools).
- GCP mode checks for `gcloud` and Application Default Credentials (ADC).

**Checks:**
1. Python >= 3.11
2. `uv` (if missing: auto-install attempted via pip; preferred: install from [docs.astral.sh/uv](https://docs.astral.sh/uv/getting-started/installation/))
3. Cloud CLI (`aws` in AWS mode, `gcloud` in GCP mode)
4. Cloud credentials (`aws sts get-caller-identity` in AWS mode, ADC token in GCP mode)
5. Terraform >= 1.0

```bash
opencontext authenticate
opencontext authenticate --cloud gcp
```

| Flag | Default | Description |
|------|---------|-------------|
| `--cloud` | `aws` | Cloud provider (`aws` or `gcp`) |

---

### `opencontext configure`

Interactive wizard that creates `config.yaml`, the Terraform `.tfvars` file, and initializes the Terraform workspace.

**Prompts:**
- Starting point (example template or scratch)
- Organization name and city
- Environment (`staging` or `prod`)
- Plugin (CKAN, ArcGIS, or Socrata) and connection settings
- Cloud-specific settings:
  - AWS: region, Lambda name, memory (MB), timeout (seconds), optional custom domain
  - GCP: project ID, region, function name, memory (MiB), timeout (seconds), autoscaling min/max instances, optional artifact bucket

**Outputs:**
- `config.yaml` — plugin + cloud settings
- `terraform/<cloud>/<env>.tfvars` — Terraform variables
- Terraform workspace `<city-slug>-<env>` (created or selected)

```bash
opencontext configure
opencontext configure --cloud gcp
```

---

### `opencontext serve`

Start a local development server for testing without deploying to AWS. The server uses the same MCP handler as the Lambda adapter, so behavior is identical to production.

```bash
opencontext serve
opencontext serve --port 9000
opencontext serve --config path/to/config.yaml
```

| Flag | Default | Description |
|------|---------|-------------|
| `--port` | `8000` | Port to listen on |
| `--config` | `config.yaml` | Path to config file |

The server starts at `http://localhost:<port>/mcp`. Use it with Claude Desktop (via Claude Connectors), `opencontext test --url`, or any HTTP client. Logs are written to stdout at the level set in `config.yaml`.

---

### `opencontext deploy`

Package the deployment artifact, run `terraform plan`, show a summary, prompt for confirmation, then apply.

```bash
opencontext deploy --env staging
opencontext deploy --env prod
opencontext deploy --cloud gcp --env staging
```

| Flag | Default | Description |
|------|---------|-------------|
| `--env` | `staging` | Environment to deploy to |
| `--cloud` | `aws` | Cloud provider (`aws` or `gcp`) |

**What it does:**
1. Runs all validation checks (same as `opencontext validate`)
2. Installs Python dependencies into `.deploy/` using `uv pip install -r requirements.txt`
3. Copies `core/`, `plugins/`, `server/`, and `custom_plugins/` into the zip
4. Copies deployment artifacts into `terraform/<cloud>/`
5. Runs `terraform plan` and shows add/change/destroy counts
6. Prompts for confirmation (defaults to No)
7. Runs `terraform apply`
8. Prints provider-specific outputs (`api_gateway_url` / `mcp_endpoint_url`, logs/resources)

After deployment, use the printed connector URL with Claude Connectors.

---

### `opencontext status`

Show deployment status for an environment with provider-specific runtime and endpoint details.

```bash
opencontext status --env staging
opencontext status --env prod
opencontext status --cloud gcp --env staging
```

| Flag | Default | Description |
|------|---------|-------------|
| `--env` | `staging` | Environment to query |
| `--cloud` | `aws` | Cloud provider (`aws` or `gcp`) |

---

### `opencontext validate`

Run pre-deployment validation checks without deploying. Useful for CI or before a deploy.

```bash
opencontext validate --env staging
opencontext validate --cloud gcp --env staging
```

| Flag | Default | Description |
|------|---------|-------------|
| `--env` | `staging` | Environment to validate against |
| `--cloud` | `aws` | Cloud provider (`aws` or `gcp`) |

**Checks:**
1. `config.yaml` exists
2. Exactly one plugin enabled
3. Plugin required fields present
4. `terraform/<cloud>/<env>.tfvars` exists
5. Terraform installed
6. Terraform initialized (`.terraform/` directory present)
7. `terraform validate` passes
8. Provider credentials valid (`aws sts ...` for AWS, ADC token for GCP)
9. Provider-specific checks (ACM/domain checks for AWS, `project_id` for GCP)

Exits with code 1 if any check fails.

---

### `opencontext test`

Send MCP JSON-RPC requests to the deployed server and report results.

```bash
opencontext test --env staging
opencontext test --url https://my-lambda-url.lambda-url.us-east-1.on.aws
```

| Flag | Default | Description |
|------|---------|-------------|
| `--env` | `staging` | Environment to test (fetches URL from Terraform output) |
| `--url` | — | Override URL to test against (skips Terraform lookup) |

**Tests run:**
1. Ping
2. Initialize (MCP protocol handshake)
3. List tools
4. Call first available tool with empty arguments

If a custom domain is configured and its certificate is `ISSUED`, the command also tests against the custom domain URL.

---

### `opencontext logs`

Tail runtime logs for the deployed function.

```bash
opencontext logs --env staging
opencontext logs --env staging --follow
opencontext logs --env staging --verbose
opencontext logs --env staging --since 30m
opencontext logs --cloud gcp --env staging
```

| Flag | Default | Description |
|------|---------|-------------|
| `--env` | `staging` | Environment to fetch logs for |
| `--cloud` | `aws` | Cloud provider (`aws` or `gcp`) |
| `--follow`, `-f` | False | Stream new log entries as they arrive |
| `--verbose`, `-v` | False | Show structured per-invocation view with duration and error highlighting |
| `--since` | `1h` | How far back to fetch (e.g., `30m`, `2h`, `24h`) |

AWS supports `--verbose` parsing/highlights for CloudWatch logs. GCP uses `gcloud functions logs read` output.

---

### `opencontext domain`

Check and manage custom domain setup. Shows certificate status, DNS records to create, and (if the certificate is issued) tests the domain is live.

```bash
opencontext domain --env staging
opencontext domain --env prod
```

| Flag | Default | Description |
|------|---------|-------------|
| `--env` | `staging` | Environment to check |

When the certificate is `PENDING_VALIDATION`, this command prints the two CNAME records that city IT needs to create, plus a pre-filled email template to send them.

---

### `opencontext architecture`

Print a human-readable overview of the AWS infrastructure in the terminal — request flow, API Gateway settings, Lambda config, supporting services (CloudWatch, SQS DLQ, Terraform state), custom domain, and resource tagging.

```bash
opencontext architecture
```

---

### `opencontext plugin list`

List all built-in and custom plugins and their enabled/disabled status from `config.yaml`.

```bash
opencontext plugin list
```

---

### `opencontext security`

Run a `pip-audit` vulnerability scan against installed packages and display results grouped by severity (CRITICAL, HIGH, MEDIUM, LOW). Exits with code 1 if any vulnerabilities are found.

```bash
opencontext security
opencontext security --export
```

| Flag | Default | Description |
|------|---------|-------------|
| `--export` | False | Write report to `security-report-<timestamp>.txt` |

Requires `pip-audit` to be installed as a dev dependency (`uv add --dev pip-audit`).

---

### `opencontext cost`

Estimate AWS costs for an environment based on CloudWatch metrics.

```bash
opencontext cost --env staging
opencontext cost --env prod --days 7
```

| Flag | Default | Description |
|------|---------|-------------|
| `--env` | `staging` | Environment to estimate costs for |
| `--days` | `30` | Number of days to look back |

Reports Lambda invocations, average duration, API Gateway request count, and estimated costs. Uses AWS public pricing — check AWS Cost Explorer for exact figures.

To break costs down by tag in Cost Explorer, activate the `Project`, `Environment`, and `ManagedBy` tags in AWS Console → Billing → Cost allocation tags.

---

### `opencontext upgrade`

Merge updates from the upstream OpenContext template into your fork.

```bash
opencontext upgrade
opencontext upgrade --upstream-url https://github.com/sgarcese/OpenContext.git
```

| Flag | Default | Description |
|------|---------|-------------|
| `--upstream-url` | upstream repo URL | URL of the upstream template repository |

**What it does:**
1. Adds an `upstream` git remote if not present
2. Fetches `upstream/main`
3. Shows new commits and affected files
4. Warns about protected files (`config.yaml`, `terraform/aws/*.tfvars`, `examples/`) that will not be overwritten
5. Prompts for confirmation, then runs `git merge upstream/main --no-commit --no-ff`
6. Auto-resolves conflicts in protected files by keeping your version
7. Leaves the merge staged — run `git commit` to finalize or `git merge --abort` to cancel

---

### `opencontext destroy`

Tear down all resources for an environment in the selected cloud. Requires typing the environment name to confirm.

```bash
opencontext destroy --env staging
opencontext destroy --env prod
opencontext destroy --cloud gcp --env staging
```

| Flag | Default | Description |
|------|---------|-------------|
| `--env` | `staging` | Environment to destroy |
| `--cloud` | `aws` | Cloud provider (`aws` or `gcp`) |

Runs `terraform destroy -auto-approve` after confirmation. This is irreversible.

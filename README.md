# OpenContext

<p align="center">
  <img src="docs/opencontext_logo.png" alt="OpenContext Logo" width="400">
</p>

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![MCP Compatible](https://img.shields.io/badge/MCP-Compatible-green.svg)](https://modelcontextprotocol.io/)

---

## Quick Start

```bash
# 1. Configure (create config, enable one data source)
cp config-example.yaml config.yaml
# Edit config.yaml - set enabled: true for one plugin

# 2. Test locally
pip install aiohttp
python3 scripts/local_server.py

# 3. Deploy
./scripts/deploy.sh
```

Add to Claude Desktop (`~/Library/Application Support/Claude/claude_desktop_config.json`):

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

Get the URL from: `cd terraform/aws && terraform output -raw api_gateway_url`

See [Getting Started](docs/GETTING_STARTED.md) for full setup.

---

## Documentation


| Doc                                        | Description                                     |
| ------------------------------------------ | ----------------------------------------------- |
| [Getting Started](docs/GETTING_STARTED.md) | Setup and usage                                 |
| [Architecture](docs/ARCHITECTURE.md)       | System design and plugins                       |
| [Deployment](docs/DEPLOYMENT.md)           | AWS, Terraform, monitoring                      |
| [Testing](docs/TESTING.md)                 | Local testing (Terminal, Claude, MCP Inspector) |


---

## Examples

- **Boston OpenData (CKAN):** [examples/boston-opendata/config.yaml](examples/boston-opendata/config.yaml)
- **Custom plugin:** [examples/custom-plugin/](examples/custom-plugin/)

---

## Contributing

Pre-commit hooks (optional):

```bash
pip install pre-commit
pre-commit install
```

Hooks: Ruff, yamllint, gofmt. Run manually: `pre-commit run --all-files`.

---

## License

MIT — see [LICENSE](LICENSE).

**Author:** Srihari Raman, City of Boston Department of Innovation and Technology

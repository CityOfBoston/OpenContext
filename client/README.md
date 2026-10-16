# OpenContext Client

Stdio client for connecting Claude Desktop to OpenContext MCP servers.

## Installation

### Download Binary

Download the latest binary for your platform from the [Releases](https://github.com/sgarcese/OpenContext/releases) page. Binaries are published when a version tag is pushed; if no release is listed yet, [build from source](#build-from-source).

- macOS (Intel): `opencontext-client-darwin-amd64`
- macOS (Apple Silicon): `opencontext-client-darwin-arm64`
- Linux (Intel): `opencontext-client-linux-amd64`
- Windows: `opencontext-client-windows-amd64.exe`

Make the binary executable:

```bash
chmod +x opencontext-client-darwin-arm64
mv opencontext-client-darwin-arm64 opencontext-client
```

### Build from Source

Requirements: Go 1.21+

**Build for your platform:**

```bash
cd client
make build
```

**Build for all platforms (cross-compilation):**

```bash
cd client
make build-all
```

This produces binaries for macOS (Intel/ARM), Linux (amd64/arm64), and Windows (amd64).

## Usage

### Connecting to Claude

**Recommended: Claude Connectors** (same on Claude.ai and Claude Desktop)

1. Go to **Settings** → **Connectors** (or **Customize** → **Connectors** on claude.ai)
2. Click **Add custom connector**
3. Enter a name and your API Gateway or Lambda URL (e.g. `https://xxx.execute-api.us-east-1.amazonaws.com/staging/mcp`)

**Alternative: Go client with Claude Desktop config**

If you prefer the config file approach, add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "my-mcp-server": {
      "command": "/path/to/opencontext-client",
      "args": ["https://your-lambda-url.lambda-url.us-east-1.on.aws"]
    }
  }
}
```

The client automatically appends `/mcp` to the Lambda URL.

### Command Line

```bash
# Using Lambda URL as argument
./opencontext-client https://your-lambda-url.lambda-url.us-east-1.on.aws

# Using environment variable
export OPENCONTEXT_LAMBDA_URL=https://your-lambda-url.lambda-url.us-east-1.on.aws
./opencontext-client
```

## Environment Variables

- `OPENCONTEXT_LAMBDA_URL`: Lambda Function URL (required if not provided as argument)
- `OPENCONTEXT_TIMEOUT`: HTTP request timeout in seconds (default: 30)

## How It Works

The client reads MCP JSON-RPC messages from stdin and forwards them to the Lambda Function URL's `/mcp` endpoint via HTTP POST. Responses are written to stdout in the same JSON-RPC format.

This allows Claude Desktop to communicate with OpenContext MCP servers running on AWS Lambda using the stdio transport protocol, bridging it to HTTP.

**Note:** The recommended way to connect is via [Claude Connectors](https://claude.ai/customize/connectors) (works on both Claude.ai and Claude Desktop). The Go client is an alternative for the config file approach.

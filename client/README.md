# OpenContext Client

Stdio client for connecting Claude Desktop to OpenContext MCP servers.

## Installation

### Download Binary

Download the latest binary for your platform from the [Releases](https://github.com/thealphacubicle/OpenContext/releases) page.

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

### With Claude Desktop

Add to your Claude Desktop configuration file:

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

**Note:** The client automatically appends `/mcp` to the Lambda URL. You can provide the base Lambda URL (without `/mcp`) or the full URL with `/mcp` - both work.

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

**Note:** OpenContext also supports Streamable HTTP transport directly (no binary needed). See the main README for both connection options.

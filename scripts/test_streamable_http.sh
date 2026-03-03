#!/bin/bash
# Test script for Streamable HTTP transport MCP server
# Tests the full MCP lifecycle: initialize, list tools, call tool

set -e

# Check if jq is installed
if ! command -v jq &> /dev/null; then
  echo "Error: jq is required but not installed."
  echo "Install with: brew install jq (macOS) or apt-get install jq (Linux)"
  exit 1
fi

BASE_URL="${1:-http://localhost:8000/mcp}"

echo "=========================================="
echo "Testing OpenContext MCP Server"
echo "=========================================="
echo "Base URL: $BASE_URL"
echo ""

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Step 1: Initialize and extract session ID
echo -e "${BLUE}Step 1: Initialize MCP connection${NC}"
INIT_REQUEST='{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {
    "protocolVersion": "2025-03-26",
    "capabilities": {},
    "clientInfo": {
      "name": "test-client",
      "version": "1.0.0"
    }
  }
}'

echo "Request:"
echo "$INIT_REQUEST" | jq '.'
echo ""

# Use temp files to capture response
TEMP_HEADERS=$(mktemp)
TEMP_BODY=$(mktemp)

# Get response with headers separated
HTTP_CODE=$(curl -s -o "$TEMP_BODY" -w "%{http_code}" -X POST "$BASE_URL" \
  -H "Content-Type: application/json" \
  -d "$INIT_REQUEST" \
  -D "$TEMP_HEADERS")

# Extract session ID from headers (case-insensitive)
SESSION_ID=$(grep -i "mcp-session-id" "$TEMP_HEADERS" | head -n 1 | sed -E 's/^[^:]*:[[:space:]]*(.*)$/\1/' | tr -d '\r' | tr -d '\n' || echo "")

# Body is in TEMP_BODY
BODY=$(cat "$TEMP_BODY")

if [ "$HTTP_CODE" != "200" ]; then
  echo -e "${YELLOW}Error: HTTP $HTTP_CODE${NC}"
  echo "$BODY" | jq '.' 2>/dev/null || echo "$BODY"
  rm -f "$TEMP_RESPONSE" "$TEMP_HEADERS"
  exit 1
fi

echo "Response:"
echo "$BODY" | jq '.'
echo ""

rm -f "$TEMP_RESPONSE" "$TEMP_HEADERS"

if [ -z "$SESSION_ID" ]; then
  echo -e "${YELLOW}Warning: Could not extract session ID from headers${NC}"
  echo "Continuing without session ID..."
else
  echo -e "${GREEN}Session ID: $SESSION_ID${NC}"
  echo ""
fi

# Step 2: List tools using session ID
echo -e "${BLUE}Step 2: List available tools${NC}"
LIST_REQUEST='{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/list"
}'

echo "Request:"
echo "$LIST_REQUEST" | jq '.'
echo ""

if [ -n "$SESSION_ID" ]; then
  LIST_RESPONSE=$(curl -s -X POST "$BASE_URL" \
    -H "Content-Type: application/json" \
    -H "Mcp-Session-Id: $SESSION_ID" \
    -d "$LIST_REQUEST")
else
  LIST_RESPONSE=$(curl -s -X POST "$BASE_URL" \
    -H "Content-Type: application/json" \
    -d "$LIST_REQUEST")
fi

echo "Response:"
echo "$LIST_RESPONSE" | jq '.'
echo ""

# Extract tool names
TOOL_COUNT=$(echo "$LIST_RESPONSE" | jq '.result.tools | length')
echo -e "${GREEN}Found $TOOL_COUNT tools${NC}"
echo ""

# Step 3: Call a tool (ckan__search_datasets)
echo -e "${BLUE}Step 3: Call tool (ckan__search_datasets)${NC}"
CALL_REQUEST='{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "tools/call",
  "params": {
    "name": "ckan__search_datasets",
    "arguments": {
      "query": "traffic",
      "limit": 5
    }
  }
}'

echo "Request:"
echo "$CALL_REQUEST" | jq '.'
echo ""

if [ -n "$SESSION_ID" ]; then
  CALL_RESPONSE=$(curl -s -X POST "$BASE_URL" \
    -H "Content-Type: application/json" \
    -H "Mcp-Session-Id: $SESSION_ID" \
    -d "$CALL_REQUEST")
else
  CALL_RESPONSE=$(curl -s -X POST "$BASE_URL" \
    -H "Content-Type: application/json" \
    -d "$CALL_REQUEST")
fi

echo "Response:"
echo "$CALL_RESPONSE" | jq '.'
echo ""

# Check for errors
ERROR=$(echo "$CALL_RESPONSE" | jq -r '.error // empty')
if [ -n "$ERROR" ]; then
  echo -e "${YELLOW}Tool call returned an error:${NC}"
  echo "$CALL_RESPONSE" | jq '.error'
  exit 1
else
  echo -e "${GREEN}Tool call successful!${NC}"
fi

echo ""
echo "=========================================="
echo -e "${GREEN}All tests passed!${NC}"
echo "=========================================="

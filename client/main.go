package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"net/http"
	"os"
	"strconv"
	"strings"
	"time"
)

// JSONRPCRequest represents a JSON-RPC 2.0 request
type JSONRPCRequest struct {
	JSONRPC string      `json:"jsonrpc"`
	ID      interface{} `json:"id"`
	Method  string      `json:"method"`
	Params  interface{} `json:"params,omitempty"`
}

// JSONRPCError represents a JSON-RPC 2.0 error
type JSONRPCError struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
	Data    string `json:"data,omitempty"`
}

// JSONRPCResponse represents a JSON-RPC 2.0 response
type JSONRPCResponse struct {
	JSONRPC string        `json:"jsonrpc"`
	ID      interface{}   `json:"id"`
	Result  interface{}   `json:"result,omitempty"`
	Error   *JSONRPCError `json:"error,omitempty"`
}

// Client handles the communication between stdin/stdout and the Lambda URL
type Client struct {
	lambdaURL string
	client    *http.Client
}

// NewClient creates a new Client instance
func NewClient(lambdaURL string, timeout time.Duration) *Client {
	// Ensure URL ends with /mcp endpoint
	url := strings.TrimRight(lambdaURL, "/")
	if !strings.HasSuffix(url, "/mcp") {
		url = url + "/mcp"
	}
	return &Client{
		lambdaURL: url,
		client: &http.Client{
			Timeout: timeout,
		},
	}
}

// HandleRequest processes a single JSON-RPC request
// Returns nil for notifications (no response should be sent)
func (c *Client) HandleRequest(req *JSONRPCRequest) *JSONRPCResponse {
	// Marshal request
	reqJSON, err := json.Marshal(req)
	if err != nil {
		// Don't send error response for notifications
		if req.ID == nil {
			return nil
		}
		return &JSONRPCResponse{
			JSONRPC: "2.0",
			ID:      req.ID,
			Error: &JSONRPCError{
				Code:    -32700, // Parse error
				Message: "Parse error",
				Data:    err.Error(),
			},
		}
	}

	// Create HTTP request
	httpReq, err := http.NewRequest("POST", c.lambdaURL, bytes.NewBuffer(reqJSON))
	if err != nil {
		// Don't send error response for notifications
		if req.ID == nil {
			return nil
		}
		return &JSONRPCResponse{
			JSONRPC: "2.0",
			ID:      req.ID,
			Error: &JSONRPCError{
				Code:    -32603, // Internal error
				Message: "Internal error",
				Data:    err.Error(),
			},
		}
	}
	httpReq.Header.Set("Content-Type", "application/json")

	// Add API key header if API_KEY environment variable is set
	if apiKey := os.Getenv("API_KEY"); apiKey != "" {
		httpReq.Header.Set("x-api-key", apiKey)
	}

	// Send request
	resp, err := c.client.Do(httpReq)
	if err != nil {
		// Don't send error response for notifications
		if req.ID == nil {
			return nil
		}
		return &JSONRPCResponse{
			JSONRPC: "2.0",
			ID:      req.ID,
			Error: &JSONRPCError{
				Code:    -32603, // Internal error (HTTP error in Python client)
				Message: "HTTP error",
				Data:    err.Error(),
			},
		}
	}
	defer resp.Body.Close()

	// Check status code
	if resp.StatusCode >= 400 {
		// Don't send error response for notifications
		if req.ID == nil {
			return nil
		}
		return &JSONRPCResponse{
			JSONRPC: "2.0",
			ID:      req.ID,
			Error: &JSONRPCError{
				Code:    -32603, // Internal error
				Message: "HTTP error",
				Data:    fmt.Sprintf("Status: %d", resp.StatusCode),
			},
		}
	}

	// Check if response body is empty (for notifications)
	bodyBytes, err := io.ReadAll(resp.Body)
	if err != nil {
		// Don't send error response for notifications
		if req.ID == nil {
			return nil
		}
		return &JSONRPCResponse{
			JSONRPC: "2.0",
			ID:      req.ID,
			Error: &JSONRPCError{
				Code:    -32603, // Internal error
				Message: "Failed to read response body",
				Data:    err.Error(),
			},
		}
	}

	// If body is empty and this is a notification, return nil (no response)
	if len(bodyBytes) == 0 {
		if req.ID == nil {
			return nil
		}
		return &JSONRPCResponse{
			JSONRPC: "2.0",
			ID:      req.ID,
			Error: &JSONRPCError{
				Code:    -32603, // Internal error
				Message: "Empty response body",
			},
		}
	}

	// Parse response
	var jsonResp JSONRPCResponse
	if err := json.Unmarshal(bodyBytes, &jsonResp); err != nil {
		// Don't send error response for notifications
		if req.ID == nil {
			return nil
		}
		return &JSONRPCResponse{
			JSONRPC: "2.0",
			ID:      req.ID,
			Error: &JSONRPCError{
				Code:    -32603, // Internal error
				Message: "Invalid JSON response from server",
				Data:    fmt.Sprintf("Failed to parse response: %s", err.Error()),
			},
		}
	}

	return &jsonResp
}

// Run starts the client loop
func (c *Client) Run() error {
	scanner := bufio.NewScanner(os.Stdin)

	// Set a large buffer size for long lines if needed, but default is usually fine (64k)
	// We'll stick to default for now as it matches Python's line reading

	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}

		var req JSONRPCRequest
		if err := json.Unmarshal([]byte(line), &req); err != nil {
			resp := &JSONRPCResponse{
				JSONRPC: "2.0",
				ID:      nil,
				Error: &JSONRPCError{
					Code:    -32700, // Parse error
					Message: "Parse error",
					Data:    err.Error(),
				},
			}
			respJSON, _ := json.Marshal(resp)
			fmt.Println(string(respJSON))
			os.Stdout.Sync() // Ensure immediate flush for Claude Desktop
			continue
		}

		resp := c.HandleRequest(&req)
		// Don't send response for notifications (nil response)
		if resp == nil {
			continue
		}
		respJSON, _ := json.Marshal(resp)
		fmt.Println(string(respJSON))
		os.Stdout.Sync() // Ensure immediate flush for Claude Desktop
	}

	if err := scanner.Err(); err != nil && err != io.EOF {
		return err
	}

	return nil
}

func main() {
	// Parse flags
	flag.Parse()
	args := flag.Args()

	// Get Lambda URL
	var lambdaURL string
	if len(args) > 0 {
		lambdaURL = args[0]
	} else {
		lambdaURL = os.Getenv("OPENCONTEXT_LAMBDA_URL")
	}

	if lambdaURL == "" {
		fmt.Fprintf(os.Stderr, "Error: Lambda URL required\n")
		fmt.Fprintf(os.Stderr, "Usage: opencontext-client <lambda_url>\n")
		fmt.Fprintf(os.Stderr, "Or set OPENCONTEXT_LAMBDA_URL environment variable\n")
		os.Exit(1)
	}

	// Get timeout
	timeout := 30
	if timeoutStr := os.Getenv("OPENCONTEXT_TIMEOUT"); timeoutStr != "" {
		if t, err := strconv.Atoi(timeoutStr); err == nil && t > 0 {
			timeout = t
		} else {
			fmt.Fprintf(os.Stderr, "Error: Invalid OPENCONTEXT_TIMEOUT value '%s'. Must be a positive integer.\n", timeoutStr)
			os.Exit(1)
		}
	}

	client := NewClient(lambdaURL, time.Duration(timeout)*time.Second)
	if err := client.Run(); err != nil {
		fmt.Fprintf(os.Stderr, "Error: %v\n", err)
		os.Exit(1)
	}
}

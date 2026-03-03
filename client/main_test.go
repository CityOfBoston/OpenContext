package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestNewClient(t *testing.T) {
	url := "http://example.com/"
	client := NewClient(url, 10*time.Second)

	// NewClient appends /mcp if not present (MCP protocol endpoint)
	if client.lambdaURL != "http://example.com/mcp" {
		t.Errorf("Expected lambdaURL to be 'http://example.com/mcp', got '%s'", client.lambdaURL)
	}
	if client.client.Timeout != 10*time.Second {
		t.Errorf("Expected timeout to be 10s, got %v", client.client.Timeout)
	}
}

func TestHandleRequest_Success(t *testing.T) {
	// Mock server
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Check request
		if r.Method != "POST" {
			t.Errorf("Expected method POST, got %s", r.Method)
		}
		if r.Header.Get("Content-Type") != "application/json" {
			t.Errorf("Expected Content-Type application/json, got %s", r.Header.Get("Content-Type"))
		}

		var req JSONRPCRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			t.Errorf("Failed to decode request: %v", err)
		}
		if req.Method != "test" {
			t.Errorf("Expected method 'test', got '%s'", req.Method)
		}

		// Send response
		resp := JSONRPCResponse{
			JSONRPC: "2.0",
			ID:      req.ID,
			Result:  "success",
		}
		json.NewEncoder(w).Encode(resp)
	}))
	defer server.Close()

	client := NewClient(server.URL, 1*time.Second)
	req := &JSONRPCRequest{
		JSONRPC: "2.0",
		ID:      1,
		Method:  "test",
	}

	resp := client.HandleRequest(req)

	if resp.Error != nil {
		t.Errorf("Expected no error, got %v", resp.Error)
	}
	if resp.Result != "success" {
		t.Errorf("Expected result 'success', got %v", resp.Result)
	}
	if resp.ID != 1.0 { // JSON numbers are floats
		t.Errorf("Expected ID 1, got %v", resp.ID)
	}
}

func TestHandleRequest_HTTPError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
	}))
	defer server.Close()

	client := NewClient(server.URL, 1*time.Second)
	req := &JSONRPCRequest{
		JSONRPC: "2.0",
		ID:      1,
		Method:  "test",
	}

	resp := client.HandleRequest(req)

	if resp.Error == nil {
		t.Error("Expected error, got nil")
	}
	if resp.Error.Code != -32603 {
		t.Errorf("Expected error code -32603, got %d", resp.Error.Code)
	}
	if resp.Error.Message != "HTTP error" {
		t.Errorf("Expected error message 'HTTP error', got '%s'", resp.Error.Message)
	}
}

func TestHandleRequest_InvalidJSONResponse(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte("invalid json"))
	}))
	defer server.Close()

	client := NewClient(server.URL, 1*time.Second)
	req := &JSONRPCRequest{
		JSONRPC: "2.0",
		ID:      1,
		Method:  "test",
	}

	resp := client.HandleRequest(req)

	if resp.Error == nil {
		t.Error("Expected error, got nil")
	}
	if resp.Error.Code != -32603 {
		t.Errorf("Expected error code -32603, got %d", resp.Error.Code)
	}
	if resp.Error.Message != "Invalid JSON response from server" {
		t.Errorf("Expected error message 'Invalid JSON response from server', got '%s'", resp.Error.Message)
	}
}

func TestHandleRequest_ConnectionError(t *testing.T) {
	// Client pointing to closed port
	client := NewClient("http://127.0.0.1:12345", 100*time.Millisecond)
	req := &JSONRPCRequest{
		JSONRPC: "2.0",
		ID:      1,
		Method:  "test",
	}

	resp := client.HandleRequest(req)

	if resp.Error == nil {
		t.Error("Expected error, got nil")
	}
	if resp.Error.Code != -32603 {
		t.Errorf("Expected error code -32603, got %d", resp.Error.Code)
	}
	if resp.Error.Message != "HTTP error" {
		t.Errorf("Expected error message 'HTTP error', got '%s'", resp.Error.Message)
	}
}

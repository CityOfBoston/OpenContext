"""Comprehensive tests for AWS Lambda adapter.

These tests verify Lambda event transformation, error handling,
and integration with UniversalHTTPHandler.
"""

import json
from unittest.mock import patch, MagicMock, AsyncMock

from server.adapters.aws_lambda import lambda_handler, get_handler


class MockLambdaContext:
    """Mock Lambda context object."""

    def __init__(self, request_id="test-request-id-123"):
        self.aws_request_id = request_id
        self.function_name = "test-function"
        self.memory_limit_in_mb = 512


class TestLambdaHandler:
    """Test lambda_handler function."""

    def test_lambda_handler_with_function_url_event(self):
        """Test Lambda handler with Function URL event format."""
        event = {
            "requestContext": {
                "http": {
                    "method": "POST",
                    "path": "/mcp",
                }
            },
            "rawPath": "/mcp",
            "body": json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "ping",
                    "params": {},
                }
            ),
            "headers": {
                "Content-Type": "application/json",
            },
        }
        context = MockLambdaContext()

        with patch("server.adapters.aws_lambda.get_handler") as mock_get_handler:
            mock_handler = MagicMock()
            mock_handler.handle_request = AsyncMock(
                return_value=(
                    200,
                    {"Content-Type": "application/json"},
                    json.dumps({"result": "success"}),
                )
            )
            mock_get_handler.return_value = mock_handler

            response = lambda_handler(event, context)

            assert response["statusCode"] == 200
            assert response["headers"]["Content-Type"] == "application/json"
            mock_handler.handle_request.assert_called_once()
            call_args = mock_handler.handle_request.call_args
            assert call_args[1]["method"] == "POST"
            assert call_args[1]["path"] == "/mcp"
            assert call_args[1]["request_id"] == "test-request-id-123"

    def test_lambda_handler_with_api_gateway_event(self):
        """Test Lambda handler with API Gateway event format."""
        event = {
            "httpMethod": "POST",
            "path": "/mcp",
            "body": json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "ping",
                    "params": {},
                }
            ),
            "headers": {
                "Content-Type": "application/json",
            },
        }
        context = MockLambdaContext()

        with patch("server.adapters.aws_lambda.get_handler") as mock_get_handler:
            mock_handler = MagicMock()
            mock_handler.handle_request = AsyncMock(
                return_value=(
                    200,
                    {"Content-Type": "application/json"},
                    json.dumps({"result": "success"}),
                )
            )
            mock_get_handler.return_value = mock_handler

            response = lambda_handler(event, context)

            assert response["statusCode"] == 200
            mock_handler.handle_request.assert_called_once()
            call_args = mock_handler.handle_request.call_args
            assert call_args[1]["method"] == "POST"
            assert call_args[1]["path"] == "/mcp"

    def test_lambda_handler_with_options_request(self):
        """Test Lambda handler with OPTIONS request (CORS preflight)."""
        event = {
            "requestContext": {
                "http": {
                    "method": "OPTIONS",
                    "path": "/mcp",
                }
            },
            "rawPath": "/mcp",
            "body": "",
            "headers": {},
        }
        context = MockLambdaContext()

        with patch("server.adapters.aws_lambda.get_handler") as mock_get_handler:
            mock_handler = MagicMock()
            mock_handler.handle_options = MagicMock(
                return_value=(
                    200,
                    {"Access-Control-Allow-Origin": "*"},
                    "",
                )
            )
            mock_get_handler.return_value = mock_handler

            response = lambda_handler(event, context)

            assert response["statusCode"] == 200
            mock_handler.handle_options.assert_called_once()

    def test_lambda_handler_converts_dict_body_to_json(self):
        """Test that dict body is converted to JSON string."""
        event = {
            "requestContext": {
                "http": {
                    "method": "POST",
                    "path": "/mcp",
                }
            },
            "rawPath": "/mcp",
            "body": {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "ping",
            },
            "headers": {},
        }
        context = MockLambdaContext()

        with patch("server.adapters.aws_lambda.get_handler") as mock_get_handler:
            mock_handler = MagicMock()
            mock_handler.handle_request = AsyncMock(return_value=(200, {}, ""))
            mock_get_handler.return_value = mock_handler

            lambda_handler(event, context)

            call_args = mock_handler.handle_request.call_args
            assert isinstance(call_args[1]["body"], str)
            assert "jsonrpc" in call_args[1]["body"]

    def test_lambda_handler_lowercases_headers(self):
        """Test that headers are lowercased."""
        event = {
            "requestContext": {
                "http": {
                    "method": "POST",
                    "path": "/mcp",
                }
            },
            "rawPath": "/mcp",
            "body": json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}),
            "headers": {
                "Content-Type": "application/json",
                "X-Custom-Header": "value",
            },
        }
        context = MockLambdaContext()

        with patch("server.adapters.aws_lambda.get_handler") as mock_get_handler:
            mock_handler = MagicMock()
            mock_handler.handle_request = AsyncMock(return_value=(200, {}, ""))
            mock_get_handler.return_value = mock_handler

            lambda_handler(event, context)

            call_args = mock_handler.handle_request.call_args
            headers = call_args[1]["headers"]
            assert "content-type" in headers
            assert "x-custom-header" in headers

    def test_lambda_handler_handles_missing_context(self):
        """Test that handler works without context."""
        event = {
            "requestContext": {
                "http": {
                    "method": "POST",
                    "path": "/mcp",
                }
            },
            "rawPath": "/mcp",
            "body": json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}),
            "headers": {},
        }

        with patch("server.adapters.aws_lambda.get_handler") as mock_get_handler:
            mock_handler = MagicMock()
            mock_handler.handle_request = AsyncMock(return_value=(200, {}, ""))
            mock_get_handler.return_value = mock_handler

            response = lambda_handler(event, None)

            assert response["statusCode"] == 200
            call_args = mock_handler.handle_request.call_args
            assert call_args[1]["request_id"] == "unknown"

    def test_lambda_handler_handles_exception(self):
        """Test that handler catches exceptions and returns 500."""
        event = {
            "requestContext": {
                "http": {
                    "method": "POST",
                    "path": "/mcp",
                }
            },
            "rawPath": "/mcp",
            "body": json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}),
            "headers": {},
        }
        context = MockLambdaContext()

        with patch("server.adapters.aws_lambda.get_handler") as mock_get_handler:
            mock_get_handler.side_effect = Exception("Handler error")

            response = lambda_handler(event, context)

            assert response["statusCode"] == 500
            assert response["headers"]["Content-Type"] == "application/json"
            assert response["headers"]["Access-Control-Allow-Origin"] == "*"
            body = json.loads(response["body"])
            assert body["error"]["code"] == -32603
            assert body["error"]["message"] == "Internal error"

    def test_lambda_handler_handles_exception_without_context(self):
        """Test that handler handles exceptions without context."""
        event = {
            "requestContext": {
                "http": {
                    "method": "POST",
                    "path": "/mcp",
                }
            },
            "rawPath": "/mcp",
            "body": json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}),
            "headers": {},
        }

        with patch("server.adapters.aws_lambda.get_handler") as mock_get_handler:
            mock_get_handler.side_effect = Exception("Handler error")

            response = lambda_handler(event, None)

            assert response["statusCode"] == 500
            body = json.loads(response["body"])
            assert body["error"]["code"] == -32603

    def test_lambda_handler_defaults_path_when_missing(self):
        """Test that handler defaults path to / when missing."""
        event = {
            "requestContext": {
                "http": {
                    "method": "POST",
                }
            },
            "body": json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}),
            "headers": {},
        }
        context = MockLambdaContext()

        with patch("server.adapters.aws_lambda.get_handler") as mock_get_handler:
            mock_handler = MagicMock()
            mock_handler.handle_request = AsyncMock(return_value=(200, {}, ""))
            mock_get_handler.return_value = mock_handler

            lambda_handler(event, context)

            call_args = mock_handler.handle_request.call_args
            assert call_args[1]["path"] == "/"

    def test_lambda_handler_defaults_method_when_missing(self):
        """Test that handler defaults method to POST when missing."""
        event = {
            "requestContext": {
                "http": {
                    "path": "/mcp",
                }
            },
            "rawPath": "/mcp",
            "body": json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}),
            "headers": {},
        }
        context = MockLambdaContext()

        with patch("server.adapters.aws_lambda.get_handler") as mock_get_handler:
            mock_handler = MagicMock()
            mock_handler.handle_request = AsyncMock(return_value=(200, {}, ""))
            mock_get_handler.return_value = mock_handler

            lambda_handler(event, context)

            call_args = mock_handler.handle_request.call_args
            assert call_args[1]["method"] == "POST"


class TestGetHandler:
    """Test get_handler function."""

    def test_get_handler_creates_new_instance(self):
        """Test that get_handler creates new instance when None."""
        import server.adapters.aws_lambda

        server.adapters.aws_lambda._handler = None

        handler1 = get_handler()
        assert handler1 is not None

        handler2 = get_handler()
        # Should return same instance (singleton)
        assert handler1 is handler2

    def test_get_handler_reuses_existing_instance(self):
        """Test that get_handler reuses existing instance."""
        import server.adapters.aws_lambda

        existing_handler = MagicMock()
        server.adapters.aws_lambda._handler = existing_handler

        handler = get_handler()
        assert handler is existing_handler

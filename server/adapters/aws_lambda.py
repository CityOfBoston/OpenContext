"""AWS Lambda adapter for OpenContext MCP server.

This adapter transforms AWS Lambda events (from Function URL or API Gateway)
into the universal HTTP format expected by UniversalHTTPHandler.
"""

import asyncio
import base64
import json
import logging
from typing import Any, Dict, Optional, Protocol

from server.http_handler import UniversalHTTPHandler


class LambdaContext(Protocol):
    """Protocol for AWS Lambda context object.

    This defines the expected interface for Lambda context objects,
    which provide runtime information about the Lambda execution environment.
    """

    aws_request_id: str
    function_name: Optional[str]
    memory_limit_in_mb: Optional[int]


logger = logging.getLogger(__name__)

# Module-level handler instance for Lambda warm starts
_handler: Optional[UniversalHTTPHandler] = None


def get_handler() -> UniversalHTTPHandler:
    """Get or create the universal HTTP handler instance.

    Uses lazy initialization to support Lambda warm starts.

    Returns:
        UniversalHTTPHandler instance
    """
    global _handler

    if _handler is None:
        _handler = UniversalHTTPHandler()
        logger.info("Created new UniversalHTTPHandler instance")

    return _handler


def lambda_handler(
    event: Dict[str, Any], context: Optional[LambdaContext]
) -> Dict[str, Any]:
    """AWS Lambda handler function.

    Transforms Lambda events to universal HTTP format, processes the request,
    and transforms the response back to Lambda format.

    Supports both Lambda Function URL and API Gateway event formats.

    Args:
        event: Lambda event (HTTP request from Function URL or API Gateway)
        context: Lambda context object

    Returns:
        HTTP response dictionary with statusCode, headers, and body
    """
    try:
        # Extract request ID from context
        request_id = context.aws_request_id if context else "unknown"
        function_name = getattr(context, "function_name", None) if context else None
        memory_limit = getattr(context, "memory_limit_in_mb", None) if context else None

        logger.info(
            "Lambda invocation started",
            extra={
                "request_id": request_id,
                "function_name": function_name,
                "memory_limit": memory_limit,
            },
        )

        # Extract HTTP method (supports both formats)
        http_method = event.get("requestContext", {}).get("http", {}).get(
            "method"
        ) or event.get("httpMethod", "POST")

        # Extract path (supports both formats)
        request_path = (
            event.get("rawPath")
            or event.get("requestContext", {}).get("http", {}).get("path")
            or event.get("path", "/")
        )

        # Handle OPTIONS requests for CORS preflight
        if http_method == "OPTIONS":
            handler = get_handler()
            status_code, headers, body = handler.handle_options(request_id=request_id)

            logger.info(
                "CORS preflight request handled",
                extra={
                    "request_id": request_id,
                    "status_code": status_code,
                },
            )

            return {
                "statusCode": status_code,
                "headers": headers,
                "body": body,
            }

        # Extract body
        body = event.get("body", "{}")

        # Handle base64-encoded bodies from API Gateway
        if event.get("isBase64Encoded", False):
            try:
                body = base64.b64decode(body).decode("utf-8")
            except Exception as e:
                logger.error(
                    f"Failed to decode base64 body: {e}",
                    extra={"request_id": request_id},
                )
                raise ValueError(f"Invalid base64-encoded body: {e}") from e

        if isinstance(body, dict):
            body = json.dumps(body)

        # Extract headers
        headers = event.get("headers", {})
        if isinstance(headers, dict):
            # Convert header keys to lowercase for HTTP/1.1 compliance.
            # HTTP/1.1 header field names are case-insensitive, and normalizing
            # to lowercase ensures consistent behavior across different Lambda
            # event sources (Function URL vs API Gateway). This normalization
            # is expected by UniversalHTTPHandler for reliable header access.
            headers = {k.lower(): v for k, v in headers.items()}
        else:
            headers = {}

        # Get handler and process request
        handler = get_handler()

        async def _run_with_cleanup():
            try:
                return await handler.handle_request(
                    method=http_method,
                    path=request_path,
                    body=body,
                    headers=headers,
                    request_id=request_id,
                )
            finally:
                # Close plugin HTTP clients before event loop closes.
                # Fixes "Event loop is closed" on warm starts when tools use httpx.
                from server import http_handler

                if http_handler._plugin_manager is not None:
                    await http_handler._plugin_manager.shutdown()
                    http_handler._plugin_manager = None
                    http_handler._mcp_server = None

        # Run async handler
        status_code, response_headers, response_body = asyncio.run(_run_with_cleanup())

        # Transform to Lambda response format
        lambda_response = {
            "statusCode": status_code,
            "headers": response_headers,
            "body": response_body,
        }

        logger.info(
            "Lambda invocation completed",
            extra={
                "request_id": request_id,
                "status_code": status_code,
            },
        )

        return lambda_response

    except Exception as e:
        # Comprehensive error handling - catch all exceptions and return 500
        request_id = context.aws_request_id if context else "unknown"

        logger.error(
            f"Error in Lambda handler: {e}",
            extra={
                "request_id": request_id,
                "error_type": type(e).__name__,
            },
            exc_info=True,
        )

        error_response = {
            "statusCode": 500,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
            "body": json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {
                        "code": -32603,
                        "message": "Internal error",
                        "data": str(e),
                    },
                }
            ),
        }

        return error_response

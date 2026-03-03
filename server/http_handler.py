"""Universal HTTP handler for OpenContext MCP server.

This handler provides cloud-agnostic HTTP request processing that can be
used by any cloud provider adapter (AWS Lambda, GCP Cloud Functions, Azure Functions, etc.).
"""

import json
import logging
import os
import time
import uuid
from typing import Any, Dict, Optional, Tuple

from core.logging_utils import (
    configure_json_logging,
    format_request_log,
    format_response_log,
)
from core.mcp_server import MCPServer
from core.plugin_manager import PluginManager
from core.validators import (
    ConfigurationError,
    get_logging_config,
    load_and_validate_config,
)

# Configure JSON logging globally (must be called before other loggers are created)
# Try to get log level from config, but default to INFO if config not available yet
try:
    # Try loading config to get log level
    if os.environ.get("OPENCONTEXT_CONFIG"):
        config_json = os.environ.get("OPENCONTEXT_CONFIG")
        config = json.loads(config_json)
        logging_config = get_logging_config(config)
        log_level = logging_config.get("level", "INFO")
    else:
        # Try loading from config.yaml (for local testing)
        config = load_and_validate_config("config.yaml")
        logging_config = get_logging_config(config)
        log_level = logging_config.get("level", "INFO")
except Exception:
    # If config loading fails, use default
    log_level = "INFO"

configure_json_logging(level=log_level, pretty=False)  # Compact JSON for CloudWatch
logger = logging.getLogger(__name__)

# Global variables for container reuse (warm starts)
_plugin_manager: Optional[PluginManager] = None
_mcp_server: Optional[MCPServer] = None
_config: Optional[Dict[str, Any]] = None


def _load_config() -> Dict[str, Any]:
    """Load configuration from environment or embedded config.

    Returns:
        Configuration dictionary
    """
    global _config

    if _config is not None:
        return _config

    # Try to load from environment variable (set by Terraform)
    config_json = os.environ.get("OPENCONTEXT_CONFIG")
    if config_json:
        try:
            _config = json.loads(config_json)
            logger.info("Loaded configuration from environment variable")
            return _config
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse config from environment: {e}")
            raise

    # Fall back to loading from config.yaml (for local testing)
    try:
        _config = load_and_validate_config("config.yaml")
        logger.info("Loaded configuration from config.yaml")
        return _config
    except FileNotFoundError:
        logger.error(
            "No configuration found. Set OPENCONTEXT_CONFIG environment variable "
            "or ensure config.yaml exists."
        )
        raise


async def _initialize_server() -> None:
    """Initialize plugin manager and MCP server.

    This function is called on first request (cold start) and reuses
    the initialized instances for subsequent requests (warm starts).
    """
    global _plugin_manager, _mcp_server

    if _plugin_manager is not None and _mcp_server is not None:
        return

    try:
        config = _load_config()

        # Initialize Plugin Manager
        _plugin_manager = PluginManager(config)

        # Load plugins (validates ONE plugin enabled)
        await _plugin_manager.load_plugins()

        # Initialize MCP Server
        _mcp_server = MCPServer(_plugin_manager)

        logger.info("OpenContext MCP server initialized successfully")

    except ConfigurationError as e:
        # Log error and crash
        logger.error(f"Configuration error: {e}")
        raise RuntimeError(f"Configuration error: {e}") from e
    except Exception as e:
        logger.error(f"Failed to initialize server: {e}", exc_info=True)
        raise


class UniversalHTTPHandler:
    """Universal HTTP handler for cloud-agnostic request processing."""

    def __init__(self) -> None:
        """Initialize the universal HTTP handler."""
        logger.info("UniversalHTTPHandler initialized")

    @staticmethod
    def _get_cors_headers() -> Dict[str, str]:
        """Get standard CORS headers for responses.

        Returns:
            Dictionary of CORS headers
        """
        return {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "content-type",
            "Access-Control-Expose-Headers": "x-request-id, mcp-session-id",
        }

    async def handle_request(
        self,
        method: str,
        path: str,
        body: str,
        headers: Dict[str, str],
        request_id: Optional[str] = None,
    ) -> Tuple[int, Dict[str, str], str]:
        """Handle a universal HTTP request.

        Args:
            method: HTTP method (e.g., "POST", "GET")
            path: Request path (e.g., "/mcp")
            body: Request body as JSON string
            headers: Request headers as dictionary
            request_id: Optional request ID for logging/tracing

        Returns:
            Tuple of (status_code, response_headers, response_body)
        """
        start_time = time.perf_counter()
        request_id = request_id or "unknown"

        # Validate path - must be /mcp
        if path != "/mcp":
            duration_ms = (time.perf_counter() - start_time) * 1000
            error_body = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {
                        "code": -32601,
                        "message": "Not Found",
                        "data": f"Path '{path}' not found. Expected '/mcp'",
                    },
                }
            )
            logger.warning(
                f"404 error: Path '{path}' not found",
                extra={
                    "request_id": request_id,
                    "request_path": path,
                    "http_method": method,
                    "duration_ms": duration_ms,
                },
            )
            error_headers = {"Content-Type": "application/json"}
            error_headers.update(self._get_cors_headers())
            return (
                404,
                error_headers,
                error_body,
            )

        # Validate method - must be POST
        if method != "POST":
            duration_ms = (time.perf_counter() - start_time) * 1000
            error_body = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {
                        "code": -32601,
                        "message": "Method Not Allowed",
                        "data": f"Method '{method}' not allowed. Expected 'POST'",
                    },
                }
            )
            logger.warning(
                f"405 error: Method '{method}' not allowed",
                extra={
                    "request_id": request_id,
                    "request_path": path,
                    "http_method": method,
                    "duration_ms": duration_ms,
                },
            )
            error_headers = {"Content-Type": "application/json", "Allow": "POST"}
            error_headers.update(self._get_cors_headers())
            return (
                405,
                error_headers,
                error_body,
            )

        # Parse JSON to check if this is an initialize request
        # NOTE: This is intentionally parsing the JSON body separately from the
        # later parsing in _mcp_server.handle_http_request(). This early parsing
        # allows us to detect initialize requests and generate session IDs without
        # affecting error handling if the JSON is invalid. The body will be parsed
        # again later, which is an acceptable trade-off for error handling isolation.
        try:
            request_json = json.loads(body)
            is_initialize = request_json.get("method") == "initialize"
        except (json.JSONDecodeError, AttributeError):
            is_initialize = False

        # Generate session ID for initialize requests
        # NOTE: This session ID is for logging and tracing purposes only.
        # It is NOT implementing true session management - there is no persistent
        # session storage. The session ID is included in response headers to
        # help correlate logs and trace requests, but it does not maintain
        # any server-side session state.
        session_id = None
        if is_initialize:
            session_id = str(uuid.uuid4())
            logger.info(
                f"Initialize request detected, generating session ID: {session_id}",
                extra={"request_id": request_id},
            )

        # Log request details
        request_log_data = format_request_log(
            request_id=request_id,
            http_method=method,
            request_path=path,
            headers=headers,
            body=body,
            lambda_context=None,  # Not available in universal handler
        )
        logger.info("Incoming HTTP request", extra=request_log_data)

        try:
            # Initialize server on first request
            await _initialize_server()

            # Handle request
            response = await _mcp_server.handle_http_request(body, headers)

            # Extract status code and body from response
            status_code = response.get("statusCode", 200)
            response_body = response.get("body", "")
            response_headers = response.get("headers", {}).copy()

            # Add session ID to response headers if this was an initialize request
            if session_id:
                response_headers["Mcp-Session-Id"] = session_id

            # Add request ID to response headers for tracing
            response_headers["X-Request-ID"] = request_id

            # Ensure Content-Type is set
            if "Content-Type" not in response_headers:
                response_headers["Content-Type"] = "application/json"

            # Add CORS headers
            response_headers.update(self._get_cors_headers())

            # Calculate duration
            duration_ms = (time.perf_counter() - start_time) * 1000

            # Log response details
            response_log_data = format_response_log(
                request_id=request_id,
                status_code=status_code,
                headers=response_headers,
                body=response_body,
                duration_ms=duration_ms,
                success=True,
            )
            logger.info("HTTP request processed successfully", extra=response_log_data)

            return (status_code, response_headers, response_body)

        except ConfigurationError as e:
            # Configuration errors should crash
            duration_ms = (time.perf_counter() - start_time) * 1000
            error_body = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {
                        "code": -32603,
                        "message": "Server configuration error",
                        "data": str(e),
                    },
                }
            )

            # Log error response
            error_headers = {"Content-Type": "application/json"}
            error_headers.update(self._get_cors_headers())
            response_log_data = format_response_log(
                request_id=request_id,
                status_code=500,
                headers=error_headers,
                body=error_body,
                duration_ms=duration_ms,
                success=False,
            )
            logger.error(
                f"Configuration error in request {request_id}: {e}",
                extra={**response_log_data, "error_type": "ConfigurationError"},
                exc_info=True,
            )

            return (500, error_headers, error_body)

        except Exception as e:
            duration_ms = (time.perf_counter() - start_time) * 1000
            error_body = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {
                        "code": -32603,
                        "message": "Internal error",
                        "data": str(e),
                    },
                }
            )

            # Log error response
            error_headers = {"Content-Type": "application/json"}
            error_headers.update(self._get_cors_headers())
            response_log_data = format_response_log(
                request_id=request_id,
                status_code=500,
                headers=error_headers,
                body=error_body,
                duration_ms=duration_ms,
                success=False,
            )
            logger.error(
                f"Error processing request {request_id}: {e}",
                extra={**response_log_data, "error_type": type(e).__name__},
                exc_info=True,
            )

            return (500, error_headers, error_body)

    def handle_options(
        self, request_id: Optional[str] = None
    ) -> Tuple[int, Dict[str, str], str]:
        """Handle CORS preflight OPTIONS request.

        Args:
            request_id: Optional request ID for logging/tracing

        Returns:
            Tuple of (status_code, response_headers, response_body)
        """
        request_id = request_id or "unknown"
        cors_headers = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "content-type",
            "Access-Control-Expose-Headers": "x-request-id, mcp-session-id",
            "Access-Control-Max-Age": "86400",
            "Content-Type": "application/json",
            "X-Request-ID": request_id,
        }

        logger.info(
            "CORS preflight OPTIONS request handled",
            extra={"request_id": request_id},
        )

        return (200, cors_headers, "")

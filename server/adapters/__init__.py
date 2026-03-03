"""Cloud provider adapters for OpenContext.

This package contains adapters that transform cloud-specific event formats
(e.g., AWS Lambda events, GCP Cloud Functions events, Azure Functions events)
into the universal HTTP format expected by UniversalHTTPHandler.

Each adapter handles:
- Event format transformation (cloud-specific -> universal)
- Response format transformation (universal -> cloud-specific)
- Cloud-specific context extraction (request IDs, function names, etc.)
"""

from .aws_lambda import lambda_handler

__all__ = ["lambda_handler"]

"""Base open data plugin for OpenContext.

This module defines :class:`BaseOpenDataPlugin`, a shared base class that
centralizes the HTTP client lifecycle, retry policy, HTTP error translation,
tool dispatch with required-argument validation, and record formatting that
the CKAN/Socrata/ArcGIS plugins currently copy-paste. Concrete plugins
subclass it, declare ``config_class`` and ``tool_handlers()``, and implement
the remaining ``DataPlugin`` abstract methods.
"""

import logging
import re
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx
from tenacity import (
    retry,
    retry_if_not_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from core.config_base import BasePluginConfig
from core.interfaces import DataPlugin, ToolResult
from core.portal_content import (
    DEFAULT_MAX_LINE,
    DEFAULT_MAX_TEXT,
    clean_error_message,
    clean_text,
    frame_portal_content,
    indent_continuation,
)

logger = logging.getLogger(__name__)

# Safe SQL identifier: letters, digits, underscores; must not start with a
# digit; max 64 chars. Used by build_where_clause to reject field names that
# could smuggle SQL fragments.
_SAFE_IDENTIFIER = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$")

# ``YYYY-MM-DD`` prefix of an ISO-8601 timestamp.
_ISO_DATE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}")
# Hostname safe to echo back to the model when a URL's host is untrusted.
_SAFE_HOSTNAME = re.compile(r"^[a-z0-9]([a-z0-9-]{0,62}\.)*[a-z0-9-]{1,63}$")

HTTP_RETRY = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_not_exception_type((RuntimeError, httpx.HTTPStatusError)),
)


class ToolHandler:
    """Descriptor for a single tool exposed by a plugin.

    Attributes:
        handler: Async callable taking an arguments dict and returning either
            a :class:`ToolResult` or a ``str`` (which is wrapped into a
            successful ``ToolResult``).
        required_args: Argument names that must be present and truthy before
            the handler runs.
        guidance: Optional next-step hint written by the connector (e.g.
            "Use get_dataset with a dataset ID for details"). It is emitted
            *outside* the untrusted-data boundary so connector instructions
            are never mixed with portal text.
        frame_output: Whether successful text output is wrapped in the
            untrusted-data boundary by :meth:`BaseOpenDataPlugin.execute_tool`.
            Leave True for anything that echoes portal content.
    """

    __slots__ = ("frame_output", "guidance", "handler", "required_args")

    def __init__(
        self,
        handler: Callable[[dict[str, Any]], Any],
        required_args: tuple[str, ...] = (),
        *,
        guidance: str | None = None,
        frame_output: bool = True,
    ) -> None:
        """Initialize a ToolHandler.

        Args:
            handler: Async callable taking the arguments dict.
            required_args: Tuple of argument names that must be present and
                truthy before the handler is invoked.
            guidance: Connector-authored hint appended after the data boundary.
            frame_output: Wrap successful text output in the data boundary.
        """
        self.handler = handler
        self.required_args = required_args
        self.guidance = guidance
        self.frame_output = frame_output


class BaseOpenDataPlugin(DataPlugin):
    """Shared base class for open data plugins.

    Subclasses set :attr:`config_class` to a :class:`BasePluginConfig`
    subclass, implement :meth:`tool_handlers` to declare their tools, and
    fill in the remaining ``DataPlugin`` abstract methods
    (:meth:`initialize`, :meth:`get_tools`, :meth:`health_check`, plus the
    data-access methods).
    """

    config_class: type[BasePluginConfig] = BasePluginConfig

    # Shape of a valid dataset/resource identifier for this provider. Used by
    # :meth:`safe_id` before an ID is interpolated into a portal URL or a
    # follow-up instruction, so a crafted ID cannot carry arbitrary text.
    id_pattern: re.Pattern[str] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,199}$")

    # Human-readable label for the data boundary preamble.
    provider_label: str = "open data portal"

    def __init__(self, config: dict[str, Any]) -> None:
        """Initialize the plugin and eagerly validate its configuration.

        Args:
            config: Plugin configuration dictionary (from config.yaml).
        """
        super().__init__(config)
        self.plugin_config: BasePluginConfig = self.config_class(**config)
        self._clients: list[httpx.AsyncClient] = []

    def _create_http_client(self, **kwargs: Any) -> httpx.AsyncClient:
        """Create an :class:`httpx.AsyncClient` and track it for shutdown.

        Args:
            **kwargs: Forwarded to ``httpx.AsyncClient``.

        Returns:
            The created async HTTP client.
        """
        client = httpx.AsyncClient(**kwargs)
        self._clients.append(client)
        return client

    async def shutdown(self) -> None:
        """Close all tracked HTTP clients and mark the plugin uninitialized."""
        for client in self._clients:
            try:
                await client.aclose()
            except Exception as e:
                logger.warning(f"Error closing HTTP client: {e}")
        self._clients.clear()
        self._initialized = False
        logger.info(f"{self.plugin_name} plugin shut down")

    # ── Untrusted portal content helpers ───────────────────────────────

    @property
    def portal_source(self) -> str:
        """Description of the portal used in the data-boundary preamble."""
        return f"{self.plugin_config.city_name} {self.provider_label}"

    def portal_text(
        self,
        value: Any,
        *,
        max_len: int = DEFAULT_MAX_TEXT,
        default: str = "",
    ) -> str:
        """Clean a free-text portal value (description, notes, record value).

        Multi-line content is preserved but normalized; see
        :func:`core.portal_content.clean_text`.
        """
        cleaned = clean_text(value, max_len=max_len)
        return cleaned if cleaned else default

    def portal_block(
        self,
        value: Any,
        *,
        max_len: int = DEFAULT_MAX_TEXT,
        default: str = "",
    ) -> str:
        """Clean a multi-line value for a ``Label: value`` line.

        Like :meth:`portal_text`, but every continuation line is indented so
        the value cannot forge a top-level label or connector instruction.
        """
        return indent_continuation(
            self.portal_text(value, max_len=max_len, default=default)
        )

    def portal_line(
        self,
        value: Any,
        *,
        max_len: int = DEFAULT_MAX_LINE,
        default: str = "",
    ) -> str:
        """Clean a portal value that must stay on one line (title, tag, name)."""
        cleaned = clean_text(value, max_len=max_len, single_line=True)
        return cleaned if cleaned else default

    @staticmethod
    def short_date(value: Any) -> str:
        """Render a portal timestamp as ``YYYY-MM-DD`` (or ``""``).

        Accepts ISO-8601 strings (with or without time/``Z``), epoch seconds,
        and epoch milliseconds (ArcGIS). Anything unrecognized is returned
        cleaned and single-line so nothing is silently dropped.
        """
        if value is None or value == "":
            return ""
        if isinstance(value, bool):
            return ""
        if isinstance(value, (int, float)):
            try:
                seconds = value / 1000 if value > 1e11 else value
                return datetime.fromtimestamp(seconds, tz=UTC).strftime("%Y-%m-%d")
            except (ValueError, OverflowError, OSError):
                return ""
        text = clean_text(value, max_len=40, single_line=True)
        if _ISO_DATE_PREFIX.match(text):
            return text[:10]
        if text.isdigit():
            return BaseOpenDataPlugin.short_date(int(text))
        return text

    @staticmethod
    def human_size(value: Any) -> str:
        """Render a byte count as ``B/KB/MB/GB`` (or the cleaned raw value)."""
        if value is None or value == "" or isinstance(value, bool):
            return ""
        try:
            size = float(value)
        except (TypeError, ValueError):
            return clean_text(value, max_len=DEFAULT_MAX_LINE, single_line=True)
        if size < 0:
            return ""
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if size < 1024 or unit == "TB":
                return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
            size /= 1024
        return ""  # pragma: no cover

    def display_portal_url(self, url: Any, *, extra_hosts: Iterable[str] = ()) -> str:
        """Render a portal-supplied URL only if its host is trusted.

        Trusted hosts are the configured ``portal_url`` / ``base_url`` hosts
        (and their subdomains) plus ``extra_hosts``. An untrusted URL is
        never echoed; only its hostname is shown as ``(external: host)`` so
        the model can still say what kind of link it is. This is the
        display-side counterpart of the fetch-side allow-lists: a crafted
        dataset record cannot plant an arbitrary link in the model's context.
        """
        if not url:
            return ""
        cleaned = clean_text(url, max_len=500, single_line=True)
        parsed = urlparse(cleaned)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in ("http", "https") or not host:
            return "(external: unparseable host)"
        trusted: list[str] = []
        for attr in ("portal_url", "base_url"):
            configured = getattr(self.plugin_config, attr, None)
            if configured:
                configured_host = (urlparse(str(configured)).hostname or "").lower()
                if configured_host:
                    trusted.append(configured_host)
        trusted.extend(h.lower().lstrip(".") for h in extra_hosts if h)
        for t in trusted:
            if host == t or host.endswith(f".{t}"):
                return cleaned
        if _SAFE_HOSTNAME.match(host):
            return f"(external: {host})"
        return "(external: unparseable host)"

    def format_search_header(
        self, total: int | None, shown: int, *, offset: int = 0
    ) -> str:
        """Header line for search/list results with the catalog-wide total.

        ``total`` is the portal's total hit count (``None`` when the API did
        not return one); ``shown`` is how many results follow.
        """
        city = self.plugin_config.city_name
        if total is None or not isinstance(total, int) or total < shown:
            return f"Found {shown} dataset(s) in {city}'s open data portal:"
        if total > shown or offset:
            first = offset + 1 if shown else 0
            last = offset + shown
            return (
                f"Found {total} matching dataset(s) in {city}'s open data portal "
                f"(showing {first}-{last}):"
            )
        return f"Found {total} dataset(s) in {city}'s open data portal:"

    def safe_id(self, value: Any, *, default: str = "unknown") -> str:
        """Return ``value`` if it looks like a valid identifier, else ``default``.

        Use before building ``Portal: {portal_url}/dataset/{id}`` links or
        ``Use get_schema with dataset_id='{id}'`` hints so that an ID coming
        from the portal cannot smuggle path segments, query strings, or prose
        into a URL or an instruction.
        """
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            value = str(value)
        if isinstance(value, str) and self.id_pattern.match(value):
            return value
        return default

    def _raise_http_error(self, exc: httpx.HTTPStatusError, context: str = "") -> None:
        """Translate an :class:`httpx.HTTPStatusError` into a RuntimeError.

        Attempts to extract a human-readable message from the response JSON
        (``message`` key, or CKAN-style nested ``error`` dict); falls back to
        the raw status text.

        Args:
            exc: The HTTP status error raised by ``raise_for_status``.
            context: Optional context label (e.g. ``"Discovery API"``).

        Raises:
            RuntimeError: Always, chained from ``exc``.
        """
        status_code = exc.response.status_code
        msg: str | None = None
        try:
            body = exc.response.json()
            if isinstance(body, dict):
                if body.get("message"):
                    msg = body.get("message")
                else:
                    err = body.get("error")
                    if isinstance(err, dict):
                        msg = err.get("message", str(err))
                    elif err is not None:
                        msg = str(err)
        except (ValueError, TypeError):
            pass

        if not msg:
            msg = exc.response.text or f"HTTP {status_code}"

        # The message body is portal-controlled; cap and normalize it and
        # label it so the model does not mistake it for connector output.
        msg = clean_error_message(msg)
        portal = f"{self.plugin_config.city_name} OpenData portal"
        prefix = f"Error{context} on" if context else "Error on"
        raise RuntimeError(
            f"{prefix} {portal} (HTTP {status_code}); portal said: {msg!r}"
        ) from exc

    def tool_handlers(self) -> dict[str, ToolHandler]:
        """Return the mapping of tool name to :class:`ToolHandler`.

        Subclasses must override this to expose their tools via
        :meth:`execute_tool`.

        Returns:
            Dict mapping tool name (without plugin prefix) to ToolHandler.
        """
        return {}

    async def execute_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> ToolResult:
        """Dispatch a tool call to the matching registered handler.

        Args:
            tool_name: Name of the tool (without plugin prefix).
            arguments: Tool input arguments.

        Returns:
            ``ToolResult`` with content and success flag. Unknown tools,
            missing required arguments, and handler exceptions are all
            translated into unsuccessful ``ToolResult`` objects.
        """
        handlers = self.tool_handlers()
        handler = handlers.get(tool_name)
        if handler is None:
            return ToolResult(
                content=[],
                success=False,
                error_message=f"Unknown tool: {tool_name}",
            )

        for arg in handler.required_args:
            if not arguments.get(arg):
                return ToolResult(
                    content=[],
                    success=False,
                    error_message=f"{arg} is required",
                )

        try:
            result = await handler.handler(arguments)
            if not isinstance(result, ToolResult):
                text = result if isinstance(result, str) else str(result)
                result = ToolResult(
                    content=[{"type": "text", "text": text}],
                    success=True,
                )
            return self._finalize_result(result, handler, tool_name)
        except Exception as e:
            logger.error(f"Error executing tool {tool_name}: {e}", exc_info=True)
            return ToolResult(
                content=[],
                success=False,
                error_message=clean_error_message(str(e)) or "Tool execution failed",
            )

    def _finalize_result(
        self, result: ToolResult, handler: ToolHandler, tool_name: str
    ) -> ToolResult:
        """Apply the untrusted-data boundary to a handler's ``ToolResult``.

        Every ``text`` content item of a successful result is wrapped by
        :func:`core.portal_content.frame_portal_content`; the handler's
        ``guidance`` is placed after the closing marker of the last item.
        Error messages are normalized and capped.
        """
        if not result.success:
            if result.error_message:
                result.error_message = clean_error_message(result.error_message)
            return result
        if not handler.frame_output:
            return result

        text_indexes = [
            i
            for i, item in enumerate(result.content)
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        for pos, i in enumerate(text_indexes):
            item = dict(result.content[i])
            item["text"] = frame_portal_content(
                item.get("text", ""),
                source=self.portal_source,
                guidance=handler.guidance if pos == len(text_indexes) - 1 else None,
                tool_name=tool_name,
            )
            result.content[i] = item
        return result

    def format_records(
        self,
        records: list[dict[str, Any]],
        *,
        max_display: int = 10,
        header: str | None = None,
        skip_keys: frozenset = frozenset({"_id"}),
    ) -> str:
        """Format a list of record dicts for user display.

        Replicates the ``Record N:`` style used by the existing plugins, with
        a ``... and X more record(s)`` suffix and a ``No records found.``
        empty case.

        Args:
            records: List of record dictionaries.
            max_display: Maximum number of records to render in full.
            header: Optional leading header line (e.g. ``"Found N record(s)"``).
            skip_keys: Record keys to omit from the output.

        Returns:
            Formatted string.
        """
        if not records:
            return "No records found."

        lines: list[str] = []
        if header:
            lines.append(header)
            lines.append("")

        for i, record in enumerate(records[:max_display], 1):
            lines.append(f"Record {i}:")
            for key, value in record.items():
                if key in skip_keys:
                    continue
                # Keys stay on one line; values keep their newlines but every
                # continuation line is indented so a value cannot forge a
                # top-level "Record N:" header or a connector instruction.
                safe_key = self.portal_line(key, default="(empty)")
                safe_value = indent_continuation(self.portal_text(value))
                lines.append(f"  {safe_key}: {safe_value}")
            lines.append("")

        if len(records) > max_display:
            lines.append(f"... and {len(records) - max_display} more record(s)")

        return "\n".join(lines)

    @staticmethod
    def build_where_clause(filters: dict[str, Any]) -> str:
        """Build a SQL ``WHERE`` clause from a field/value filter dict.

        Strings are escaped by doubling single quotes, ``None`` becomes
        ``IS NULL``, and other values are rendered as-is. Conditions are
        joined with ``AND``. Field names must be plain identifiers
        (letters, digits, underscores, not starting with a digit); anything
        else raises so SQL cannot be smuggled in through field names.

        Args:
            filters: Mapping of field name to filter value.

        Returns:
            The ``WHERE`` clause body (without the leading ``WHERE``
            keyword), or an empty string when ``filters`` is empty.

        Raises:
            ValueError: If a field name is not a safe identifier.
        """
        if not filters:
            return ""
        conditions: list[str] = []
        for field, value in filters.items():
            if not isinstance(field, str) or not _SAFE_IDENTIFIER.match(field):
                raise ValueError(f"Invalid filter field name: {field!r}")
            if isinstance(value, str):
                escaped = value.replace("'", "''")
                conditions.append(f"{field} = '{escaped}'")
            elif value is None:
                conditions.append(f"{field} IS NULL")
            else:
                conditions.append(f"{field} = {value}")
        return " AND ".join(conditions)

    # The following DataPlugin abstract methods remain abstract; subclasses
    # implement them. They are re-declared here only to document intent and
    # keep type checkers happy about the partial-implementation pattern.

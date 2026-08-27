# Security Model

OpenContext sits between an LLM host (Claude.ai, Claude Desktop, any MCP
client) and a public open data portal. Data flows in two directions, and each
direction has its own threat model.

## Outbound: LLM → portal

The model constructs queries (SQL, SoQL, ODSQL, ArcGIS `where` clauses) and
IDs that the connector forwards to the portal. Defenses:

- **Query validators** (`core/query_validator.py`, `plugins/*/…_validator.py`)
  reject multi-statement, write, and dangerous-function queries and cap query
  length.
- **Identifier whitelists** restrict field names, metric expressions, `ORDER
  BY`, and `HAVING` values assembled by `aggregate_data`.
- **`build_where_clause`** escapes values and rejects non-identifier field
  names.
- **Catalog filter whitelisting (CKAN)** (`_build_fq`) restricts `list_datasets`
  / `get_catalog_stats` filters to known Solr fields and quotes values as
  escaped Solr phrases, so model-supplied filter values cannot alter the query.
- **ArcGIS SSRF allow-list** (`_validate_feature_url`, `trusted_service_hosts`)
  stops a dataset record from steering Feature Service requests to arbitrary
  hosts.

## Inbound: portal → LLM (prompt injection)

Everything a portal returns is untrusted text that lands inside the model's
context window: dataset titles and descriptions, schema labels, error bodies,
and—most importantly—the records themselves. Datasets such as 311 requests,
permit applications, and public comments contain free text submitted by
members of the public. **An attacker does not need to compromise the portal to
plant text in it; they file a service request.**

Because hosts commonly pair an OpenContext connector with tools that can act
(email, calendar, files), the realistic harm is not a wrong answer about
parking tickets but a poisoned record instructing the assistant to exfiltrate
or modify the user's data through another connector.

### What the connector does

All of this lives in `core/portal_content.py` and is applied centrally by
`BaseOpenDataPlugin`, so every plugin gets it without opting in.

| Defense | Where | Effect |
| --- | --- | --- |
| **Untrusted-data boundary** | `execute_tool` → `_finalize_result` → `frame_portal_content` | Every successful text result is wrapped: a one-line preamble names the source and states that the content is data, not instructions; the body sits between `<<<BEGIN PORTAL DATA>>>` / `<<<END PORTAL DATA>>>`; the connector's own next-step hint (`ToolHandler(guidance=…)`) is emitted **after** the closing marker so instruction-shaped text never sits inside the data region. |
| **Normalization** | `clean_text`, `portal_text`, `portal_line` | Strips C0/C1 controls, zero-width and bidi-override code points, Unicode tag characters (“ASCII smuggling”), private-use and unassigned code points; collapses newlines in single-line fields (titles, IDs, tags, field names); truncates with an explicit `…[truncated, N more chars]` marker; defangs any literal boundary marker inside a value. |
| **Structure forgery prevention** | `format_records`, `indent_continuation` | Record keys are single-line; multi-line values have every continuation line indented, so a value cannot start a fake `Record 2:` header or a fake connector instruction at column 0. |
| **Size caps** | `DEFAULT_MAX_TEXT` (4 000 chars/value), `DEFAULT_MAX_LINE` (300), `DEFAULT_MAX_RESPONSE` (60 000/body), `DEFAULT_MAX_ERROR` (500) | Limits context stuffing. |
| **ID validation** | `safe_id` with a per-plugin `id_pattern` | An ID is only interpolated into a `Portal:` URL or a hint if it matches the provider's ID shape (Socrata 4x4, CKAN slug/UUID, Hub hex, ODS slug); otherwise it renders as `unknown` and no link is built. Links are always built from config + validated ID, never echoed from the portal. |
| **URL gating** | `BaseOpenDataPlugin.display_portal_url` (ArcGIS `_display_url` wraps it with `trusted_service_hosts`) | Portal-supplied URLs (resource downloads, license/attribution links, service endpoints) are echoed only when their host is the portal/API host or a subdomain of it (or an explicitly trusted host); otherwise only `(external: hostname)` is shown, never the URL itself. |
| **Error bodies** | `_raise_http_error`, `clean_error_message`, `mcp_server` error `data` | Portal error text is capped, flattened, and labeled `portal said: '…'`. |
| **Injection heuristics** | `detect_injection_markers` | A conservative regex scan (instruction overrides, role markers, chat-template tokens, exfiltration verbs, markdown image beacons, hidden HTML). A hit never blocks; it prepends a `WARNING:` line in the connector's voice and logs a `Possible prompt injection markers` entry with the tool name so operators can find poisoned records in CloudWatch. |
| **Tool annotations** | `ToolDefinition.annotations` | Every tool advertises `readOnlyHint: true, openWorldHint: true` so hosts can treat results as untrusted. |

### What the connector cannot do

None of this makes injection impossible. The model ultimately decides what to
do with the text, and the host's own defenses—tool permission prompts, the
client's injection classifiers, and the user reviewing actions—are the last
line of defense. The connector's job is to shrink the attack surface, keep
its own voice separable from portal content, and give operators visibility.

### Guidance for deployers

- Do not wire OpenContext into **unattended** agent pipelines that also hold
  write-capable tools (email, file sharing, payments).
- Watch for `Possible prompt injection markers` warnings in CloudWatch; each
  carries the tool name and matched heuristics.
- Keep `trusted_service_hosts` (ArcGIS) minimal.
- When adding a plugin, build output through `portal_line` / `portal_text` /
  `safe_id` / `format_records` and put next-step hints in
  `ToolHandler(guidance=…)`, not in the data body. Set `id_pattern` and
  `provider_label` on the plugin class.

## Reporting

Report vulnerabilities privately to the repository maintainers rather than
opening a public issue.

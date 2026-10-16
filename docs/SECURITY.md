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
  BY`, and `HAVING` values assembled by `aggregate_data`. For ArcGIS,
  `order_by` accepts only field names with an optional `ASC`/`DESC`
  (`WhereValidator.validate_order_by`); `aggregate_data` statistic fields,
  output names and `group_by` fields must be plain identifiers that exist in
  the layer's schema, and `having` goes through the same `WhereValidator` as
  `where`, which also refuses subqueries and set operators (`SELECT`, `UNION`,
  `INTERSECT`, `EXCEPT` outside quoted values). `layer` must be an integer id the service lists. All of these are
  checked before any request is sent.
- **`build_where_clause`** escapes values and rejects non-identifier field
  names.
- **Redirect credential scoping** (`_create_http_client(protect_headers=…)`)
  follows redirects (so a renamed portal domain such as `data.sfgov.org` →
  `data.sf.gov` keeps working) but strips the credential header (Socrata
  `X-App-Token`, CKAN/ODS/ArcGIS `Authorization`) on any hop whose host is not
  the configured portal/base host (or a trusted extra such as `*.arcgis.com`).
  A lapsed domain can be re-registered by someone else, so the credential is
  never forwarded to it; the request still follows through, unauthenticated.
- **ArcGIS SSRF guard** (`_validate_feature_url`, `trusted_service_hosts`,
  `auto_trust_hub_services`) stops a dataset record from steering Feature
  Service requests to arbitrary hosts. `*.arcgis.com`, the portal host and
  the explicit allow-list are always trusted. Hub-referenced hosts are
  auto-trusted only for https URLs on public DNS names with an ArcGIS REST
  service path; IP literals (e.g. cloud metadata endpoints), single-label
  and `.internal`/`.local` names, and non-service paths are refused, the
  bearer token is never forwarded to an auto-trusted host (redirect
  credential scoping applies), and operators can disable auto-trust.
- **Metadata XML parsing** (`plugins/arcgis/metadata_xml.py`). ArcGIS
  `get_schema` reads an item's formal metadata document from a fixed
  `www.arcgis.com` URL built from a validated 32-hex item id, without the
  bearer token (an ArcGIS Enterprise token never travels to ArcGIS Online)
  and with a 10-second timeout. The body is streamed and abandoned past
  2 MB, so an oversized document is never held in memory. Documents over
  2 MB are refused, and an expat pre-pass refuses any document that declares
  a DTD (doctype or entity declaration), whatever its encoding, before
  ElementTree builds a tree. Neither external entities (XXE) nor entity
  expansion ("billion laughs") reach the parser that reads the fields. Definitions it yields are portal content and go
  through the same cleaning and length caps as other schema text.

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
| **HTML to text** | `html_to_text` | Catalog descriptions, licences and access notes stored as HTML are converted to plain text (scripts and styles dropped, entities decoded) before normalization, so markup cannot hide text from the injection scan or reach the model as raw tags. |
| **Normalization** | `clean_text`, `portal_text`, `portal_line` | Strips C0/C1 controls, zero-width and bidi-override code points, Unicode tag characters (“ASCII smuggling”), private-use and unassigned code points; collapses newlines in single-line fields (titles, IDs, tags, field names); truncates with an explicit `…[truncated, N more chars]` marker; defangs any literal boundary marker inside a value. |
| **Structure forgery prevention** | `format_records`, `render_rows`, `indent_continuation` | Record keys are single-line; multi-line values have every continuation line indented, so a value cannot start a fake `Record 2:` header or a fake connector instruction at column 0. JSON output escapes newlines; CSV values are kept on one line. |
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

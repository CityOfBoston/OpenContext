# Built-in Plugins Reference

OpenContext includes built-in plugins for CKAN, ArcGIS Hub, Socrata, and Opendatasoft open data portals.

| Portal Software | Example Cities / Portals      | Plugin                             |
| --------------- | ----------------------------- | ---------------------------------- |
| CKAN            | Boston, data.gov, data.gov.uk | `ckan`                             |
| ArcGIS Hub      | Washington DC, hub.arcgis.com | `arcgis`                           |
| Socrata         | Chicago, NYC, Seattle         | `socrata`                          |
| Opendatasoft    | Long Beach, public.opendatasoft.com | `opendatasoft`               |
| Other           | Any custom API or database    | [Custom plugin](CUSTOM_PLUGINS.md) |

Not sure which plugin to use? Check your portal's URL or "About" page, or look for the platform logo.

---

## CKAN Plugin

For CKAN-based open data portals (e.g., data.gov, data.gov.uk).

### Configuration

```yaml
plugins:
  ckan:
    enabled: true
    base_url: "https://data.yourcity.gov" # CKAN API base URL
    portal_url: "https://data.yourcity.gov" # Public portal URL
    city_name: "Your City" # City/organization name
    timeout: 120 # HTTP timeout in seconds
    api_key: "${CKAN_API_KEY}" # Optional: API key
```

### Tools

| Tool                                                                                     | Description                                                                                        |
| ---------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| `ckan__search_datasets(query, limit)`                                                    | Free-text search; the header reports the catalog-wide match count                                  |
| `ckan__list_datasets(query, organization, tag, format, license, group, sort, limit, offset)` | Browse the catalog with exact-match filters, sorting (default: most recently modified) and paging |
| `ckan__get_catalog_stats(facets, query, organization, tag, format, license, group, limit)` | Dataset counts overall and per organization / tag / format / license / group                    |
| `ckan__get_dataset(dataset_id, max_resources)`                                           | Full metadata: organization, license, created/modified dates, tags, groups, and every resource with format, dates, size and download URL |
| `ckan__query_data(resource_id, filters, limit)`                                          | Query data from a resource                                                                         |
| `ckan__get_schema(resource_id)`                                                          | Get schema for a resource                                                                          |
| `ckan__execute_sql(sql)`                                                                 | Execute PostgreSQL SELECT queries (advanced)                                                       |
| `ckan__aggregate_data(resource_id, metrics, group_by, filters, having, order_by, limit)` | Aggregate data with GROUP BY — supports `count(*)`, `sum()`, `avg()`, `min()`, `max()`, `stddev()` |

### SQL Execution

The `execute_sql` tool allows complex PostgreSQL queries (CTEs, window functions, joins). Only SELECT is allowed — INSERT, UPDATE, DELETE, DROP, and other destructive operations are blocked. Resource IDs must be valid UUIDs in double quotes: `FROM "uuid-here"`.

### CKAN API

This plugin uses CKAN's Action API:

- `/api/3/action/package_search` - Search datasets
- `/api/3/action/package_show` - Get dataset
- `/api/3/action/datastore_search` - Query data

See [CKAN API documentation](https://docs.ckan.org/en/latest/api/) for details.

---

## ArcGIS Hub Plugin

For ArcGIS Hub open data portals (e.g., hub.arcgis.com, data-yourcity.hub.arcgis.com).

### Configuration

```yaml
plugins:
  arcgis:
    enabled: true
    portal_url: "https://hub.arcgis.com" # ArcGIS Hub portal URL
    city_name: "Your City" # City/organization name
    timeout: 120 # HTTP timeout in seconds
    token: "${ARCGIS_TOKEN}" # Optional: bearer token for private items
    # trusted_service_hosts: # Extra Feature Service hosts to allow explicitly
    #   - "maps2.dcgis.dc.gov"
    # auto_trust_hub_services: true # Accept Hub-referenced ArcGIS service URLs (default)
```

### Tools

| Tool                                                                                  | Description                                                   |
| ------------------------------------------------------------------------------------- | ------------------------------------------------------------- |
| `arcgis__search_datasets(query, limit)`                                                   | Search the Hub catalog                                        |
| `arcgis__get_dataset(dataset_id)`                                                     | Get metadata for a Hub item, with its service's layers/tables |
| `arcgis__get_aggregations(field, query)`                                                  | Facet counts for type, tags, categories, or access            |
| `arcgis__get_schema(dataset_id, layer)`                                               | Field names, types, aliases, lengths, domains, and definitions/units from the item's metadata, for a layer or table |
| `arcgis__query_data(dataset_id, layer, where, out_fields, limit, offset, order_by, format)` | Query a Feature Service, with paging and a total match count |
| `arcgis__aggregate_data(dataset_id, statistics, group_by, where, having, order_by, layer, limit, format)` | Server-side count/sum/avg/min/max/stddev, grouped by fields |

### Usage Notes

- `get_dataset` returns the Hub item metadata. Check that the item has a `Service URL` before calling `query_data`, and use the listed layer ids with `layer`.
- `get_aggregations` accepts `field` values: `"type"`, `"tags"`, `"categories"`, `"access"`. This is a catalog-level tool, not a DataPlugin method — it has no equivalent in other plugins.
- `query_data` uses the ArcGIS Feature Service query interface. The `where` parameter is a SQL WHERE clause (e.g., `"population > 10000"`). Only Feature Layer, Feature Service, Map Service, and Table types are queryable.
- Metadata: `get_dataset` shows the full description as plain text (HTML converted, capped at 12,000 characters with a truncation notice; search results keep a 300-character excerpt), the licence and access information as text, and the default layer's `Data last edited` / `Schema last edited` dates from its `editingInfo`. `get_schema` adds the length of string fields and coded-value or range domains when the service defines them, and a `Definition:` (and `Units:`) line per field when the item's formal metadata (`metadata.xml` on ArcGIS Online, FGDC/ArcGIS `eainfo` section) defines it. HUD's LIHTC layer, for example, defines 118 of its fields there. Metadata is best-effort: a missing, malformed or oversized document never fails the call. Documents and lasting misses (404, malformed, oversized) are cached per item, while network errors and 5xx replies are retried on the next call. Private items get no definitions, since the request carries no token; and the metadata's own domains are used only when the service has none. Definitions are matched to the layer by entity name; a document that describes another layer of the service is not used for the one you asked about.
- Source block: `query_data` and `aggregate_data` end with the dataset title and Hub page, the layer URL queried, the data edit date (when the layer description has already been read, e.g. by `get_dataset` or `get_schema`, so it costs no extra request), the licence, and the retrieval time.
- `layer`: a Feature Service can hold several layers and tables. `get_dataset` lists them (`0: SAFMR_Zip_Code_Tab_Areas (polygon layer) [default]`, `1: SAFMR_table (table)`), and `get_schema`/`query_data` take `layer` to reach one other than the default. The default is the layer the item's URL names, otherwise the service's first layer (then first table). An id the service does not list is refused with the list of valid ids.
- `aggregate_data` runs one `outStatistics` query instead of paging rows to sum them client-side. `statistics` is a list of `{"type": "sum", "field": "HCV_PUBLIC", "as": "vouchers"}` (types: count, sum, avg, min, max, stddev; a `count` without a field counts records by the object-id field), `group_by` a list of fields. Field names must exist in the layer's schema (matched case-insensitively); `where` and `having` go through the WHERE validator and `order_by` through the sort-order validator. Nulls are excluded from every statistic, so a sum over suppressed values is a lower bound. Layers that report `supportsStatistics: false` are refused with a pointer to `query_data`.
- `query_data` paging: the reply starts with `Returned N of M matching record(s) (offset: O, limit: L)` and, when more records match, ends with `Next page: offset=…`. `offset` maps to `resultOffset` and `order_by` to `orderByFields` (field names, each optionally `ASC`/`DESC`; anything else is refused). Pass `order_by` for stable paging. The total comes from a `returnCountOnly` request, which is skipped when a first page comes back short of `limit`.
- `query_data` output: `format` is `text` (default, `Record N:` blocks), `json` (an array with one object per line) or `csv` (a header row plus one line per record). Every returned record is shown; if the reply would pass the response size limit, whole records are dropped from the end, a `Showing N of M record(s)` notice is added, and the next offset accounts for it.

### Implementation Notes

**Two-hop resolution.** `query_data` first fetches the dataset metadata via `get_dataset` to resolve the Feature Service URL, then queries the Feature Service directly. Always call `get_dataset` first and check the `service_url` field is non-empty before calling `query_data`.

**WHERE clause validation.** The `where` parameter (and `aggregate_data`'s `having`) is validated by `WhereValidator`, and `order_by` by `WhereValidator.validate_order_by`, before anything is sent to the Feature Service. Clauses with forbidden keywords are rejected before the network call: write and DDL statements, and also `SELECT`, `UNION`, `INTERSECT` and `EXCEPT`, since a WHERE or HAVING fragment never needs a subquery or set operator. Keywords inside quoted values (`status = 'SELECT'`) and field names that merely contain them (`UNION_NAME`) are allowed. Quotes inside values are escaped by doubling (`'O''Brien'`); a backslash-escaped quote (`\'`) is refused, since some backends would end the literal there.

**Feature Service host restriction.** A dataset record could point `query_data`/`get_schema` at an arbitrary host (SSRF), so the plugin validates the Feature Service URL before querying it. Always trusted: `*.arcgis.com`, the `portal_url` host, and anything in `trusted_service_hosts`. Hub catalogs routinely reference services self-hosted on city GIS domains (`gis.charlottenc.gov`, `maps2.dcgis.dc.gov`, `gis.indy.gov`), so by default (`auto_trust_hub_services: true`) a Hub-referenced URL is also accepted when it is https, on a public DNS name (never an IP literal, single label, or `.internal`/`.local` name), and has an ArcGIS REST path (`/rest/services/.../FeatureServer|MapServer[/layer]`). The bearer `token` is never sent to auto-trusted hosts. A refused URL raises an error beginning `untrusted_service_host: '<host>'` that names the host to add to `trusted_service_hosts`. Set `auto_trust_hub_services: false` to require an explicit allow-list.

**Schema fallback.** `get_schema` reads the layer metadata endpoint (`.../FeatureServer/<layer>?f=json`). Some self-hosted services return HTML or an ArcGIS error envelope there while `/query` works; in that case the plugin derives the field list from a one-row query instead of failing.

**Layer resolution.** If the dataset's service URL points at a `FeatureServer` or `MapServer` root without a layer index (e.g. `.../FeatureServer`), the plugin reads the service description and uses its first layer, then its first table, falling back to `/0` only when the description cannot be read (HUD services whose only layer is id 4 or 13 are reachable this way). Pass `layer` to choose another one. Service and layer descriptions are cached per plugin instance.

**Queryable item types.** `query_data` only works on the following ArcGIS item types:

| Item Type                | Queryable                 |
| ------------------------ | ------------------------- |
| Feature Layer            | Yes                       |
| Feature Service          | Yes                       |
| Map Service              | Yes                       |
| Table                    | Yes                       |
| Web Map, Dashboard, etc. | No — raises a clear error |

### ArcGIS API

This plugin uses two API layers:

- **Hub Search API** (OGC API - Records) — catalog search and aggregations
- **ArcGIS Feature Service** query endpoint — data queries

---

## Socrata Plugin

For Socrata-based open data portals (e.g., data.cityofchicago.org, data.cityofnewyork.us, data.seattle.gov).

**Note:** An App Token is optional but recommended. Without one, requests share the portal's per-IP pool and may be throttled. Register for a free token at [https://dev.socrata.com/register](https://dev.socrata.com/register), or generate one from a portal's *Developer Settings → App Tokens*. Leave `app_token` unset rather than guessing: some portals (e.g. data.cdc.gov) serve untokened requests fine but reject an invalid token with HTTP 403.

**Careful — App Token vs. API Key:** Socrata's developer console also offers a separate "API Key" credential (*Developer Settings → API Keys*), which issues a **Key ID + Key Secret pair** for HTTP Basic Auth on authenticated requests (writes, private datasets). This plugin does not implement Basic Auth — it sends `app_token` bare as the `X-App-Token` header, so only a real App Token works here. Pasting an API Key's Key ID in as `app_token` fails silently for some tools and not others: `search_datasets`/`get_dataset` keep working, but `query_dataset` fails with `"Invalid app_token specified"` (HTTP 403) on the `/resource/{id}.json` endpoint. No secret/private key is needed for public open-data portals — the bare App Token is sufficient.

### Configuration

```yaml
plugins:
  socrata:
    enabled: true
    base_url: "https://data.yourcity.gov"
    portal_url: "https://data.yourcity.gov"
    city_name: "Your City"
    app_token: "${SOCRATA_APP_TOKEN}" # Optional (recommended); omit to send no X-App-Token header
    timeout: 30 # HTTP timeout in seconds (default: 30)
```

### Tools

| Tool                                             | Description                                     |
| ------------------------------------------------ | ----------------------------------------------- |
| `socrata__search_datasets(query, limit)`         | Search for datasets in the portal catalog       |
| `socrata__get_dataset(dataset_id)`               | Get full metadata for a dataset (4x4 ID)        |
| `socrata__get_schema(dataset_id)`                | Get column schema for constructing SoQL queries |
| `socrata__query_dataset(dataset_id, soql_query)` | Query data using SoQL                           |
| `socrata__list_categories()`                     | List all categories with dataset counts         |
| `socrata__execute_sql(dataset_id, soql)`         | Execute raw SoQL SELECT (advanced)              |

### Typical Workflow

```
list_categories → search_datasets → get_dataset → get_schema → query_dataset
```

### SoQL Notes

- `GROUP BY` is required whenever using `COUNT()` or any aggregation.
- Boolean fields use `= true` / `= false`, not `= 'Y'` or `= 1`.
- For conditional counts: `SUM(CASE WHEN col = true THEN 1 ELSE 0 END)`.
- `LIMIT` caps returned rows and can affect aggregation results.

### Implementation Notes

**`execute_sql` security.** Raw SoQL is validated by `SoQLValidator` before execution. Only `SELECT` statements are allowed — `INSERT`, `UPDATE`, `DELETE`, `DROP`, and all other mutations are blocked.

**Retry behavior.** All Discovery API and SODA3 calls automatically retry up to 3 times with exponential backoff (2–10 seconds) via `tenacity`. `RuntimeError` and `HTTPStatusError` are not retried (they indicate a hard failure, not a transient one).

**`list_categories` fallback.** The Discovery API's `facets` parameter often returns empty results for domain-scoped catalog requests (e.g. Chicago). When this happens, the plugin automatically falls back to paginating all datasets and deriving categories from the `domain_category` field in each result.

**Computed region columns.** `get_schema` may return `:@computed_region_*` columns at the end of the schema list. These are system-generated geographic columns — they are not useful for SoQL queries and can be ignored.

**Dual-client architecture.** The plugin uses two separate HTTP clients: Discovery API calls go to `api.us.socrata.com` (catalog search and categories), and SODA3 calls go to the portal's own domain (schema, metadata, data queries). Both clients share the `X-App-Token` header.

### Socrata API

This plugin uses two Socrata API layers:

- **Discovery API** (api.us.socrata.com) — catalog search, categories
- **SODA3** (portal domain) — dataset metadata, schema, data queries

See [Socrata developer documentation](https://dev.socrata.com/) for details.

---

## Opendatasoft Plugin

For Opendatasoft-based open data portals using the Explore API v2.1 (e.g., data.longbeach.gov, public.opendatasoft.com).

**Note:** Public Opendatasoft portals require no credentials. An API key is only needed for private datasets.

### Configuration

```yaml
plugins:
  opendatasoft:
    enabled: true
    base_url: "https://data.longbeach.gov"    # Portal API base URL
    portal_url: "https://data.longbeach.gov"  # Public portal URL
    city_name: "Long Beach"                   # City/organization name
    timeout: 30.0                             # HTTP timeout (default: 30)
    api_key: "${ODS_API_KEY}"                 # Optional: private datasets only
```

### Tools

| Tool                                                                          | Description                                                        |
| ----------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| `opendatasoft__search_datasets(query, limit)`                                 | Search the portal catalog (full-text via ODSQL `search()`)         |
| `opendatasoft__get_dataset(dataset_id)`                                       | Dataset metadata: publisher, license, dates, record/field counts   |
| `opendatasoft__get_schema(dataset_id)`                                        | Field names, types and descriptions for ODSQL clauses              |
| `opendatasoft__query_data(dataset_id, where, select, order_by, limit)`        | Query records with ODSQL (limit capped at 100)                     |
| `opendatasoft__aggregate_data(dataset_id, metrics, group_by, where, order_by, limit)` | Aggregate records with GROUP BY                            |
| `opendatasoft__list_categories()`                                             | List portal themes with dataset counts                             |

### ODSQL Notes

The Explore API takes ODSQL fragments rather than full SQL statements:

- String literals use double quotes: `status = "Open"`. Single quotes also work.
- Full-text matching uses `search("text")`; wildcards use `like`, e.g. `name like "North*"`.
- `select` supports fields and aggregates — `count(*)`, `count(field)`, `count(distinct field)`, `sum()`, `avg()`, `min()`, `max()` — each with an `as alias`.
- `group_by` takes a comma-separated list of field names.
- `order_by` takes `field ASC|DESC`, and may reference a `select` alias.
- The records endpoint returns at most 100 rows per call.

### Implementation Notes

**Clause validation.** `ODSQLValidator` (built on the shared `BaseQueryValidator`) checks every `where`/`select`/`group_by`/`order_by` fragment before dispatch: forbidden SQL keywords are rejected outside of quoted literals (keywords inside literals are treated as data), and `aggregate_data` whitelists group-by fields, metric aliases and aggregate expressions.

**Global aggregates.** `aggregate_data` without `group_by` returns a single row.

### Opendatasoft API

This plugin uses the Explore API v2.1 (`{base_url}/api/explore/v2.1`):

- `/catalog/datasets` - Catalog list/search
- `/catalog/datasets/{dataset_id}` - Dataset metadata including fields
- `/catalog/datasets/{dataset_id}/records` - Record queries and aggregations
- `/catalog/facets?facet=theme` - Portal-wide themes with counts

See [Opendatasoft Explore API documentation](https://help.opendatasoft.com/apis/ods-explore-v2/) for details.

---

## Custom Plugins

If your portal doesn't use CKAN, ArcGIS Hub, Socrata, or Opendatasoft, you can create a custom plugin. See [Custom Plugins Guide](CUSTOM_PLUGINS.md) for instructions.

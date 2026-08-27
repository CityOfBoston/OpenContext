# Built-in Plugins Reference

OpenContext includes built-in plugins for CKAN, Socrata, ArcGIS Hub, and Opendatasoft open data portals.

## CKAN Plugin

For CKAN-based open data portals (e.g., data.boston.gov, data.gov, data.gov.uk).

### Configuration

```yaml
plugins:
  ckan:
    enabled: true
    base_url: "https://data.yourcity.gov"       # CKAN API base URL
    portal_url: "https://data.yourcity.gov"     # Public portal URL
    city_name: "Your City"                      # City/organization name
    timeout: 120                                # HTTP timeout in seconds
    api_key: "${CKAN_API_KEY}"                  # Optional: API key
```

### Tools

- `ckan__search_datasets(query, limit)` - Free-text search; header reports the catalog-wide match count
- `ckan__list_datasets(query, organization, tag, format, license, group, sort, limit, offset)` - Browse the catalog with exact-match filters, sorting (default: most recently modified first) and paging; returns the total count plus organization, modified date, resource count and formats per dataset
- `ckan__get_catalog_stats(facets, query, organization, tag, format, license, group, limit)` - Count public datasets overall and per organization / tag / resource format / license / group (from the portal search index); values it returns are the exact filter values `list_datasets` accepts
- `ckan__get_dataset(dataset_id, max_resources)` - Full dataset metadata: organization (title + slug), license, created/modified dates, tags, groups, and every resource with ID, format, created/modified dates, size, DataStore flag, download URL and description. Datasets split by year expose one resource per year, so resource names/dates/URLs date each slice. `max_resources` defaults to 50 (max 500)
- `ckan__query_data(resource_id, filters, limit)` - Query data from a resource
- `ckan__get_schema(resource_id)` - Get schema for a resource
- `ckan__execute_sql(sql)` - Execute a validated `SELECT` query against the datastore
- `ckan__aggregate_data(resource_id, metrics, group_by, filters, having, order_by, limit)` - GROUP BY aggregations without writing SQL

**`aggregate_data` notes:**
- `metrics` maps alias to expression, e.g. `{"cnt": "count(*)", "avg_amt": "avg(amount)"}`. Supported: `count(*)`, `count(field)`, `count(distinct field)`, `sum()`, `avg()`, `min()`, `max()`, `stddev()`, `variance()`
- `having` keys are aggregate expressions or declared metric aliases; string values may carry a comparison operator (`{"count(*)": ">= 5"}`), bare numbers default to `>`
- `order_by` accepts `"field"`, `"-field"` (descending), or `"field ASC|DESC"`
- All identifiers and expressions are validated against safe whitelists before SQL is built

**Catalog browsing notes:**
- `list_datasets` / `get_catalog_stats` filters are exact matches on CKAN's search index (Solr). Filter names are whitelisted and values are quoted/escaped as Solr phrases, so operators and wildcards in values are inert
- Resource download URLs are shown verbatim only when their host is the portal's own host (or a subdomain); other hosts render as `(external: hostname)`
- Counts cover public datasets only (private/draft datasets are not in the search index)

### Examples

**Search datasets:**
```
Search for datasets about housing in Boston
```

**Get dataset:**
```
Get details about the "311 Service Requests" dataset
```

**Query data:**
```
Query the first 10 records from resource abc123
```

## CKAN API

This plugin uses CKAN's Action API:
- `/api/3/action/package_search` - Search datasets
- `/api/3/action/package_show` - Get dataset
- `/api/3/action/datastore_search` - Query data

See [CKAN API documentation](https://docs.ckan.org/en/latest/api/) for details.

## Socrata Plugin

For Socrata-based open data portals (e.g., data.cityofchicago.org, data.cityofnewyork.us, data.seattle.gov).

**Note:** Socrata requires a free app token. Register at [https://dev.socrata.com/register](https://dev.socrata.com/register).

### Configuration

```yaml
plugins:
  socrata:
    enabled: true
    base_url: "https://data.cityofboston.gov"
    portal_url: "https://data.cityofboston.gov"
    city_name: "Boston"
    app_token: "${SOCRATA_APP_TOKEN}"   # Required
    timeout: 30.0                        # HTTP timeout (default: 30)
```

### Tools

- `socrata__search_datasets(query, limit)` - Search for datasets in the portal catalog; header reports the catalog-wide match count
- `socrata__get_dataset(dataset_id)` - Full metadata for a dataset (4x4 ID): source/attribution, license, created/published/modified dates, row and column counts, downloads/views, category, tags
- `socrata__get_schema(dataset_id)` - Get column schema for constructing SoQL queries
- `socrata__query_dataset(dataset_id, soql_query)` - Query data using SoQL
- `socrata__execute_sql(dataset_id, soql)` - Execute raw SoQL query (advanced, similar to CKAN execute_sql)
- `socrata__list_categories()` - List all categories with dataset counts

### Examples

**Search datasets:**
```
Search for datasets about housing in Boston
```

**Get dataset:**
```
Get details about dataset wc4w-4jew
```

**Get schema (call before query_dataset):**
```
Get schema for dataset wc4w-4jew
```

**Query data:**
```
Query dataset wc4w-4jew with: SELECT * WHERE year > 2020 LIMIT 50
```

**List categories:**
```
List all dataset categories on Boston's open data portal
```

### Socrata API

This plugin uses two Socrata API layers:
- **Discovery API** (api.us.socrata.com) - Catalog search, categories
- **SODA3** (portal domain) - Dataset metadata, schema, data queries

See [Socrata developer documentation](https://dev.socrata.com/) for details.

## ArcGIS Plugin

For ArcGIS Hub / ArcGIS Open Data portals (e.g., hub.arcgis.com, city Hub sites).

### Configuration

```yaml
plugins:
  arcgis:
    enabled: true
    portal_url: "https://hub.arcgis.com"   # ArcGIS Hub portal URL
    city_name: "Your City"
    timeout: 120
    # token: "${ARCGIS_TOKEN}"             # Optional: Bearer token for private items
    # trusted_service_hosts:               # Extra hosts for self-hosted Feature Services
    #   - "gis.yourcity.gov"
```

### Tools

- `arcgis__search_datasets(query, limit)` - Search the Hub catalog (query required)
- `arcgis__get_dataset(dataset_id)` - Hub item metadata: owner/organization, created/modified/last-edit dates, record count, size, license, categories, type keywords, item and service URLs (host-gated)
- `arcgis__get_aggregations(field, query)` - Aggregate counts for a field
- `arcgis__get_schema(dataset_id)` - Get Feature Service layer schema
- `arcgis__query_data(dataset_id, where, out_fields, limit)` - Query records (limit max 1000)

### Security notes

Feature Service URLs resolved from Hub metadata are restricted to `*.arcgis.com`, the configured portal host, or hosts listed in `trusted_service_hosts` (exact host or subdomain) — an SSRF guard. Add self-hosted city GIS domains to `trusted_service_hosts` when Hub datasets reference them. `where` clauses are validated with a forbidden-keyword scan that skips quoted string literals, so values like `status = 'SET'` are fine.

### ArcGIS API

This plugin uses a two-hop flow: the Hub API resolves a dataset ID to its Feature Service URL, then records are queried from that service.

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

- `opendatasoft__search_datasets(query, limit)` - Search the portal catalog (full-text via ODSQL `search()`)
- `opendatasoft__get_dataset(dataset_id)` - Dataset metadata: publisher, license, attribution, modified/data-processed dates, record and field counts, theme, keywords, references
- `opendatasoft__get_schema(dataset_id)` - Get field names, types and descriptions for ODSQL clauses
- `opendatasoft__query_data(dataset_id, where, select, order_by, limit)` - Query records with ODSQL (limit capped at 100)
- `opendatasoft__aggregate_data(dataset_id, metrics, group_by, where, order_by, limit)` - Aggregate records with GROUP BY
- `opendatasoft__list_categories()` - List portal themes with dataset counts

### ODSQL notes

The Explore API takes ODSQL fragments rather than full SQL statements:

- String literals use double quotes: `status = "Open"`. Single quotes also work.
- Full-text matching uses `search("text")`; wildcards use `like`, e.g. `name like "North*"`.
- `select` supports fields and aggregates — `count(*)`, `count(field)`, `count(distinct field)`, `sum()`, `avg()`, `min()`, `max()` — each with an `as alias`.
- `group_by` takes a comma-separated list of field names.
- `order_by` takes `field ASC|DESC`, and may reference a `select` alias.
- The records endpoint returns at most 100 rows per call.

Clauses are validated before dispatch: forbidden SQL keywords are rejected outside of quoted literals (keywords inside literals are treated as data), and `aggregate_data` whitelists group-by fields, metric aliases and aggregate expressions.

### Examples

**Search datasets:**
```
Search for datasets about police calls in Long Beach
```

**Get dataset:**
```
Get details about the police-calls-for-service dataset
```

**Get schema (call before query_data):**
```
Get schema for dataset police-calls-for-service
```

**Query data:**
```
Query police-calls-for-service where call_type = "Noise", ordered by received DESC
```

**Aggregate data:**
```
Count police calls by call_type in Long Beach
```

**List categories:**
```
List all dataset themes on Long Beach's open data portal
```

### Opendatasoft API

This plugin uses the Explore API v2.1 (`{base_url}/api/explore/v2.1`):
- `/catalog/datasets` - Catalog list/search
- `/catalog/datasets/{dataset_id}` - Dataset metadata including fields
- `/catalog/datasets/{dataset_id}/records` - Record queries and aggregations
- `/catalog/facets?facet=theme` - Portal-wide themes with counts

See [Opendatasoft Explore API documentation](https://help.opendatasoft.com/apis/ods-explore-v2/) for details.

## Custom Plugins

If your portal doesn't use CKAN, you can create a custom plugin. See [Custom Plugins Guide](CUSTOM_PLUGINS.md) for instructions.

## Examples

See [examples/](../examples/) for complete configuration examples.

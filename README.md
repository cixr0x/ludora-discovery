# Ludora

Collect Mexican online boardgame and tabletop store listings with the Brave Search API.

The collector searches Brave, deduplicates candidate store domains, fetches each store website, and stores dirty discovery records in Postgres with:

- `store_name`
- `canonical_domain`
- `website_url`
- `instagram_url`
- `facebook_url`
- `city`
- `state`
- `country`
- `store_logo`
- `status`
- `confidence`
- `source_queries`
- `evidence`

The filter is intentionally strict: accepted results must look like Mexican online stores that sell board games, tabletop games, card games, miniatures, or TCG products. Marketplaces, social-only pages, blogs, news, publishers, and event pages are excluded where possible.

## Quick Start

From `C:\PROJECTS\ludora`:

```powershell
python .\scripts\collect_boardgame_stores_mx.py --query-scope expanded --verbose
```

The script reads `BRAVE_SEARCH_API_KEY`, `LUDORA_DATABASE_URL`, and BGG configuration from `.env` by default:

```text
BRAVE_SEARCH_API_KEY=your_brave_key_here
LUDORA_DATABASE_URL=postgresql://user:password@localhost:5432/ludora
BGG_API_TOKEN=your_bgg_token_here
BGG_API_BASE_URL=https://boardgamegeek.com/xmlapi2
```

You can still override them with environment variables, `--api-key`, or `--database-url`.

Database output:

```text
discovery_store_candidates
```

## Database Persistence

Apply the shared schema before the first database-backed run:

```powershell
psql "$env:LUDORA_DATABASE_URL" -f ..\database\schema.sql
```

Store candidates are persisted by default. To also extract raw listing candidates from accepted store homepages:

```powershell
python .\scripts\collect_boardgame_stores_mx.py --collect-listings --listing-limit 100
```

The database path writes only dirty discovery tables. Curated `stores`, `items`, and `offers` are created by the admin workflow.

## Discovery API

Run the local operations API when admin needs to start discovery from the browser:

```powershell
$env:PYTHONPATH='src'
python -m ludora.api --host 127.0.0.1 --port 8001
```

The API reads `BRAVE_SEARCH_API_KEY`, `LUDORA_DATABASE_URL`, and `BGG_API_TOKEN` from `.env` by default, matching the CLI. For local development you can point it at the admin-service env file if that is where the shared credentials live:

```powershell
python -m ludora.api --host 127.0.0.1 --port 8001 --env-file ..\ludora-admin\ludora-admin-service\.env
```

Some stores block plain HTTP crawlers but allow a real browser session to read their product sitemap and product pages. Enable the browser-backed fallback before starting the API:

```powershell
$env:LUDORA_BROWSER_FETCH_ENABLED='true'
```

Browser fallback uses the installed Chrome executable when available. You can override it with:

```powershell
$env:LUDORA_BROWSER_EXECUTABLE_PATH='C:\Program Files\Google\Chrome\Application\chrome.exe'
```

Available endpoints:

```text
GET  /health
POST /operations/store-discovery-runs
POST /operations/stores/{store_id}/item-discovery-runs
POST /operations/item-update-runs
GET  /operations/store-discovery-runs/latest
GET  /operations/store-discovery-runs/{run_id}
```

Only one discovery operation can be active at a time. A second start request returns HTTP `409`.

To also export the old CSV/JSON files for manual inspection:

```powershell
python .\scripts\collect_boardgame_stores_mx.py --export-files --output-dir data
```

When `--export-files` is enabled, audit files include every discovered candidate domain that reached enrichment, including rejected domains and the rejection reasons. Use them to understand why a store you expected did not make the final dataset.

Preview the search queries without spending API credits:

```powershell
python .\scripts\collect_boardgame_stores_mx.py --dry-run-queries --query-scope core
```

For broader coverage:

```powershell
python .\scripts\collect_boardgame_stores_mx.py --query-scope full --pages 10 --verbose
```

That uses more Brave requests. Brave Web Search supports up to 20 results per request and offsets up to 9, so `--pages 10` is the broadest setting per query. `expanded` is the default balance between coverage and API usage.

## Options

```text
--query-scope core|expanded|full
--max-queries N
--count N
--pages N
--output-dir data
--env-file .env
--request-delay 1.1
--website-delay 0.3
--max-enrichment-pages 3
--include-low-confidence
--database-url postgresql://...
--collect-listings
--listing-limit 100
--export-files
--dry-run-queries
--verbose
```

## Development

Run tests:

```powershell
python -m unittest discover -s tests -v
```

Optional editable install:

```powershell
python -m pip install -e .
ludora-collect-stores --dry-run-queries
```
# ludora-search
# ludora-discovery

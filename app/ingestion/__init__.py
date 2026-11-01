"""Source ingestion: connectors, fetching, normalisation, idempotent persistence.

Layering (each layer only imports the ones above it):

    errors, models, countries   pure data + validation
    http                        fetcher port, retry policy, response validation
    connectors/*                fetch + parse for one source (no database access)
    ports                       repository interface
    runner                      orchestration: run bookkeeping, status rules, dry-run
    sql_repository              PostgreSQL implementation of the repository port
"""

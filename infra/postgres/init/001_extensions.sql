-- ==========================================================================
-- StockGambling - PostgreSQL bootstrap
-- ==========================================================================
-- Executed once, by docker-entrypoint-initdb.d, on an empty data directory.
-- Enables the extensions the ORM relies on (pg_trgm for fuzzy CEDEAR symbol
-- search, btree_gist for exclusion constraints over date ranges).
-- ==========================================================================

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS btree_gist;

-- The application must always run in UTC: market *dates* are stored separately
-- from instants, and a server running in local time silently corrupts them.
\getenv sg_database POSTGRES_DB
ALTER DATABASE :"sg_database" SET timezone TO 'UTC';

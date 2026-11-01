#!/usr/bin/env bash
# Applies the REAL migrations to a disposable PostgreSQL database and runs tests/db/checks.sql.
#
#   scripts/test_db.sh "host=localhost port=5432 user=postgres dbname=viruswatch_test"
#
# The database must be empty and disposable (the bootstrap creates roles/schemas and the checks
# insert fixture rows). Set NO_PGVECTOR=1 when the server lacks the pgvector extension; see
# tests/db/no_pgvector.sed for exactly what is substituted in that mode.
set -euo pipefail
cd "$(dirname "$0")/.."

CONN="${1:?usage: scripts/test_db.sh '<libpq connection string>'}"
psql_run() { psql -X -q -v ON_ERROR_STOP=1 "$CONN" "$@"; }

echo "==> bootstrap (Supabase stand-ins: roles, auth.users, auth.uid())"
psql_run -f tests/db/bootstrap.sql

for f in supabase/migrations/*.sql; do
  echo "==> migration $(basename "$f")"
  if [ "${NO_PGVECTOR:-0}" = "1" ]; then
    sed -f tests/db/no_pgvector.sed "$f" | psql_run -f -
  else
    psql_run -f "$f"
  fi
done

echo "==> checks"
psql_run -f tests/db/checks.sql
echo "ALL DATABASE CHECKS PASSED"

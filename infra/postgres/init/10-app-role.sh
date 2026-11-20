#!/bin/sh
# Creates the application's database role and database, and installs the extensions.
#
# The app connects as an ordinary (non-superuser) role that owns its tables, exactly
# as on managed PostgreSQL such as Render. That matters for row-level security:
# superusers bypass it, so a superuser connection would hide isolation bugs.
#
# Runs automatically when the Docker volume is first created. Safe to run again, and
# used by CI and `make test-integration` with APP_DB_NAME set to another database.
#
# Needs: POSTGRES_USER (a superuser), APP_DB_USER, APP_DB_PASSWORD, APP_DB_NAME.
# Connection settings for psql come from the usual PG* variables when set.
set -eu

psql_admin() {
  psql -v ON_ERROR_STOP=1 --no-psqlrc --username "$POSTGRES_USER" "$@"
}

psql_admin --dbname postgres \
  --set app_user="$APP_DB_USER" --set app_password="$APP_DB_PASSWORD" --set app_db="$APP_DB_NAME" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD %L',
              :'app_user', :'app_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_user') \gexec
SELECT format('ALTER ROLE %I PASSWORD %L', :'app_user', :'app_password') \gexec
SELECT format('CREATE DATABASE %I OWNER %I', :'app_db', :'app_user')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'app_db') \gexec
SQL

# pgvector is not a trusted extension, so a superuser installs it once per database.
psql_admin --dbname "$APP_DB_NAME" <<'SQL'
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS citext;
SQL

#!/bin/sh
# postgres-init: legt einen separaten App-User mit NOSUPERUSER an
# (Refs #671, ADR-017 § Plain Compose primaer + RLS-Pflicht).
#
# Hintergrund: Postgres-Image macht den per POSTGRES_USER angelegten
# Bootstrap-Login-User automatisch zum SUPERUSER. Der kann sich selbst
# nicht entrechten (Postgres: "bootstrap user must have SUPERUSER"),
# also brauchen wir einen separaten App-User mit NOSUPERUSER + LOGIN,
# damit RLS gegen Superuser-Bypass greift.
#
# Variablen aus dem Container-Env (gesetzt via docker-compose):
#   POSTGRES_USER       — Bootstrap (=postgres), Superuser
#   POSTGRES_DB         — Datenbank-Name
#   APP_DB_USER         — App-Login (NOSUPERUSER, NOBYPASSRLS)
#   APP_DB_PASSWORD     — App-Login-Passwort

set -e

if [ -z "$APP_DB_USER" ] || [ -z "$APP_DB_PASSWORD" ]; then
	echo "01-app-role.sh: APP_DB_USER / APP_DB_PASSWORD missing — abort" >&2
	exit 1
fi

psql -v ON_ERROR_STOP=1 \
	--username "$POSTGRES_USER" \
	--dbname "$POSTGRES_DB" <<-SQL
	-- App-Role anlegen, explizit ohne SUPERUSER und ohne BYPASSRLS:
	-- Damit greifen RLS-Policies aus Migration 0047 unbedingt.
	CREATE ROLE "$APP_DB_USER"
		WITH LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB
		PASSWORD '$APP_DB_PASSWORD';

	-- App-Role ist Owner der Datenbank — kann Tabellen anlegen, RLS
	-- darauf aktivieren, etc. Migrations laufen unter dieser Rolle.
	ALTER DATABASE "$POSTGRES_DB" OWNER TO "$APP_DB_USER";
	GRANT ALL PRIVILEGES ON DATABASE "$POSTGRES_DB" TO "$APP_DB_USER";
	GRANT ALL ON SCHEMA public TO "$APP_DB_USER";
SQL

#!/bin/sh
# postgres-init: legt zwei Roles an
#   - APP_DB_USER     (NOSUPERUSER, NOBYPASSRLS): Django-Runtime
#   - ADMIN_DB_USER   (NOSUPERUSER, BYPASSRLS):   seed/migrate/Wartung
#
# Hintergrund: Postgres-Image macht den per POSTGRES_USER angelegten
# Bootstrap-Login-User automatisch zum SUPERUSER. Der kann sich selbst
# nicht entrechten ("bootstrap user must have SUPERUSER"). Wir nutzen
# also POSTGRES_USER=postgres als Bootstrap und legen separat zwei
# Application-Rollen an.
#
# Idempotenz: Beide Role-Anlagen via DO-Block mit pg_roles-Check —
# das Script ist beim manuellen Re-Run auf bestehender DB safe.
#
# Refs #671, #863, ADR-005, ADR-017.
#
# Variablen aus dem Container-Env:
#   POSTGRES_USER       — Bootstrap (=postgres), Superuser
#   POSTGRES_DB         — Datenbank-Name
#   APP_DB_USER         — App-Login (NOSUPERUSER, NOBYPASSRLS)
#   APP_DB_PASSWORD     — App-Login-Passwort
#   ADMIN_DB_USER       — Admin-Login (NOSUPERUSER, BYPASSRLS)
#   ADMIN_DB_PASSWORD   — Admin-Login-Passwort

set -e

if [ -z "$APP_DB_USER" ] || [ -z "$APP_DB_PASSWORD" ]; then
	echo "01-app-role.sh: APP_DB_USER / APP_DB_PASSWORD missing — abort" >&2
	exit 1
fi

psql -v ON_ERROR_STOP=1 \
	--username "$POSTGRES_USER" \
	--dbname "$POSTGRES_DB" \
	-v app_user="$APP_DB_USER" \
	-v app_password="$APP_DB_PASSWORD" \
	-v dbname="$POSTGRES_DB" <<-'SQL'
	-- App-User: Django-Runtime, RLS-Policies greifen.
	DO $$
	BEGIN
	    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_user') THEN
	        EXECUTE format(
	            'CREATE ROLE %I WITH LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB PASSWORD %L',
	            :'app_user', :'app_password'
	        );
	    END IF;
	END
	$$;
	-- App-User wird DB-Owner; Schema-Aenderungen via Django-Migrations
	-- laufen damit unter dieser Rolle.
	ALTER DATABASE :"dbname" OWNER TO :"app_user";
	GRANT ALL PRIVILEGES ON DATABASE :"dbname" TO :"app_user";
	GRANT ALL ON SCHEMA public TO :"app_user";
	SQL

if [ -n "$ADMIN_DB_USER" ] && [ -n "$ADMIN_DB_PASSWORD" ]; then
	psql -v ON_ERROR_STOP=1 \
		--username "$POSTGRES_USER" \
		--dbname "$POSTGRES_DB" \
		-v admin_user="$ADMIN_DB_USER" \
		-v admin_password="$ADMIN_DB_PASSWORD" \
		-v app_user="$APP_DB_USER" \
		-v dbname="$POSTGRES_DB" <<-'SQL'
		-- Admin-User: Operator-Tasks (seed, migrate, retention-pruning).
		-- BYPASSRLS umgeht RLS-Policies, NOSUPERUSER verhindert Eskalation.
		DO $$
		BEGIN
		    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'admin_user') THEN
		        EXECUTE format(
		            'CREATE ROLE %I WITH LOGIN NOSUPERUSER BYPASSRLS NOCREATEROLE NOCREATEDB PASSWORD %L',
		            :'admin_user', :'admin_password'
		        );
		    END IF;
		END
		$$;
		GRANT ALL PRIVILEGES ON DATABASE :"dbname" TO :"admin_user";
		GRANT ALL ON SCHEMA public TO :"admin_user";
		GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO :"admin_user";
		GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO :"admin_user";
		-- Damit der Admin-User auch Tabellen sieht, die der App-User
		-- spaeter per Django-Migration anlegt:
		ALTER DEFAULT PRIVILEGES FOR ROLE :"app_user" IN SCHEMA public
		    GRANT ALL PRIVILEGES ON TABLES TO :"admin_user";
		ALTER DEFAULT PRIVILEGES FOR ROLE :"app_user" IN SCHEMA public
		    GRANT ALL PRIVILEGES ON SEQUENCES TO :"admin_user";
		-- Role-Membership: Admin erbt damit Owner-Rechte des App-Users —
		-- noetig fuer DROP POLICY / ALTER TABLE in Migrationen, weil
		-- BYPASSRLS allein nicht fuer DDL ausreicht. Refs #863.
		GRANT :"app_user" TO :"admin_user";
		SQL
fi

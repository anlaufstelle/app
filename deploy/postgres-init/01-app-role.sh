#!/bin/sh
# postgres-init: legt zwei Roles an und trennt Runtime von DB-Ownership.
#   - APP_DB_USER     (NOSUPERUSER, NOBYPASSRLS): Django-Runtime, KEIN Owner,
#                                                 nur DML-Rechte (SELECT/INSERT/
#                                                 UPDATE/DELETE) + Sequenz-/Schema-
#                                                 USAGE.
#   - ADMIN_DB_USER   (NOSUPERUSER, BYPASSRLS):   Owner der DB + aller Objekte;
#                                                 fuehrt migrate/seed/Wartung aus.
#
# A1.4 (Refs #1026): Frueher war die App-Rolle DB-Owner. Als Table-Owner kann
# eine Rolle die Append-Only-Trigger (``auditlog_immutable``,
# ``eventhistory_no_update``/``_no_delete``) per Owner-DDL
# ``ALTER TABLE ... DISABLE TRIGGER`` abschalten — ein Audit-Bypass, den
# ``ENABLE ALWAYS`` allein NICHT verhindert. Deshalb ist die Runtime-Rolle jetzt
# strikt von der Ownership getrennt: Owner ist die BYPASSRLS-Admin-/Wartungs-
# Rolle (die den Bypass fuer die legitimen DSGVO-/Retention-Pfade ohnehin
# braucht, siehe ``services/system/_db_admin.py``), die App-Rolle bekommt nur
# DML. Damit kann die kompromittierte Runtime-Verbindung weder Trigger
# DISABLE-n (kein Owner) noch ``session_replication_role`` setzen (kein GRANT).
#
# Hintergrund: Postgres-Image macht den per POSTGRES_USER angelegten
# Bootstrap-Login-User automatisch zum SUPERUSER. Der kann sich selbst
# nicht entrechten ("bootstrap user must have SUPERUSER"). Wir nutzen
# also POSTGRES_USER=postgres als Bootstrap und legen separat zwei
# Application-Rollen an.
#
# Idempotenz: Beide Role-Anlagen via SELECT … WHERE NOT EXISTS + \gexec —
# das Script ist beim manuellen Re-Run auf bestehender DB safe. Owner-/GRANT-
# Statements sind ihrerseits idempotent (mehrfaches Setzen ist unschaedlich).
# Kein DO-$$-Block: psql interpoliert :'var'-Variablen nicht innerhalb
# von Dollar-Quotes; ein DO-Block saehe das Literal ":'app_user'" und
# wuerde jeden frischen Init abbrechen (Refs #1039).
#
# Ownership-Fluss: Weil migrate/seed als Admin (= Owner) laufen, entstehen
# frisch migrierte Tabellen admin-owned. Die ``ALTER DEFAULT PRIVILEGES FOR
# ROLE <admin>`` weisen jeder danach von der Admin-Rolle erzeugten Tabelle/
# Sequenz automatisch die App-DML-Rechte zu — kein per-Tabelle-Owner-Fix mehr
# noetig (``normalize_db_ownership`` wird damit zum No-op, weil current_user ==
# db_owner). Ohne gesetzte ADMIN-Rolle faellt das Script auf das Legacy-
# Einzelrollen-Modell zurueck (App = Owner) — nur fuer einfache Setups ohne
# Drei-Rollen-Trennung.
#
# Refs #671, #863, #1026, #1039, ADR-005, ADR-017, ADR-020.
#
# Variablen aus dem Container-Env:
#   POSTGRES_USER       — Bootstrap (=postgres), Superuser
#   POSTGRES_DB         — Datenbank-Name
#   APP_DB_USER         — App-Login (NOSUPERUSER, NOBYPASSRLS), kein Owner
#   APP_DB_PASSWORD     — App-Login-Passwort
#   ADMIN_DB_USER       — Admin-/Owner-Login (NOSUPERUSER, BYPASSRLS)
#   ADMIN_DB_PASSWORD   — Admin-Login-Passwort

set -e

if [ -z "$APP_DB_USER" ] || [ -z "$APP_DB_PASSWORD" ]; then
	echo "01-app-role.sh: APP_DB_USER / APP_DB_PASSWORD missing — abort" >&2
	exit 1
fi

# --- App-Rolle anlegen (Django-Runtime, RLS-geschuetzt) --------------------
psql -v ON_ERROR_STOP=1 \
	--username "$POSTGRES_USER" \
	--dbname "$POSTGRES_DB" \
	-v app_user="$APP_DB_USER" \
	-v app_password="$APP_DB_PASSWORD" \
	-v dbname="$POSTGRES_DB" <<-'SQL'
	-- App-User: Django-Runtime, RLS-Policies greifen. NOBYPASSRLS ist der
	-- Linchpin der RLS-Architektur (ADR-005/ADR-020) und bleibt unveraendert.
	SELECT format(
	    'CREATE ROLE %I WITH LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB PASSWORD %L',
	    :'app_user', :'app_password'
	)
	WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_user')
	\gexec
	-- Verbindungsrecht — Ownership/Schema-Rechte je nach Modell unten.
	GRANT CONNECT ON DATABASE :"dbname" TO :"app_user";
	SQL

if [ -n "$ADMIN_DB_USER" ] && [ -n "$ADMIN_DB_PASSWORD" ]; then
	# --- Drei-Rollen-Modell: Admin = Owner, App = DML-only ----------------
	psql -v ON_ERROR_STOP=1 \
		--username "$POSTGRES_USER" \
		--dbname "$POSTGRES_DB" \
		-v admin_user="$ADMIN_DB_USER" \
		-v admin_password="$ADMIN_DB_PASSWORD" \
		-v app_user="$APP_DB_USER" \
		-v dbname="$POSTGRES_DB" <<-'SQL'
		-- Admin-User: Owner + Operator-Tasks (seed, migrate, retention-pruning,
		-- DSGVO-Anonymisierung). BYPASSRLS umgeht RLS-Policies fuer die
		-- facility-uebergreifende Wartung, NOSUPERUSER verhindert Eskalation.
		SELECT format(
		    'CREATE ROLE %I WITH LOGIN NOSUPERUSER BYPASSRLS NOCREATEROLE NOCREATEDB PASSWORD %L',
		    :'admin_user', :'admin_password'
		)
		WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'admin_user')
		\gexec
		-- Admin wird Owner der Datenbank und braucht CREATE im Schema fuer
		-- Migrationen (CREATE TABLE / ALTER TABLE / CREATE POLICY ...).
		GRANT ALL ON SCHEMA public TO :"admin_user";
		ALTER DATABASE :"dbname" OWNER TO :"admin_user";
		GRANT ALL PRIVILEGES ON DATABASE :"dbname" TO :"admin_user";
		-- App-Rolle: NUR DML + Lesen, KEINE Ownership. Als Nicht-Owner kann sie
		-- die Append-Only-Trigger nicht per DISABLE TRIGGER abschalten (A1.4).
		GRANT USAGE ON SCHEMA public TO :"app_user";
		-- Alle KUENFTIG von der Admin-Rolle (= migrate) erzeugten Tabellen/
		-- Sequenzen bekommen automatisch die App-DML-Rechte. Greift, weil das
		-- Init-Script VOR dem ersten migrate laeuft und migrate als Admin
		-- connectet (Refs #863).
		ALTER DEFAULT PRIVILEGES FOR ROLE :"admin_user" IN SCHEMA public
		    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"app_user";
		ALTER DEFAULT PRIVILEGES FOR ROLE :"admin_user" IN SCHEMA public
		    GRANT USAGE, SELECT ON SEQUENCES TO :"app_user";
		-- Bestehende Objekte (idempotenter Re-Run / Upgrade-Pfad) nachziehen.
		GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO :"app_user";
		GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO :"app_user";
		-- Refs #1016 A1.2: session_replication_role ist ein SUPERUSER-Parameter.
		-- Die NOSUPERUSER-Admin-Rolle darf ihn nur mit explizitem GRANT SET ON
		-- PARAMETER (PostgreSQL 15+) setzen.
		GRANT SET ON PARAMETER session_replication_role TO :"admin_user";
		SQL
else
	# --- Legacy-Einzelrollen-Fallback: App = Owner -----------------------
	# Kein ADMIN_DB_USER gesetzt -> keine Rollentrennung moeglich. Die App-
	# Rolle wird Owner (Alt-Verhalten). NUR fuer einfache Setups; das
	# Drei-Rollen-Modell (ADR-020) ist die empfohlene, sichere Konfiguration.
	psql -v ON_ERROR_STOP=1 \
		--username "$POSTGRES_USER" \
		--dbname "$POSTGRES_DB" \
		-v app_user="$APP_DB_USER" \
		-v dbname="$POSTGRES_DB" <<-'SQL'
		ALTER DATABASE :"dbname" OWNER TO :"app_user";
		GRANT ALL ON SCHEMA public TO :"app_user";
		SQL
fi

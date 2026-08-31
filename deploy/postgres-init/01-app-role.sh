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
# Bewusst INIT-ONLY (Refs #1964): Bestehende Rollen werden hier nicht per
# ``ALTER ROLE ... PASSWORD`` an die aktuelle Env angeglichen, weil der
# Postgres-Init-Hook bei vorhandenem Volume gar nicht erneut laeuft. Ein solcher
# Zweig waere nur scheinbar idempotent. Fuer Bestandscluster gilt stattdessen
# ``scripts/ops/rotate-db-passwords.sh`` + Ops-Runbook § DB-Passwortrotation.
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

# --- Namensinvarianten (Refs #2086) ---------------------------------------
# Bis #2086 prueften die Preflights nur Vorhandensein und Passwort-Entropie,
# nicht die NAMEN. Das war fail-open: bei APP_DB_USER == Bootstrap-Rolle
# (typisch "postgres") ueberspringt die "WHERE NOT EXISTS"-Klausel unten das
# CREATE ROLE stillschweigend — die Runtime bleibt dann der SUPERUSER, den
# dieses Script gerade abloesen soll, und kein Fehler weist darauf hin.
# Bei APP == ADMIN faellt zusaetzlich die Runtime-/Owner-Trennung (A1.4) weg:
# dieselbe Rolle waere Owner UND Django-Runtime und koennte die
# Append-Only-Trigger per DDL abschalten.
# Der Bootstrap-Name ist per Postgres-Image-Konvention "postgres"; wir pruefen
# beides — den konfigurierten POSTGRES_USER und das Literal.
_BOOTSTRAP_ROLE="${POSTGRES_USER:-postgres}"
if [ "$APP_DB_USER" = "$_BOOTSTRAP_ROLE" ] || [ "$APP_DB_USER" = "postgres" ]; then
	echo "01-app-role.sh: APP_DB_USER ('$APP_DB_USER') darf nicht die Bootstrap-Rolle sein — abort" >&2
	echo "  Die Bootstrap-Rolle ist SUPERUSER und kann sich nicht selbst entrechten;" >&2
	echo "  die App-Rolle muss ein eigener, getrennter Login sein (ADR-005/ADR-020)." >&2
	exit 1
fi
if [ -n "${ADMIN_DB_USER:-}" ]; then
	if [ "$ADMIN_DB_USER" = "$_BOOTSTRAP_ROLE" ] || [ "$ADMIN_DB_USER" = "postgres" ]; then
		echo "01-app-role.sh: ADMIN_DB_USER ('$ADMIN_DB_USER') darf nicht die Bootstrap-Rolle sein — abort" >&2
		exit 1
	fi
	if [ "$ADMIN_DB_USER" = "$APP_DB_USER" ]; then
		echo "01-app-role.sh: ADMIN_DB_USER und APP_DB_USER sind identisch ('$APP_DB_USER') — abort" >&2
		echo "  Damit gibt es keine Runtime-/Owner-Trennung: dieselbe Rolle waere" >&2
		echo "  DB-Owner und Django-Runtime und koennte die Append-Only-Trigger" >&2
		echo "  per 'ALTER TABLE ... DISABLE TRIGGER' abschalten (A1.4)." >&2
		exit 1
	fi
fi

# Refs #1962: Die ausgelieferten Deployment-Stacks setzen dieses Flag. Dort
# ist das Drei-Rollen-Modell kein optionaler Legacy-Fallback, und die einzige
# Stelle, die das Bootstrap-Secret sieht, muss Platzhalter/zu kurze Werte
# fail-closed abweisen. Ein manueller Legacy-Aufruf ohne Flag bleibt kompatibel.
case "${REQUIRE_ADMIN_DB_ROLE:-false}" in
	true | 1 | yes)
		if [ -z "${ADMIN_DB_USER:-}" ] || [ -z "${ADMIN_DB_PASSWORD:-}" ]; then
			echo "01-app-role.sh: ADMIN_DB_USER / ADMIN_DB_PASSWORD missing — abort" >&2
			exit 1
		fi

		validate_secret() {
			_secret_name="$1"
			_secret_value="$2"
			if [ -z "$_secret_value" ]; then
				echo "01-app-role.sh: ${_secret_name} missing — abort" >&2
				exit 1
			fi
			case "$_secret_value" in
				change-me*)
					echo "01-app-role.sh: ${_secret_name} is a change-me placeholder — abort" >&2
					exit 1
					;;
			esac
			if [ "${#_secret_value}" -lt 32 ]; then
				echo "01-app-role.sh: ${_secret_name} is shorter than 32 characters — abort" >&2
				exit 1
			fi
		}

		validate_secret POSTGRES_PASSWORD "${POSTGRES_PASSWORD:-}"
		validate_secret APP_DB_PASSWORD "$APP_DB_PASSWORD"
		validate_secret ADMIN_DB_PASSWORD "$ADMIN_DB_PASSWORD"
		;;
esac

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

# ADR-020: Drei-Rollen-Postgres-Modell (Bootstrap / App / Admin)

- **Status:** Accepted
- **Date:** 2026-05-28
- **Deciders:** Tobias Nix
- **Refs:** #902

## Context

Row Level Security ([ADR-005](005-facility-scoping-and-rls.md)) traegt nur, solange die Postgres-Rolle, mit der Django zur Laufzeit verbindet, **weder `SUPERUSER` noch `BYPASSRLS`** besitzt. Beide Attribute heben die `facility_isolation`-Policies wirkungslos aus — eine kompromittierte App- oder Service-Connection sieht dann ueber alle Facilities hinweg.

Drei realweltliche Stolperfallen bedrohen genau diese Voraussetzung:

1. **Default-`postgres`-Image** (Verhalten in 16 wie 18 identisch, Refs #1039) legt den per `POSTGRES_USER` definierten Login automatisch als **Superuser** an. Wer das `.env`-Muster „App-User = POSTGRES_USER" naiv uebernimmt, faehrt RLS-frei in Produktion — ohne Warnung. dokumentiert das als realen Selbst-Hosting-Risiko.
2. **Migrationen, Restore und einige Retention-Schritte** brauchen Rechte, die ein NOSUPERUSER/NOBYPASSRLS-User nicht hat (`CREATE EXTENSION`, `ALTER TABLE … ENABLE ROW LEVEL SECURITY`, facility-uebergreifendes Pruning). Wenn die App-Rolle dafuer aufgemacht wird, faellt RLS auch im Normalbetrieb.
3. **Erstinstallation** muss die App-Rollen ueberhaupt erst anlegen koennen — irgendeine Bootstrap-Identitaet mit Superuser-Rechten ist unvermeidbar.

Ein Single-Role-Modell loest hoechstens zwei der drei Probleme zugleich.

## Decision

Anlaufstelle laeuft in Produktion mit **drei strikt getrennten Postgres-Rollen**:

| Rolle | Attribute | Zweck | Wer/Wann |
|-------|-----------|-------|----------|
| `postgres` (Bootstrap-Superuser) | `SUPERUSER` | Rollen-Setup beim allerersten Start, Restore aus Dump | Container-Init via [`deploy/postgres-init/01-app-role.sh`](../../deploy/postgres-init/01-app-role.sh), manuelle Restore-Runs |
| `anlaufstelle` (App-Rolle, `POSTGRES_USER`) | `NOSUPERUSER NOBYPASSRLS` | Django-Runtime — alle normalen Web-Requests | `DATABASES['default']` |
| `anlaufstelle_admin` (Admin-Rolle, `POSTGRES_ADMIN_USER`) | `NOSUPERUSER BYPASSRLS` | Migrationen, Seed, Retention-Pruning, facility-uebergreifende Wartung | Management-Commands, Cron-Jobs |

Konkrete Folge-Festlegungen:

- **`.env` ist verbindlich:** `POSTGRES_ADMIN_USER`, `POSTGRES_ADMIN_PASSWORD` und `POSTGRES_BOOTSTRAP_PASSWORD` sind Pflichtvariablen in Produktion (siehe [`docs/admin-guide.md` § Datenbank](../admin-guide.md)). Default-Werte gibt es bewusst nicht — kein stiller Fallback auf die App-Rolle.
- **Runtime-Verifikation:** Das Management-Command [`manage.py check_db_roles`](../../src/core/management/commands/check_db_roles.py) prueft beim Start (und im Compliance-Dashboard, Refs #919) per `pg_roles`, dass die App-Rolle `rolsuper=false`/`rolbypassrls=false` und die Admin-Rolle `rolsuper=false`/`rolbypassrls=true` hat. Exit-Codes: `0` ok, `1` falsches Attributprofil, `2` Konfiguration unvollstaendig.
- **Migrationen laufen unter der Admin-Rolle**, nicht unter der App-Rolle. Damit braucht die App-Rolle keine DDL-Rechte und das RLS-Gate bleibt zur Laufzeit eng.
- **Ownership-Normalisierung nach dem Migrate:** Weil der Migrate-Job als Admin connectet, entstehen frisch migrierte Tabellen *admin-owned* — auf einem frischen Cluster bekaeme die App-Rolle sonst `permission denied` (Refs #1085). Der Migrate-One-Shot ruft daher nach `migrate` [`manage.py normalize_db_ownership`](../../src/core/management/commands/normalize_db_ownership.py) auf: `REASSIGN OWNED BY <admin> TO <db_owner>` uebertraegt alle Objekte (Tabellen, Sequenzen, Matviews) env-agnostisch und idempotent auf den Datenbank-Owner (App-Rolle, gesetzt per `ALTER DATABASE OWNER`). Generalisiert das per-Tabelle-Muster aus [`0093_cache_table_owner.py`](../../src/core/migrations/0093_cache_table_owner.py) (Refs #1030) — neue admin-erstellte Tabellen brauchen keine einzelnen Owner-Fix-Migrationen mehr.

## Consequences

- **+** RLS ist auch unter dem default `postgres`-Image belastbar. Die App-Rolle hat per Konstruktion keine Bypass-Rechte, egal wie das Image den `POSTGRES_USER` initialisieren wuerde — das Init-Script entreisst dem Superuser-Default explizit die Attribute. Auf `postgres:18-alpine` re-verifiziert: Init-Logik und Rollen-Attribute unveraendert, `check_db_roles` Exit 0 (Refs #1039).
- **+** Klare Verantwortung pro Rolle erleichtert Rollen-Audit. `pg_roles`-Snapshot zeigt eindeutig, wer was darf.
- **+** Migrationen und Retention koennen ihre Arbeit (facility-uebergreifende Updates, Trigger-Wartung) machen, ohne dass die Web-App dieselben Rechte erbt.
- **+** Lockout-Recovery und Rollen-Rotation laufen ueber den Bootstrap-Superuser — die normale App-Verbindung kennt das Passwort nie.
- **−** Drei `.env`-Variablen statt einer — Breaking-Change fuer Self-Hoster beim v0.12-Upgrade. Mitigation: prominent in CHANGELOG/Admin-Guide; `check_db_roles` als Frueh-Diagnose.
- **−** Mehr Komplexitaet im Deployment-Init (Postgres-Init-Script, Container-Healthcheck-Reihenfolge).
- **−** Wer mit einer einzigen DB-User-Annahme aus dem Django-Oekosystem kommt (`./manage.py migrate` als App-User), wird vom Setup ueberrascht. Doku-Pflicht.

## Alternatives considered

- **Single-Role mit `BYPASSRLS` fuer die App.** Verworfen: hebt das gesamte RLS-Gate auf. Das war der Ist-Zustand des Default-Images und exakt das Risiko, das sichtbar gemacht hat.
- **Single-Role ohne Bypass + Migrationen ueber App-Rolle.** Verworfen: braucht entweder DDL-Rechte auf der App-Rolle (= effektiv Superuser-naehe) oder eine Sondermechanik, die RLS pro Migration temporaer abschaltet. Beide Varianten sind fragiler als zwei getrennte Rollen.
- **Dynamischer Rollenwechsel per `SET ROLE` pro Request.** Verworfen: braucht eine Connection-Pool-Schicht ueber Django hinaus, plus zusaetzliche Audit-Komplexitaet. Liefert keinen Vorteil gegenueber „App-Rolle ohne Bypass" als Default — und ADR-018 nutzt fuer `super_admin` ohnehin Session-Variablen + OR-Branch statt Rollenwechsel.
- **`SET ROLE app_user` im Migrate-Job** (statt der Ownership-Normalisierung oben), damit Objekte direkt app-owned entstehen. Verworfen (Refs #1085): BYPASSRLS haengt an `current_user` — nach `SET ROLE` auf die NOBYPASSRLS-App-Rolle greifen die RLS-Policies wieder, sodass RunPython-Datenmigrationen auf facility-gescopte Tabellen (ohne gesetztes `app.current_facility_id`) still **null Zeilen** treffen. Empirisch auf `postgres:18` bestaetigt. Stattdessen bleibt der Migrate-Job auf der BYPASSRLS-Admin-Rolle und normalisiert die Ownership *nach* dem Migrate.
- **Vier Rollen (zusaetzlich Read-Only-Reporting).** Vertagt: konkreter Bedarf erst sichtbar, wenn externe Reporting-Tools angedockt werden. Additiv jederzeit nachruestbar, ohne dieses Modell zu brechen.

## References

- [`deploy/postgres-init/01-app-role.sh`](../../deploy/postgres-init/01-app-role.sh) — Init-Script, das die Rollen-Topologie aufzieht
- [`src/core/management/commands/check_db_roles.py`](../../src/core/management/commands/check_db_roles.py) — Runtime-Verifikation
- [`src/core/services/compliance/db_roles.py`](../../src/core/services/compliance/db_roles.py) — Dashboard-Integration
- [`docs/admin-guide.md` § Datenbank](../admin-guide.md)
- [ADR-005](005-facility-scoping-and-rls.md) — Facility-Scoping + RLS (Voraussetzung)
- [ADR-018](018-rollenmodell-superadmin.md) — App-Ebene Super-Admin (nicht DB-Ebene)
- Issue #902 — Drei-Rollen-Modell
- Issue #1085 — Fresh-Install-Ownership: Normalisierung nach Migrate-als-Admin

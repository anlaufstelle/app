# ADR-020: Drei-Rollen-Postgres-Modell (Bootstrap / App / Admin)

- **Status:** Accepted
- **Date:** 2026-05-28
- **Deciders:** Tobias Nix
- **Refs:** #902

## Context

Row Level Security ([ADR-005](005-facility-scoping-and-rls.md)) traegt nur, solange die Postgres-Rolle, mit der Django zur Laufzeit verbindet, **weder `SUPERUSER` noch `BYPASSRLS`** besitzt. Beide Attribute heben die `facility_isolation`-Policies wirkungslos aus — eine kompromittierte App- oder Service-Connection sieht dann ueber alle Facilities hinweg.

Drei realweltliche Stolperfallen bedrohen genau diese Voraussetzung:

1. **Default-`postgres:16`-Image** legt den per `POSTGRES_USER` definierten Login automatisch als **Superuser** an. Wer das `.env`-Muster „App-User = POSTGRES_USER" naiv uebernimmt, faehrt RLS-frei in Produktion — ohne Warnung. dokumentiert das als realen Selbst-Hosting-Risiko.
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

## Consequences

- **+** RLS ist auch unter dem default `postgres:16`-Image belastbar. Die App-Rolle hat per Konstruktion keine Bypass-Rechte, egal wie das Image den `POSTGRES_USER` initialisieren wuerde — das Init-Script entreisst dem Superuser-Default explizit die Attribute.
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
- **Vier Rollen (zusaetzlich Read-Only-Reporting).** Vertagt: konkreter Bedarf erst sichtbar, wenn externe Reporting-Tools angedockt werden. Additiv jederzeit nachruestbar, ohne dieses Modell zu brechen.

## References

- [`deploy/postgres-init/01-app-role.sh`](../../deploy/postgres-init/01-app-role.sh) — Init-Script, das die Rollen-Topologie aufzieht
- [`src/core/management/commands/check_db_roles.py`](../../src/core/management/commands/check_db_roles.py) — Runtime-Verifikation
- [`src/core/services/compliance/db_roles.py`](../../src/core/services/compliance/db_roles.py) — Dashboard-Integration
- [`docs/admin-guide.md` § Datenbank](../admin-guide.md)
- [ADR-005](005-facility-scoping-and-rls.md) — Facility-Scoping + RLS (Voraussetzung)
- [ADR-018](018-rollenmodell-superadmin.md) — App-Ebene Super-Admin (nicht DB-Ebene)
- Issue #902 — Drei-Rollen-Modell

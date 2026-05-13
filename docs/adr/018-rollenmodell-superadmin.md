# ADR-018: 5-Rollen-Modell mit Super-Admin

- **Status:** Accepted
- **Date:** 2026-05-10
- **Deciders:** Tobias Nix
- **Refs:**, schließt 

## Context

Das bisherige 4-Rollen-Modell (`admin`, `lead`, `staff`, `assistant`) hat die Hosting-Ebene und die Anwendungsbetreuungs-Ebene in der einen Rolle `admin` zusammengefasst. Drei Probleme entstanden daraus:

1. **DSGVO-Zweckbindung:** Die Person, die den Server betreibt (Hosting/Backup/Update), ist nicht zwingend dieselbe wie die Person, die in einer Einrichtung Benutzer pflegt und Audit-Logs liest. Mit nur einer `admin`-Rolle musste der Hosting-Operator entweder volle Fach-Sicht bekommen oder gar keinen Zugang — beides DSGVO-problematisch (Art. 5(1)(b) Zweckbindung; Art. 5(1)(c) Datenminimierung).
2. **NULL-Facility-Forensik (Issue ):** Pre-Auth-AuditLogs (`LOGIN_FAILED` ohne Facility-Bezug, frühe Maintenance-Events) waren nur per psql am Container auswertbar. Ein UI-Pfad fehlte. Die normale `admin`-Rolle hatte keine RLS-Bypass-Berechtigung; ein Forensik-UI hätte ihr eine zu breite Vollmacht gegeben.
3. **Bootstrap-Sicherheit:** `make seed` legte einen `admin`-Account mit Default-Passwort `anlaufstelle2026` an. Für Demos akzeptabel, in einer Produktion eine Schwachstelle. Ein dedizierter Bootstrap-Befehl ohne Default-Passwort fehlte.

Eine zusätzliche, oberhalb von `admin` angesiedelte Rolle für Hosting/Bootstrap löst alle drei Probleme — wenn sie sauber von der Anwendungsebene getrennt bleibt.

## Decision

Das Rollenmodell wird auf **5 Rollen** erweitert. Eine Rolle (`super_admin`) wirkt facility-übergreifend, vier Rollen sind strikt facility-gebunden:

| Rolle | Scope | Aufgabe |
|-------|-------|---------|
| `super_admin` | facility-übergreifend (`/system/`) | Hosting, Bootstrap, Pre-Auth-AuditLogs |
| `facility_admin` (vormals `admin`) | eine Einrichtung | Volle Anwendungsbetreuung der eigenen Einrichtung |
| `lead` | eine Einrichtung | Leitung, Auswertungen, Löschanträge genehmigen |
| `staff` | eine Einrichtung | Fachkraft, Kerndokumentation |
| `assistant` | eine Einrichtung | Assistenz, eingeschränkte Erfassung |

UI-Label: `facility_admin` heißt im UI „Anwendungsbetreuung", `super_admin` heißt „Systemadministration". Der DB-Wert `admin` wird per Migration nach `facility_admin` umbenannt.

### Architektur-Entscheidungen

**1. Organization als Branding-Hülse (Variante b1).** Die `Organization` bleibt als Modell bestehen, dient aber **nur** dem Träger-Branding (Logo, Trägername in Berichten). Es gibt **keinen** Org-Admin und **keinen** Cross-Facility-Effekt durch sie. Die einzige facility-übergreifende Rolle ist `super_admin`. Damit bleibt die Mandanten-Trennung scharf, ohne ein zusätzliches Hierarchiekonzept aufzubauen.

**2. RLS-Bypass via Postgres-Session-Variable.** Statt einen dritten DB-User mit `BYPASSRLS` einzuführen, wird eine zusätzliche Session-Variable `app.is_super_admin='true'` gesetzt und in jede `facility_isolation`-Policy als OR-Branch eingewoben (Migration [0085](././src/core/migrations/0085_rls_super_admin_branch.py)):

```sql
USING (
    facility_id::text = current_setting('app.current_facility_id', true)
    OR current_setting('app.is_super_admin', true) = 'true'
)
```

Die [`FacilityScopeMiddleware`](././src/core/middleware/facility_scope.py) setzt vor jedem Request entweder `app.current_facility_id` (für facility-gebundene User) oder `app.is_super_admin='true'` (für `super_admin`). **Always-Reset-Pattern:** vor jedem Request wird `app.is_super_admin` explizit auf `'false'` zurückgesetzt — damit kein Pool-Connection-Leak entsteht, falls die nächste Session auf derselben Connection ein facility-User-Request ist. Details: [ADR-005 Update 2026-05-10](005-facility-scoping-and-rls.md).

**3. Production-Bootstrap interaktiv (kein Default-Passwort).** Der erste `super_admin` wird über den neuen Befehl `manage.py create_super_admin` interaktiv angelegt — der Befehl fragt nach Username, E-Mail, Passwort (zweimal), Anzeigename. Es gibt **keinen** Default-Username, **kein** Default-Passwort. Wer den Schritt überspringt, erhält keinen anmeldungsfähigen Account. Lockout-Recovery für den letzten verbleibenden `super_admin`: `manage.py unlock <username>` (CLI-only, kein UI-Pfad).

**4. DSGVO-Rechenschaftspflicht: SYSTEM_VIEW-Action + UI-Banner.** Jeder Aufruf einer `/system/`-View schreibt einen `SYSTEM_VIEW`-AuditLog-Eintrag (Detail: `view_name`, Filter, Anzahl angezeigter Einträge). Das System-UI zeigt einen permanenten Banner: „Sie greifen auf Daten facility-übergreifend zu — dieser Zugriff wird im Audit-Log protokolliert." Damit ist Art. 5(2) DSGVO (Rechenschaftspflicht) auch für die hochprivilegierte Rolle erfüllt. Der DB-Trigger gegen UPDATE/DELETE (Migration 0024) greift weiterhin — ein `super_admin` kann seinen eigenen Zugriff nicht verstecken.

## Consequences

### Pro

- **+** DSGVO-Zweckbindung sauber: Hosting-Operator sieht `/system/`, aber nicht das Fach-UI einer Einrichtung. Anwendungsbetreuung sieht ihre Einrichtung, aber keine andere.
- **+** Issue gelöst: NULL-Facility-Forensik im UI verfügbar (`/system/`-AuditLog), kein psql-Zugriff mehr nötig.
- **+** Production-Bootstrap ohne Default-Passwort schließt eine Schwachstelle, die `make seed` in der Produktion war.
- **+** Lockout-Recovery für den letzten Super-Admin per CLI definiert — kein „Bricked Installation"-Risiko mehr.
- **+** Org als Branding-Hülse hält die Mandanten-Architektur einfach. Kein Org-Admin, kein Hierarchie-Aufwand.
- **+** SYSTEM_VIEW-Audit + Banner erfüllen Rechenschaftspflicht auch für `super_admin`.

### Contra

- **−** Migration für bestehende Installationen nötig: DB-Wert `admin` → `facility_admin`. UI-Labels in Templates und `django.po` müssen aktualisiert werden.
- **−** Eine zusätzliche Rolle bedeutet zusätzlichen Mixin-Aufwand (`SuperAdminRequiredMixin`, `FacilityAdminRequiredMixin`) und einen neuen URL-Bereich `/system/`.
- **−** Die RLS-Policies werden komplexer durch den OR-Branch (Migration 0085). Test-Coverage für `app.is_super_admin`-Reset-Verhalten ist Pflicht.
- **−** `super_admin` ist eine Hochrisiko-Rolle: kompromittierte Credentials erlauben Einsicht in alle Einrichtungen. Mitigation: MFA-Pflicht ([ADR-015](015-mfa-totp.md)), Lockout (10 Versuche), Audit-Spur per `SYSTEM_VIEW`.
- **−** Die Abgrenzung „User-Modell ist nicht facility-RLS-geschützt" ([security-notes.md § core_user](./security-notes.md#core_user-bleibt-rls-frei-finding-s-2)) bleibt — `super_admin` ohne `facility`-FK passt in dieses bewusst gewählte Schema.

## Alternatives considered

- **Eigener DB-User für `super_admin` (BYPASSRLS).** Verworfen: Django-Connection-Pools pro gunicorn-Worker haben einen festen User. Ein dynamischer User-Wechsel pro Request bräuchte einen kompletten zweiten Connection-Pool. Mehr DB-User = aufwändigere `pg_user`-Rotation, mehr Audit-Komplexität. Die Anwendungsrollen-Schicht (Session-Variable + OR-Branch) liefert den gleichen Schutz mit weniger Komplexität.
- **Token-basierter Bootstrap (statt interaktivem Befehl).** Verworfen: Für MVP zu komplex. Token-Ausstellung, -Speicherung und -Invalidierung verlangen einen sicheren Storage. Ein interaktiver Befehl am Server-Container ist für die Zielgruppe (selbst-hostende Träger mit Linux-Kompetenz) ausreichend. Re-Evaluation, wenn Demo-Instanz oder One-Click-Deploy dazukommt.
- **Cross-Tenant-Sichtbarkeit für `facility_admin` (statt eigenständiger `super_admin`-Rolle).** Verworfen: DSGVO-Risiko. Wenn `facility_admin` in fremde Einrichtungen schauen kann, ist die Mandanten-Trennung praktisch aufgehoben. Die Trennung System-Hosting (`super_admin`) ↔ Anwendungsbetreuung (`facility_admin`) ist der explizite Schutz dagegen.
- **`super_admin` ohne `/system/`-Bereich (Hidden Role).** Verworfen: Issue braucht eine UI-Surface. Eine versteckte Rolle ohne UI-Pfad würde die Recovery-/Forensik-Anforderung nicht erfüllen.
- **Org-Admin-Rolle (Variante b2 / b3).** Verworfen: Die Organization ist heute eine reine Verwaltungs-Hülse für Branding. Eine Org-Admin-Rolle würde ein neues Berechtigungsmodell aufmachen (Org-Lead, Org-Statistiken, Org-Reports), das in v1.0 nicht gebraucht wird. Falls künftig ein Träger als konkreter Nutzer mit echtem Org-Workflow dazukommt, kann die Rolle additiv ergänzt werden — die heutige Architektur blockiert das nicht.

## References

- Issue — Plan-Issue: 5-Rollen-Modell + super_admin
- Issue — NULL-Facility-AuditLogs im UI sichtbar machen
- [ADR-005](005-facility-scoping-and-rls.md) — Facility-Scoping + RLS, mit Update 2026-05-10 zur OR-Branch-Schicht
- [ADR-007](007-auditlog-append-only.md) — AuditLog Append-Only, mit Update 2026-05-10 zu `SYSTEM_VIEW`
- [ADR-013](013-dsgvo-art16-no-selfservice.md) — DSGVO Art. 16 ohne App-Self-Service (Berichtigungen über `facility_admin`)
- [ADR-015](015-mfa-totp.md) — MFA-Pflicht-Pfad für `super_admin`
- [docs/admin-guide.md § 2.1 Erstinstallation](./admin-guide.md) — Bootstrap-Anleitung
- [docs/dev-deployment.md § Production-Bootstrap](./dev-deployment.md) — Server-Setup mit `create_super_admin`
- [docs/faq.md § C.13](./faq.md) — User-facing Rollendokumentation

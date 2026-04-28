# Security Notes

Dokumentiert bewusste Security-Entscheidungen, die beim Audit der Tiefenanalyse (siehe [docs/audits/2026-04-21-tiefenanalyse-v0.10.md](audits/2026-04-21-tiefenanalyse-v0.10.md)) verifiziert und für die Roadmap festgehalten wurden. Dieses Dokument ist das „Warum wir das so lassen"-Gegenstück zur RLS- und Facility-Scoping-Doku in [CONTRIBUTING.md](../CONTRIBUTING.md#facility-scoping--row-level-security) und zum Ops-Runbook [docs/ops-runbook.md](ops-runbook.md).

---

## `core_user` bleibt RLS-frei (Finding S-2)

**Status:** Design-Entscheidung, kein Fix notwendig.

### Beobachtung

Das `User`-Model hat einen `facility`-FK (nullable, `on_delete=SET_NULL`) in [`src/core/models/user.py`](../src/core/models/user.py), ist aber **nicht** in `DIRECT_TABLES` oder `JOIN_TABLES` der RLS-Setup-Migration [`0047_postgres_rls_setup.py`](../src/core/migrations/0047_postgres_rls_setup.py). Jede andere facility-gescopte Tabelle hat eine `facility_isolation`-Policy; `core_user` fehlt absichtlich.

### Begründung

Zwei Use-Cases sind auf Cross-Facility-Zugriff angewiesen:

1. **Login-Bootstrap.** Beim Anmelden ist die Session noch nicht an eine Facility gebunden — Django-`auth_login` sucht den User über `username` (oder E-Mail beim Password-Reset) und setzt erst danach die Facility in `request.session`. Eine facility-isolierte `core_user`-Policy würde den Login von **jedem** User scheitern lassen, solange der Session-Variablen-Setzer (die [`FacilityScopeMiddleware`](../src/core/middleware/facility_scope.py)) nicht schon läuft — klassischer Chicken-and-Egg.
2. **Cross-Facility-Administration.** Administratoren können User mehrerer Facilities verwalten (Passwort-Reset senden, Rollen anpassen, Token-Invite neu generieren). Das ist explizit gewünscht (siehe [`core.admin.UserAdmin`](../src/core/admin.py)), und eine facility-isolierte Policy würde diesen Admin-Workflow unterbinden.

### Andere Verteidigungslinien

Auch ohne RLS-Isolation auf `core_user` gilt:

- **Facility-Scoping im ORM-Layer:** Alle user-facing Views gehen über [`FacilityScopeMiddleware`](../src/core/middleware/facility_scope.py), die `request.current_facility` setzt. Queries, die User ausschließlich einer Facility liefern sollen, filtern explizit (z.B. in [`forms/workitems.py`](../src/core/forms/workitems.py): `User.objects.filter(facility=facility, ...)`).
- **Rollen-Gates:** [`AdminRequiredMixin`](../src/core/views/mixins.py) hält Cross-Facility-User-Management Admin-only.
- **AuditLog:** User-Role-Changes und Deaktivierungen werden via `post_save`-Signale geloggt (siehe [`signals/audit.py`](../src/core/signals/audit.py)).

### Verifikation

Der Retro-Audit [#600](https://github.com/tobiasnix/anlaufstelle/issues/600) hat die Liste der RLS-geschützten Tabellen vollständig durchgegangen und `core_user` bewusst **nicht** ergänzt — zusammen mit `core_organization`, `core_facility`, `core_user_groups`, `core_user_user_permissions` bleibt die Bootstrap/Auth-Schicht außerhalb der per-Facility-Policy.

---

## `SESSION_COOKIE_SAMESITE = "Lax"` bleibt (Finding S-5)

**Status:** Design-Entscheidung mit Rationale.

`CSRF_COOKIE_SAMESITE` ist in [`prod.py`](../src/anlaufstelle/settings/prod.py) auf `"Strict"` gesetzt, `SESSION_COOKIE_SAMESITE` bleibt bei `"Lax"`. Grund: **Password-Reset-E-Mail-Links** (Cross-Site → same-site nach Klick) müssen den Session-Cookie mitbringen, sonst landet der User auf der Login-Seite, obwohl der Token noch gültig ist. Django-Default `"Lax"` lässt genau diesen GET-Cross-Site-Flow durch, während POST-Formulare und Fetch-Requests weiter geblockt werden. `"Strict"` würde den E-Mail-Link-Flow aufbrechen.

Wenn wir den Token-Link-Flow (Invite, Passwort-Reset, 2FA-Backup-Download) jemals auf strikt trennen (eigener Host / eigene Subdomain), kann `"Strict"` greifen.

---

## AuditLog `facility_id` ist nullable (Design)

`AuditLog.facility` ist `null=True` (siehe [`models/audit.py`](../src/core/models/audit.py)), weil System-weite Events (z.B. fehlgeschlagene Logins vor dem Facility-Context) keine Facility haben. Diese Zeilen matchen die `facility_isolation`-Policy **nicht** — sie sind nur für RLS-bypassende Rollen (Superuser, direkte DB-Admins) sichtbar. Application-Code ruft NULL-Audit-Logs ohnehin nicht über facility-scoped Views ab.

---

## Weitere Einstiegspunkte

- [CONTRIBUTING.md § Facility-Scoping & Row Level Security](../CONTRIBUTING.md#facility-scoping--row-level-security)
- [docs/ops-runbook.md § 9](ops-runbook.md) — RLS-Runbook, Rollen, Kill-Switch
- [docs/audits/2026-04-21-tiefenanalyse-v0.10.md](audits/2026-04-21-tiefenanalyse-v0.10.md) — Security-/DSGVO-/Perf-Audit mit zeilengenauer Verifikation

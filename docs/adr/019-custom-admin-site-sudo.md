# ADR-019: Custom AdminSite mit Rollen-Gate und Sudo-Mode

- **Status:** Accepted
- **Date:** 2026-05-28
- **Deciders:** Tobias Nix
- **Refs:** #683, #785, #958

## Context

Anlaufstelle braucht eine Datenverwaltungs-Oberflaeche fuer Anwendungsbetreuung (`facility_admin`) und Systemadministration (`super_admin`) — Stammdaten, Dokumentationstypen, Feld-Templates, AuditLog-Ansicht. Das Django-Default-Admin liefert dafuer den noetigen CRUD-Rahmen, ist im Auslieferungszustand aber zu durchlaessig:

- **`is_staff` als Gate ist zu grob.** Default-Admin laesst jeden mit `is_staff=True` rein. Im 5-Rollen-Modell ([ADR-018](018-rollenmodell-superadmin.md)) muessen `lead`, `staff` und `assistant` aber draussen bleiben — auch wenn ein Migrations-Altbestand `is_staff=True` setzt.
- **Re-Auth-Pflicht fehlt.** Sensible Admin-Aktionen (Rollen aendern, Retention konfigurieren, Backups ausloesen) duerfen nicht allein an einer bestehenden Login-Session haengen. Issue #683 verlangt einen Step-Up („Sudo-Mode") wie ihn GitHub fuer kritische Settings nutzt.
- **Facility-Scoping muss in der Liste greifen.** Ein `facility_admin` darf in der ChangeList nur die eigene Einrichtung sehen — derselbe Vertrag wie der `FacilityScopedManager` in den Models ([ADR-005](005-facility-scoping-and-rls.md)), aber das Default-Admin umgeht Manager-Defaults nicht durchgaengig.
- **Doppelte Regel-Definition** zwischen [`core/admin/mixins.py`](../../src/core/admin/mixins.py) und ModelAdmin-Klassen war Refs #958 eine Quelle subtiler Drift — Rolle und Scope wurden an zwei Stellen leicht unterschiedlich geprueft.

## Decision

Anlaufstelle verwendet eine **Custom-AdminSite-Subklasse** [`AnlaufstelleAdminSite`](../../src/core/admin_site.py) als alleinige Datenverwaltungs-UI unter `/admin-mgmt/`. Die Default-`django.contrib.admin.site` wird aus dem URLconf entfernt — es gibt keinen zweiten Admin-Pfad.

- **Rollen-Gate:** `has_permission(request)` laesst nur User mit `is_super_admin` oder `is_facility_admin` durch. `lead`/`staff`/`assistant` werden geblockt, auch bei `is_staff=True`. Die Pruefung lebt in der Site-Methode `_has_admin_role` als einzige Truth-Source — ModelAdmin-Mixins delegieren an `admin_site.has_role_permission(request)`.
- **Sudo-Mode-Pflicht:** Wenn `SUDO_MODE_ENABLED=True` (Default), muss zusaetzlich `is_in_sudo(request)` true sein. Die Site-eigene `login`-View redirected einen autorisierten User ohne Sudo aktiv auf `/sudo/?next=…` statt ihn stillschweigend reinzulassen.
- **Facility-Scoping ueber Site-Methode:** `scope_to_facility(queryset, request)` ist die einzige Stelle, an der die Regel „`super_admin` sieht alles, `facility_admin` sieht nur die eigene Facility" implementiert wird. ModelAdmin-`get_queryset` ruft diese Methode auf.
- **Unfold-Theme als Basis:** Die Site erbt von `UnfoldAdminSite` (gevendor'tes `django-unfold`), damit Theme, Search-Endpoint und `each_context`-Variablen ohne Brueche funktionieren. Namespace bleibt `"admin"`, damit `{% url 'admin:...' %}` in Unfold-Templates aufloest.

## Consequences

- **+** Single Source of Truth fuer Admin-Berechtigungen. Rolle, Facility-Scope und Sudo-Status sind an genau einer Stelle (`AnlaufstelleAdminSite`) definiert; ModelAdmin-Mixins delegieren. Refs #958 hat die alte Duplizierung aufgeloest.
- **+** Sudo-Mode schliesst die Luecke „lange Sessions schuetzen kritische Aktionen". Eine geklaute Session reicht nicht mehr fuer Admin-Schreibzugriffe, ohne dass ein erneuter Auth-Schritt verlangt wird.
- **+** Rollen-Gate macht `is_staff` wieder zu einem reinen Django-Internum (z.B. `manage.py shell`-Zugriff) und entkoppelt es von der Anwendungs-Berechtigung.
- **+** Die Custom-Site rendert das CSP-Trade-off ([`docs/security-notes.md` § /admin-mgmt/](../security-notes.md#csp-unsafe-eval-auf-admin-mgmt-issue-695)) ueberhaupt erst akzeptabel: `'unsafe-eval'` wird nur auf einem Pfad gelockert, der durch Rollen-Gate + Sudo geschuetzt ist.
- **−** Wartungs-Tax bei Django-Upgrades — die Subklasse muss bei `AdminSite`-API-Aenderungen nachgezogen werden. In der Praxis ist das API stabil; Risiko gering.
- **−** Wer Django-Admin gewohnt ist, stolpert ueber den Sudo-Redirect beim ersten Zugriff. Mitigation: Banner und Doku in [`docs/admin-guide.md`](../admin-guide.md).
- **−** Die Sudo-Pflicht muss in E2E-Tests gegen `/admin-mgmt/` aktiv beruecksichtigt werden (`enter_sudo_mode` vor `goto`). Refs der internen Notiz `feedback_e2e-sudo-mode-pre-785`.

## Alternatives considered

- **Default-`AdminSite` + `is_staff`-Migration auf nur Admins.** Verworfen: Verlagert die Pruefung in eine Daten-Migration, statt in den Code. Drift-Risiko bei neuen Rollen oder Imports bleibt. Sudo-Mode liesse sich nur ueber Middleware nachruesten — fragiler als die Site-Methode.
- **Custom-Views ausserhalb des Admin-Frameworks fuer alle Verwaltungsmasken.** Verworfen fuer v1.0: Riesiger Initialaufwand (Listen-, Filter-, Search-, Form-Generation neu bauen) ohne klaren Mehrwert gegenueber dem geleehrten Admin-CRUD. Bleibt eine Option fuer eine geplante Custom-Admin-UI — bewusst gesperrt bis dahin.
- **Sudo-Mode nur als Decorator pro Aktion, ohne Site-Gate.** Verworfen: Vergessen-eines-Decorators auf einer neuen Action waere eine stille Privileg-Eskalation. Site-weites Gate macht den Default sicher; einzelne Aktionen koennen Sudo additiv verlangen.

## References

- [`src/core/admin_site.py`](../../src/core/admin_site.py)
- [`src/core/services/security/`](../../src/core/services/security/) (`is_in_sudo`)
- [`docs/security-notes.md` § CSP `'unsafe-eval'`](../security-notes.md#csp-unsafe-eval-auf-admin-mgmt-issue-695)
- [ADR-005](005-facility-scoping-and-rls.md) — Facility-Scoping + RLS
- [ADR-018](018-rollenmodell-superadmin.md) — 5-Rollen-Modell
- Issue #683 — Sudo-Mode fuer Admin
- Issue #785 — Rollen-Gate fuer Admin
- Issue #958 — Konsolidierung Admin-Mixins

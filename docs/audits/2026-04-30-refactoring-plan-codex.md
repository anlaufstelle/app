# Refactoring-Plan Anlaufstelle

Stand: 2026-04-30 
Autor: Codex 
Scope: lokale Analyse von `/work/anlaufstelle`, kein Live-System, keine Rechtsberatung.

## Inventur

Aktuelle Struktur:

- Projekt: Django 6.0, Python >=3.13, PostgreSQL-only, AGPL-3.0-or-later (`pyproject.toml:1-6`, `requirements.in:1-16`).
- Apps: eine Projekt-App `core` plus Projektpaket `anlaufstelle`; die Eine-App-Strategie ist bewusst in ADR-002 dokumentiert (`docs/adr/002-cbvs-and-service-layer.md:13-18`).
- Models: 20 Model-Module unter `src/core/models/`, u. a. Facility/Organization/User, Client, Event/EventHistory, Case/Episode, WorkItem/DeletionRequest, Retention, Audit, Attachment, DocumentType/FieldTemplate.
- Views: 28 View-Module unter `src/core/views/`, fast komplett CBV-basiert. Rollen-Mixins liegen zentral in `src/core/views/mixins.py:10-35`.
- Services: 34 Service-Module unter `src/core/services/`; `core.services.*` ist die mypy-Strict-Zone (`pyproject.toml:45-95`, `docs/adr/012-incremental-mypy.md:13-22`).
- Forms: 6 Form-Module unter `src/core/forms/`; dynamische Event-Felder werden in `DynamicEventDataForm` gebaut (`src/core/forms/events.py:109-230`).
- Templates: 90 HTML/TXT-Templates unter `src/templates/`, viele Partials bereits vorhanden (`src/templates/core/*/partials/*`).
- Settings: Split in `base.py`, `dev.py`, `prod.py`, `test.py`, `e2e.py` (`src/anlaufstelle/settings/`). Produktion ist fail-closed bei fehlendem Secret, Hosts und Encryption-Key (`src/anlaufstelle/settings/prod.py:31-102`).
- Dependencies: `requirements.in`/`requirements.txt` plus `pip-tools`; Frontend nur Tailwind über `package.json` (`requirements-dev.in:1-13`, `package.json:1-13`).
- CI: getrennte Jobs für Unit/Integration, Django-Checks, pip-audit/SBOM, Lockfile-Check, Ruff, mypy und Playwright-E2E (`.github/workflows/test.yml:10-131`, `.github/workflows/lint.yml:10-36`, `.github/workflows/e2e.yml:10-57`).
- Tests: `pytest --collect-only -q` sammelt lokal 2080 Tests, davon 1728 non-e2e und 352 e2e. Es wurde nur collect-only ausgeführt, kein vollständiger Testlauf.

## A. Refactoring-Strategie

Wirklich dran sind kleine Konsolidierungen an den Stellen, die schon lokale Helfer haben, aber noch nicht konsequent nutzen: `HTMXPartialMixin`, `FacilityScopedManager`, zentrale Audit-Helper, WorkItem-Status-/Priority-Validierung und die doppelte Statistik-Periodenlogik. Danach folgen zwei mittlere Strukturthemen: Zeitstrom-Kontext in einen Service ziehen und Retention-Strategien deduplizieren. Das lohnt, weil dort dieselben Regeln in mehreren Pfaden gepflegt werden und die bestehende Testsuite genau diese Verhalten bereits schützt.

Nicht dran ist ein großer App-Split, eine Repository-/Hexagonal-Schicht oder ein Rewrite des Service-Layers. ADR-002 passt zur Größe und zum Solo-Maintainer-Kontext. Auch das dynamische `Event.data_json` ist fachlich sinnvoll, solange die FieldTemplate-Gates und Encryption-Guards bestehen.

Reihenfolge:

1. Quick Wins bis: mechanisch, niedriges Risiko, gute Tests vorhanden.
2. bis: lokale Extracts in Statistik, WorkItems, Event-Attachments.
3. und: Retention und Zeitstrom als mittlere Refactorings mit mehr Testfokus.
4. Redesigns bis nur nach Produkt-/Betriebsentscheidung, nicht als laufender-Nebenkrieg.

Boy-Scout-Regeln ab sofort:

- Neue View-Queries immer `for_facility` oder einen zentralen Visibility-Helper nutzen.
- Neue POST-Handler brauchen Rollen-Mixin, Facility-Scoping, Rate-Limit, Audit-Verhalten und Test.
- HTMX-Views mit genau einem Full-Template und einem Partial nutzen `HTMXPartialMixin`.
- Neue Service-Funktionen typisieren; bei berührten Services mypy-Strict nicht abschwächen.
- Keine neuen Freitextfelder ohne Datenschutzentscheidung: unverschlüsselt, verschlüsselt oder bewusst verboten.

## B. Quick Wins

###

ID: 
Titel: Ruff-Version in CI an Lockfile koppeln 
Kategorie: Cleanup 
Dimension: 7 Dependency-Hygiene 
Fundstelle(n): `.github/workflows/lint.yml:14-21`, `requirements-dev.in:1-3`, `Makefile:71-75`

Vorher (Skizze):

```yaml
- run: pip install ruff==0.15.11
- run: ruff check src/
- run: ruff format --check src/
```

Nachher (Skizze):

```yaml
- run: pip install -r requirements-dev.txt
- run: ruff check src/
- run: ruff format --check src/
```

Begründung: Lokal kommt Ruff aus `requirements-dev.txt`, CI pinnt eine ältere Version. Das ist kein Produktverhalten, aber unnötiger Tooling-Drift. 
Aufwand: S (<1h) 
Risiko: niedrig, nur Lint-Job. 
Voraussetzungen: keine 
Test-Strategie: `.github/workflows/lint.yml` bzw. lokal `make lint`. 
Migrations-Bedarf: nein 
Reversibilität: trivial

###

ID: 
Titel: Audit-Pagination in Konstante benennen 
Kategorie: Refactoring 
Dimension: 1 Code-Level 
Fundstelle(n): `src/core/views/audit.py:48-50`, `src/core/constants.py:12-17`

Vorher (Skizze):

```python
paginator = Paginator(queryset, 50)
page = paginator.get_page(safe_page_param(request))
```

Nachher (Skizze):

```python
AUDIT_PAGE_SIZE = 50

paginator = Paginator(queryset, AUDIT_PAGE_SIZE)
```

Begründung: `50` ist ein bewusst anderer Wert als `DEFAULT_PAGE_SIZE = 25`, aber im Code nicht benannt. Eine Konstante macht die Abweichung sichtbar, ohne Verhalten zu ändern. 
Aufwand: S (<1h) 
Risiko: niedrig, reine Benennung. 
Voraussetzungen: keine 
Test-Strategie: `src/tests/test_audit_view.py::test_audit_log_pagination`, `pytest -m "not e2e" src/tests/test_audit_view.py`. 
Migrations-Bedarf: nein 
Reversibilität: trivial

###

ID: 
Titel: Inbox-Listenlimit aus `get` herausziehen 
Kategorie: Refactoring 
Dimension: 1 Code-Level 
Fundstelle(n): `src/core/views/workitems.py:114-122`, `src/templates/core/workitems/partials/inbox_content.html:12-47`

Vorher (Skizze):

```python
def get(self, request):
    ...
    INBOX_LIST_LIMIT = 50
    open_items = list(open_qs[: INBOX_LIST_LIMIT + 1])
```

Nachher (Skizze):

```python
INBOX_LIST_LIMIT = 50

class WorkItemInboxView(...):
    def get(self, request):
        open_items = list(open_qs[: INBOX_LIST_LIMIT + 1])
```

Begründung: Das Limit ist fachliches UI-Verhalten und wird im Template angezeigt. Als Modulkonstante ist es test- und reviewbar, statt im Methodenkörper versteckt zu sein. 
Aufwand: S (<1h) 
Risiko: niedrig, keine Query- oder Template-Änderung. 
Voraussetzungen: keine 
Test-Strategie: `src/tests/test_workitems.py`, `src/tests/test_workitem_filters.py`, E2E `src/tests/e2e/test_workitem_ui.py`. 
Migrations-Bedarf: nein 
Reversibilität: trivial

###

ID: 
Titel: Einfache HTMX-Listenviews auf `HTMXPartialMixin` umstellen 
Kategorie: Refactoring 
Dimension: 3 HTMX-spezifisch 
Fundstelle(n): `src/core/views/mixins.py:61-83`, `src/core/views/clients.py:73-75`, `src/core/views/cases.py:98-100`, `src/core/views/audit.py:79-81`, `src/core/views/search.py:35-37`

Vorher (Skizze):

```python
if request.headers.get("HX-Request"):
    return render(request, "core/clients/partials/table.html", context)
return render(request, "core/clients/list.html", context)
```

Nachher (Skizze):

```python
class ClientListView(AssistantOrAboveRequiredMixin, HTMXPartialMixin, View):
    template_name = "core/clients/list.html"
    partial_template_name = "core/clients/partials/table.html"

    def get(self, request):
        ...
        return self.render_htmx_or_full(context)
```

Begründung: Der Helper existiert bereits und ist getestet, aber genau diese einfachen Branches nutzen ihn noch nicht. Das reduziert verstreute `HX-Request`-Checks ohne URL-Redesign. 
Aufwand: S (<1h) 
Risiko: niedrig, solange nur ein Full-Template und ein Partial betroffen sind. 
Voraussetzungen: keine 
Test-Strategie: `src/tests/test_view_mixins.py:47-136`, `src/tests/test_clients.py::TestClientList::test_client_list_htmx_returns_partial`, `src/tests/test_cases.py::TestCaseListView::test_case_list_htmx_partial`, `src/tests/test_audit_view.py::test_audit_log_htmx_returns_partial`. 
Migrations-Bedarf: nein 
Reversibilität: trivial

###

ID: 
Titel: Client-Autocomplete über Facility-Manager scopen 
Kategorie: Refactoring 
Dimension: 2 Django-Patterns 
Fundstelle(n): `src/core/views/clients.py:198-228`, `src/core/models/managers.py:6-21`

Vorher (Skizze):

```python
qs = Client.objects.filter(
    facility=request.current_facility,
    is_active=True,
)
```

Nachher (Skizze):

```python
qs = Client.objects.for_facility(request.current_facility).filter(
    is_active=True,
)
```

Begründung: Die Codebase hat einen `FacilityScopedManager`. Diese View nutzt noch die manuelle Variante. Der Wechsel macht Scoping konsistent und bleibt gleiches SQL. 
Aufwand: S (<1h) 
Risiko: niedrig, Query ist äquivalent. 
Voraussetzungen: keine 
Test-Strategie: `src/tests/test_clients.py`, `src/tests/e2e/test_client_autocomplete_recency.py`, `src/tests/e2e/test_zeitstrom_events.py::TestEventErstellung::test_client_autocomplete`. 
Migrations-Bedarf: nein 
Reversibilität: trivial

###

ID: 
Titel: `CaseForm.clean_client` mit scoped Query laden 
Kategorie: Refactoring 
Dimension: 2 Django-Patterns 
Fundstelle(n): `src/core/forms/cases.py:44-53`, `src/core/services/clients.py:124-142`

Vorher (Skizze):

```python
client_obj = Client.objects.get(pk=client_id)
if self.facility and client_obj.facility_id != self.facility.pk:
    raise ValidationError(...)
return client_obj
```

Nachher (Skizze):

```python
try:
    return Client.objects.for_facility(self.facility).get(pk=client_id)
except Client.DoesNotExist:
    raise ValidationError(_("Klientel existiert nicht."))
```

Begründung: Der Vorher-Code lädt erst global und prüft dann die Facility. Das ist funktional geschützt, aber unnötig breit. Der Nachher-Code macht das Datenbank-Scoping zur ersten Bedingung. 
Aufwand: S (<1h) 
Risiko: niedrig, gleiche Fehlermeldung beibehalten. 
Voraussetzungen: keine 
Test-Strategie: `src/tests/test_cases.py::TestCaseCreateView::test_case_create_with_client_preselection`, `src/tests/test_cases.py::TestCaseService::test_create_case_validates_client_facility`. 
Migrations-Bedarf: nein 
Reversibilität: trivial

###

ID: 
Titel: File-Feld-Magic-String durch `TextChoices` ersetzen 
Kategorie: Refactoring 
Dimension: 1 Code-Level 
Fundstelle(n): `src/core/services/event.py:314-318`, `src/core/services/event.py:424-426`, `src/core/models/document_type.py:133-143`

Vorher (Skizze):

```python
if ft.field_type != "file":
    continue
```

Nachher (Skizze):

```python
if ft.field_type != FieldTemplate.FieldType.FILE:
    continue
```

Begründung: `FieldTemplate.FieldType.FILE` existiert bereits. Der String `"file"` ist korrekt, aber driftanfälliger bei Rename oder Search. 
Aufwand: S (<1h) 
Risiko: niedrig, gleicher Datenbankwert. 
Voraussetzungen: keine 
Test-Strategie: `src/tests/test_attachment_versioning_stage_b.py`, `src/tests/test_file_vault.py`, `src/tests/test_events.py`. 
Migrations-Bedarf: nein 
Reversibilität: trivial

###

ID: 
Titel: WorkItem-Choice-Validierung vereinheitlichen 
Kategorie: Refactoring 
Dimension: 1 Code-Level 
Fundstelle(n): `src/core/views/workitem_actions.py:35-38`, `src/core/views/workitem_bulk.py:93-106`, `src/core/services/workitems.py:265-298`, `src/core/models/workitem.py:19-35`

Vorher (Skizze):

```python
valid_statuses = [s.value for s in WorkItem.Status]
if new_status not in valid_statuses:
    ...

if status not in {s.value for s in WorkItem.Status}:
    ...
```

Nachher (Skizze):

```python
if new_status not in WorkItem.Status.values:
    ...

if priority not in WorkItem.Priority.values:
    ...
```

Begründung: Django `TextChoices` liefert `.values`. Die lokale Rekonstruktion kommt dreimal vor und ist reines Rauschen. 
Aufwand: S (<1h) 
Risiko: niedrig, gleiche Werte. 
Voraussetzungen: keine 
Test-Strategie: `src/tests/test_workitem_bulk.py`, `src/tests/test_workitem_status_race.py`, `src/tests/test_workitems.py`. 
Migrations-Bedarf: nein 
Reversibilität: trivial

###

ID: 
Titel: Statistik-Export-Audit über vorhandenen Helper schreiben 
Kategorie: Refactoring 
Dimension: 8 Konfigurations- und Betriebs-Refactoring 
Fundstelle(n): `src/core/views/statistics.py:182-220`, `src/core/services/audit.py:33-71`

Vorher (Skizze):

```python
AuditLog.objects.create(
    facility=facility,
    user=request.user,
    action=AuditLog.Action.EXPORT,
    target_type="CSV",
    detail={...},
    ip_address=get_client_ip(request),
)
```

Nachher (Skizze):

```python
log_audit_event(
    request,
    AuditLog.Action.EXPORT,
    target_type="CSV",
    detail={...},
)
```

Begründung: `log_audit_event` wurde genau für View-nahe Audit-Einträge gebaut. Die Statistik-Exports duplizieren Facility/User/IP-Aufbau noch inline. 
Aufwand: S (<1h) 
Risiko: niedrig, wenn `target_type`, `target_id` und `detail` unverändert bleiben. 
Voraussetzungen: keine 
Test-Strategie: `src/tests/test_export.py`, `src/tests/test_audit_service.py`, `src/tests/test_statistics.py`. 
Migrations-Bedarf: nein 
Reversibilität: trivial

## C. Strukturelle Refactorings

### Bottom-up

###

ID: 
Titel: Statistik-Periodenparser extrahieren 
Kategorie: Refactoring 
Dimension: 1 Code-Level 
Fundstelle(n): `src/core/views/statistics.py:28-49`, `src/core/views/statistics.py:103-124`

Vorher (Skizze):

```python
if period == "custom":
    date_from = parse_date(...)
elif period == "year":
    selected_year = ...
...
```

Nachher (Skizze):

```python
period_state = parse_statistics_period(request.GET, today)
stats = get_statistics_hybrid(facility, period_state.date_from, period_state.date_to)
```

Begründung: Dashboard und Chart-JSON interpretieren dieselben Query-Parameter separat. Ein Extract Function verhindert Drift, z. B. wenn ein Zeitraum ergänzt wird. 
Aufwand: M (1/2 Tag) 
Risiko: niedrig bis mittel, weil Statistik-UI und Chart-API gemeinsam betroffen sind. 
Voraussetzungen: optional, nicht blockierend. 
Test-Strategie: neue Unit-Tests für `parse_statistics_period` mit `month`, `quarter`, `half`, `year`, `custom`, invalid year; bestehend `src/tests/test_statistics_hybrid.py`, `src/tests/e2e/test_statistics_dashboard.py`. 
Migrations-Bedarf: nein 
Reversibilität: trivial

###

ID: 
Titel: WorkItem-Inbox-Query in QuerySet/Service bündeln 
Kategorie: Refactoring 
Dimension: 2 Django-Patterns 
Fundstelle(n): `src/core/views/workitems.py:46-79`, `src/core/views/workitems.py:85-149`, `src/core/models/workitem.py:135-143`

Vorher (Skizze):

```python
base_qs = WorkItem.objects.for_facility(facility).select_related(...)
base_qs = self._apply_filters(base_qs, request)
open_qs = base_qs.filter(status=...).filter(Q(...))
```

Nachher (Skizze):

```python
base_qs = WorkItem.objects.inbox_base(facility, today)
filtered = apply_workitem_inbox_filters(base_qs, request.GET, request.user)
sections = build_workitem_inbox_sections(filtered, request.user, today)
```

Begründung: `WorkItemInboxView.get` mischt GET-Parsing, Query-Annotation, drei Section-Queries und UI-Limits. Das ist noch kein God-View, aber nah an der Stelle, an der kleine Filteränderungen fehleranfällig werden. 
Aufwand: M (1/2 Tag) 
Risiko: mittel, weil Inbox-Filter UX-kritisch ist. 
Voraussetzungen:, 
Test-Strategie: `src/tests/test_workitem_filters.py`, `src/tests/test_workitem_due_filter.py`, `src/tests/test_workitems.py`; ergänzen: Unit-Test für `build_workitem_inbox_sections` mit offen/in progress/done und Limit+has_more. 
Migrations-Bedarf: nein 
Reversibilität: mit Aufwand

###

ID: 
Titel: Event-Edit-Attachment-Kontext N+1-frei in Service verschieben 
Kategorie: Refactoring 
Dimension: 6 Performance-Refactorings 
Fundstelle(n): `src/core/views/events.py:325-351`, `src/core/services/event.py:130-151`, `src/tests/test_attachment_versioning_stage_b.py`

Vorher (Skizze):

```python
for entry in entries_meta:
    att = event.attachments.filter(pk=entry["id"]).first()
    if not att or att.deleted_at is not None:
        continue
```

Nachher (Skizze):

```python
attachments_by_pk = {att.pk: att for att in event.attachments.all()}
existing_attachments = build_existing_attachment_context(event, attachments_by_pk)
```

Begründung: Die Detailansicht hat schon einen indexbasierten Weg für Attachments. Die Edit-Ansicht fragt pro Entry erneut. Bei vielen Dateien ist das ein klassisches N+1-Muster. 
Aufwand: M (1/2 Tag) 
Risiko: mittel, weil File-Replace/Remove/Add sensibel ist. 
Voraussetzungen: 
Test-Strategie: vorhandene Stage-B-Tests für Multi-File-Update; ergänzen: Query-Count-Test für `EventUpdateView.get` analog `TestEventDetailContextQueryCount`. 
Migrations-Bedarf: nein 
Reversibilität: mit Aufwand

###

ID: 
Titel: Event-Create-Fehlerkontext extrahieren 
Kategorie: Refactoring 
Dimension: 1 Code-Level 
Fundstelle(n): `src/core/views/events.py:93-167`, `src/core/views/events.py:170-248`

Vorher (Skizze):

```python
if not meta_form.is_valid():
    client_id = request.POST.get("client", "")
    client_obj = get_client_or_none(facility, client_id)
    ...
    return render(request, "core/events/create.html", {...})
```

Nachher (Skizze):

```python
context = build_event_create_context(
    request=request,
    meta_form=meta_form,
    data_form=data_form,
)
return render(request, "core/events/create.html", context)
```

Begründung: Der GET-Pfad und mehrere Fehlerpfade rekonstruieren Client, DocumentType, DataForm und Template-Kontext. Das Verhalten ist wichtig, aber die View ist inzwischen wieder schwer zu lesen. Ein Context-Builder hält die HTTP-Orchestrierung klein. 
Aufwand: M (1/2 Tag) 
Risiko: mittel, wegen dynamischer Felder, Quick-Templates und Client-Preselect. 
Voraussetzungen: 
Test-Strategie: `src/tests/test_events.py::TestEventCreateView`, `src/tests/e2e/test_zeitstrom_events.py::TestEventErstellung`; ergänzen: MetaForm-invalid mit ausgewähltem DocumentType behält DynamicFields. 
Migrations-Bedarf: nein 
Reversibilität: mit Aufwand

### Top-down

###

ID: 
Titel: Retention-Strategien als Strategy-Descriptoren ausdrücken 
Kategorie: Refactoring 
Dimension: 4 Datenmodell und Migrationen 
Fundstelle(n): `src/core/services/retention.py:485-551`, `src/core/services/retention.py:612-739`, `src/core/services/retention.py:861-974`

Vorher (Skizze):

```python
# collect_doomed_events
# Strategy 1: Anonymous ...
# enforce_anonymous
# create_proposals_for_facility: Strategy 1 ...
```

Nachher (Skizze):

```python
RETENTION_STRATEGIES = [
    RetentionStrategy("anonymous", anonymous_events_qs, settings_days="retention_anonymous_days"),
    RetentionStrategy("identified", identified_events_qs, settings_days="retention_identified_days"),
    ...
]
```

Begründung: Dieselben vier fachlichen Regeln stehen in drei Codepfaden: Vorschau, tatsächliche Löschung, Proposal-Erzeugung. Der Kommentar `IMPORTANT: Keep in sync` ist ein Geruch, weil Tests Synchronität prüfen müssen, die Struktur aber Drift zulässt. 
Aufwand: L (1-3 Tage) 
Risiko: mittel bis hoch, weil Retention Datenschutzkern ist. Kein Verhalten ändern, nur gleiche Query-Fabriken in allen Pfaden verwenden. 
Voraussetzungen:, vollständiger non-e2e-Testlauf vor Start. 
Test-Strategie: `src/tests/test_retention.py`, `src/tests/test_retention_dashboard.py`, `src/tests/test_retention_bulk_defer.py`, `src/tests/test_retention_redaction.py`; ergänzen: Parametrischer Test, dass jede Strategy in `collect`, `enforce` und `proposal` dieselben Event-IDs liefert. 
Migrations-Bedarf: nein 
Reversibilität: mit Aufwand

###

ID: 
Titel: Zeitstrom-Kontext für Full-View und Partial teilen 
Kategorie: Refactoring 
Dimension: 3 HTMX-spezifisch 
Fundstelle(n): `src/core/views/zeitstrom.py:25-72`, `src/core/views/zeitstrom.py:157-199`, `src/core/services/feed.py:38-124`

Vorher (Skizze):

```python
target_date = self._get_target_date()
...
feed_items = build_feed_items(...)
...
if doc_type_id:
    feed_items = [...]
```

Nachher (Skizze):

```python
feed_context = build_zeitstrom_feed_context(
    request=self.request,
    include_sidebar=False,
)
context.update(feed_context)
```

Begründung: Full-Page und Partial parsen Datum, TimeFilter und DocumentType-Filter separat. Der Feed ist das zentrale Startscreen-Verhalten, daher sollte der Partial-Pfad nicht langsam anders werden. 
Aufwand: M (1/2 Tag bis 1 Tag) 
Risiko: mittel, weil Startseite und HTMX-Filter betroffen sind. 
Voraussetzungen: keine 
Test-Strategie: `src/tests/test_zeitstrom.py`, `src/tests/test_zeitstrom_filters.py`, `src/tests/test_zeitstrom_perf.py`, E2E `src/tests/e2e/test_zeitstrom_filter_bug.py`. 
Migrations-Bedarf: nein 
Reversibilität: mit Aufwand

###

ID: 
Titel: Suche in Kandidaten-Query und Sichtbarkeitsfilter trennen 
Kategorie: Refactoring 
Dimension: 6 Performance-Refactorings 
Fundstelle(n): `src/core/services/search.py:15-98`, `src/tests/test_search.py:55-156`

Vorher (Skizze):

```python
def search_clients_and_events(...):
    clients = ...
    events_by_client = ...
    encrypted_field_slugs = ...
    def _get_field_sensitivities(...):
        ...
    candidates = ...
    for event in candidates:
        ...
```

Nachher (Skizze):

```python
clients = find_client_matches(...)
event_candidates = find_event_candidates(...)
events = filter_events_by_visible_data_fields(event_candidates, user, query)
```

Begründung: Der Service macht DB-Kandidatenfindung, Sensitivity-Filter, Encrypted-Field-Skip und Dedup in einer Funktion. Die Tests sind gut genug, um diese Funktion ohne Verhaltenänderung aufzuteilen. 
Aufwand: M (1/2 Tag) 
Risiko: mittel, weil Suche Privacy-Gates enthält. 
Voraussetzungen: keine 
Test-Strategie: `src/tests/test_search.py:70-156` ist der Kern; zusätzlich ein Test für encrypted field skip plus duplicate event hit in Client- und Data-Pfad. 
Migrations-Bedarf: nein 
Reversibilität: mit Aufwand

###

ID: 
Titel: `core.forms.*` als nächste mypy-Zone aufnehmen 
Kategorie: Refactoring 
Dimension: 1 Code-Level 
Fundstelle(n): `pyproject.toml:45-95`, `docs/adr/012-incremental-mypy.md:13-30`, `src/core/forms/events.py:85-230`

Vorher (Skizze):

```toml
[[tool.mypy.overrides]]
module = "core.services.*"
strict_optional = true
```

Nachher (Skizze):

```toml
[[tool.mypy.overrides]]
module = "core.forms.*"
strict_optional = true
check_untyped_defs = true
```

Begründung: Forms sind die Grenze zwischen unsauberen HTTP-Strings und typisierter Service-Logik. Sie sind klein genug für den nächsten Schritt, aber wertvoller als z. B. Template-Tags. Keine pauschale `Any`-Welle. 
Aufwand: M (1/2 Tag bis 1 Tag) 
Risiko: niedrig bis mittel, CI-only; Risiko ist eher Zeitaufwand als Laufzeitverhalten. 
Voraussetzungen: bis nicht zwingend, aber hilfreich. 
Test-Strategie: `make typecheck`; nach Erweiterung gezielt `mypy src/core/forms src/core/services`. Bestehende Form-/View-Tests schützen Laufzeitverhalten. 
Migrations-Bedarf: nein 
Reversibilität: trivial

## D. Redesigns mit Vorsicht

###

ID: 
Titel: Unverschlüsselte fachliche Freitexte neu entscheiden 
Kategorie: Redesign 
Dimension: 4 Datenmodell und Migrationen 
Fundstelle(n): `src/core/models/client.py:54-63`, `src/core/models/case.py:37-45`, `src/core/models/episode.py:20-27`, `src/core/models/workitem.py:89-97`

Vorher (Skizze):

```python
notes = models.TextField(blank=True, help_text="Nicht feldverschlüsselt ...")
description = models.TextField(blank=True, help_text="Nicht feldverschlüsselt ...")
```

Nachher (Skizze):

```python
# Option klein:
notes_encrypted = models.JSONField(default=dict, blank=True)

# Option groß:
# Freitext nur noch als Event-FieldTemplate mit Sensitivity=HIGH.
```

Begründung: Warnhinweise sind schwächer als technische Kontrolle. In niedrigschwelliger Sozialarbeit landen sensible Inhalte erfahrungsgemäß in Freitexten. Das ist keine reine Strukturfrage, sondern ändert Datenmodell und UI. 
Aufwand: XL (>3 Tage) 
Risiko: hoch, weil Migration, Export, Admin, Suche und Anonymisierung betroffen sind. 
Voraussetzungen: Datenschutzentscheidung der Maintainer*innen; Migrationsplan für bestehende Freitexte. 
Test-Strategie: neue Migrationstests, Exporttests (`src/tests/test_client_export.py`), Anonymisierungsreste (`src/tests/test_anonymize_residue.py`), Suche (`src/tests/test_search.py`), E2E für Formulare. 
Migrations-Bedarf: ja, Datenmigration oder bewusstes Belassen historischer Felder mit Deprecation. 
Reversibilität: praktisch irreversibel

Optionen:

- Nichts tun: Help-Texte bleiben. Geringster Aufwand, höchstes Fehlbedienungsrisiko.
- Kleines Redesign: neue verschlüsselte Felder einführen, alte Felder read-only anzeigen und per Management-Command migrieren.
- Großes Redesign: fachliche Freitexte aus Client/Case/Episode/WorkItem entfernen und vollständig über konfigurierbare, sensitive Event-Felder abbilden.

###

ID: 
Titel: HTMX-Fragmente und JSON-APIs im URL-Schema trennen 
Kategorie: Redesign 
Dimension: 3 HTMX-spezifisch 
Fundstelle(n): `src/core/urls.py:165-208`, `src/core/views/events.py:251-269`, `src/core/views/search.py:40-55`

Vorher (Skizze):

```python
path("api/events/fields/", EventFieldsPartialView.as_view(), ...)
path("api/search/global/", GlobalSearchPartialView.as_view(), ...)
path("api/offline/bundle/client/<uuid:pk>/", OfflineClientBundleView.as_view(), ...)
```

Nachher (Skizze):

```python
path("x/events/fields/", EventFieldsPartialView.as_view(), ...)
path("x/search/global/", GlobalSearchPartialView.as_view(), ...)
path("api/offline/bundle/client/<uuid:pk>/", OfflineClientBundleView.as_view(), ...)
```

Begründung: `api/` enthält aktuell HTML-Partials und JSON-APIs. Das ist für Code egal, aber für Caching, Docs, Tests und Client-Erwartungen unscharf. Da URLs brechen, ist das Redesign, kein Refactoring. 
Aufwand: XL (>3 Tage), wenn sauber mit Redirects/Doku/E2E. 
Risiko: hoch, weil Templates, JS, Service Worker und E2E-Tests URLs referenzieren. 
Voraussetzungen: und reduzieren vorher die verstreute HTMX-Logik. 
Test-Strategie: `src/tests/test_pwa_views.py`, `src/tests/test_offline_bundle_api.py`, E2E `src/tests/e2e/test_zeitstrom_events.py`, `src/tests/e2e/test_offline_apis.py`; zusätzlich URL-Compat-Tests für Redirects. 
Migrations-Bedarf: nein, aber URL-Kompatibilitätsphase. 
Reversibilität: mit Aufwand

Optionen:

- Nichts tun: akzeptabel bis 1.0, wenn dokumentiert wird, dass `api/` auch HTML-Partials enthält.
- Kleines Redesign: nur neue `x/`-Aliases ergänzen, alte `api/`-Partial-Routen deprecated weiter bedienen.
- Großes Redesign: alle Fragment-Endpunkte umziehen, Templates/JS aktualisieren, alte Routen nach einer Release-Phase entfernen.

###

ID: 
Titel: Django-App-Schnitt erst nach Modul-Ownership prüfen 
Kategorie: Redesign 
Dimension: 2 Django-Patterns 
Fundstelle(n): `docs/adr/002-cbvs-and-service-layer.md:13-37`, `src/core/urls.py:93-208`, `src/core/models/__init__.py:1-47`

Vorher (Skizze):

```text
core/
  models/
  views/
  services/
  forms/
```

Nachher (Skizze):

```text
core/
documentation/
retention/
statistics/
offline/
```

Begründung: Die Fachbereiche sind erkennbar, aber Django-App-Splits ändern App-Labels, Migration-Historien, Admin-Imports und ContentTypes. Für Pre-1.0 möglich, aber aktuell kein klarer Nutzen gegenüber Modul-Ownership. 
Aufwand: XL (>3 Tage) 
Risiko: hoch, besonders wegen Migrationen, ContentTypes, RLS-Policies und bestehenden Deployments. 
Voraussetzungen: erst/ abschließen; danach entscheiden, ob weiterhin Schmerz besteht. 
Test-Strategie: vollständige Suite, Migrations-Test auf leerer DB und Upgrade-DB, RLS-Functional-Tests (`src/tests/test_rls_functional.py`). 
Migrations-Bedarf: ja, App-Label-/ContentType-Migrationen. 
Reversibilität: praktisch irreversibel

Optionen:

- Nichts tun: aktuell empfohlen. `core` bleibt eine App, Module bleiben fachlich geschnitten.
- Kleines Redesign: Modul-READMEs/Ownership-Regeln pro Bereich, keine Django-App-Splits.
- Großes Redesign: eigene Django-Apps nur für stabile, klar getrennte Bereiche wie `retention` oder `offline`.

###

ID: 
Titel: AuditLog-Details minimieren und Legacy-Daten behandeln 
Kategorie: Redesign 
Dimension: 8 Konfigurations- und Betriebs-Refactoring 
Fundstelle(n): `src/core/models/audit.py:84-90`, `src/core/models/audit.py:105-116`, `src/core/views/auth.py:119-138`, `src/core/views/clients.py:255-267`

Vorher (Skizze):

```python
detail={"email": email}
detail={"format": "JSON", "pseudonym": client.pseudonym}
```

Nachher (Skizze):

```python
detail={"email_hash": hmac_lookup(email)}
detail={"format": "JSON"}  # target_id reicht
```

Begründung: `AuditLog` ist append-only und hat eine lange Retention. Pseudonyme und E-Mails im Detail sind für Forensik nicht immer nötig. Das ist aber eine Produktentscheidung: weniger PII kann weniger direkte Nachvollziehbarkeit bedeuten. 
Aufwand: XL (>3 Tage), wenn Legacy-Redaktion enthalten ist. 
Risiko: hoch, weil Audit-Forensik und Datenschutz gegeneinander abgewogen werden. 
Voraussetzungen: Forensik-Anforderungen klären: welche Detailfelder sind wirklich nötig? 
Test-Strategie: `src/tests/test_audit_coverage.py`, `src/tests/test_audit_detail.py`, `src/tests/test_auth.py::TestRateLimiting`, neue Tests gegen PII in `AuditLog.detail`. 
Migrations-Bedarf: ja, optionaler Management-Command zur Legacy-Redaktion statt DB-Migration, weil Audit-Trigger Schreibschutz hat. 
Reversibilität: praktisch irreversibel, wenn Legacy-Details gelöscht werden.

Optionen:

- Nichts tun: maximaler Audit-Kontext, mehr personenbezogene Logdaten.
- Kleines Redesign: neue Einträge minimieren, Legacy unverändert lassen.
- Großes Redesign: neue Einträge minimieren und Legacy-Details kontrolliert redigieren, mit dokumentierter Forensik-Ausnahme.

## E. Refactoring-Roadmap

| Phase | Ziel | IDs | Gate |
|---|---|---|---|
| | Mechanische Quick Wins |,,,,,, | `make lint`, gezielte Tests |
|b | HTMX/Audit kleine Konsolidierung |, | Listen-/Audit-Tests |
| | Lokale Struktur verbessern |,,, | `pytest -m "not e2e" src/tests/test_statistics* src/tests/test_workitem* src/tests/test_events.py src/tests/test_attachment_versioning_stage_b.py` |
| | Cross-Path-Deduplikation |,, | vollständige non-e2e Suite, Zeitstrom E2E-Smoke |
| | Typzone ausbauen | | `mypy src/core/forms src/core/services` |
| Nach 1.0-Planung | Produkt-/Datenmodellentscheidungen |,,, | ADR + Migrations-/Rollback-Plan |

## F. Anti-Refactoring-Liste

- `core` jetzt nicht in viele Django-Apps splitten. Die Modulschnitte sind sichtbar, ADR-002 ist stimmig, und ein App-Split würde Migrationen/ContentTypes/RLS anfassen.
- Keine Hexagonal-/Repository-Schicht einziehen. Der ORM ist hier die Persistence-Boundary; zusätzlicher Boilerplate würde dem Solo-Maintainer nicht helfen.
- CBVs nicht pauschal durch FBVs ersetzen. Die Rollen-Mixins und Architekturtests sind auf CBVs ausgerichtet.
- `Event.data_json` nicht aus Prinzip normalisieren. Dynamische Dokumentationstypen sind Kernfeature; normale Spalten wären nur für stabile, hochfrequente Reporting-Felder sinnvoll.
- Migrationen nicht jetzt squashen. Pre-1.0 spricht dafür, aber bestehende Deployments, RLS-Migrationen und Audit-Trigger machen das riskanter als der aktuelle Nutzen.
- mypy nicht global strict schalten. ADR-012 beschreibt den richtigen inkrementellen Weg.
- Funktionierende Datenschutz-Hotspots wie File-Vault, RLS, Audit-Append-only und Retention-Redaction nicht „aufräumen“, solange kein konkreter Bug vorliegt. Diese Pfade sind fachlich kritisch und gut getestet.

## G. Offene Fragen

- Welche Installationen existieren bereits außerhalb der Entwicklung? Das entscheidet, ob Migration-Squash, URL-Redesign und App-Split überhaupt vertretbar sind.
- Sind `Client.notes`, `Case.description`, `Episode.description` und `WorkItem.description` in echten Einrichtungen praktisch genutzt oder nur Altlast? Davon hängt ab.
- Sollen Statistik-PDFs externe Berichte sein oder interne Leitungsinstrumente? Das beeinflusst, ob Pseudonym-Rankings und detaillierte Auditdaten bleiben dürfen.
- Gibt es eine feste URL-Kompatibilitätszusage vor 1.0? Ohne diese kann kleiner umgesetzt werden.
- Welche AuditLog-Details sind für echte Vorfallanalyse notwendig? Ohne diese Antwort sollte nicht umgesetzt werden.
- Soll `docs/audit/` neben dem vorhandenen `docs/audits/` dauerhaft existieren, oder war die Singularform im Prompt nur ein Zielpfad für diese Datei?

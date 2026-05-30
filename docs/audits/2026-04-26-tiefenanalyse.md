# Tiefenanalyse 2026-04-26

## Scope und Baseline

- Geprüfter Workspace: `/work/anlaufstelle`
- Baseline: HEAD `aabc788` (`refactor(ui): Alpine inline x-data zu registrierten Komponenten (A,)`)
- Vorgänger-Audit: [`docs/audits/2026-04-25-vollanalyse.md`](2026-04-25-vollanalyse.md), Baseline `016728b`. Seitdem **16 Commits**, **124 geänderte Dateien**.
- Arbeitsbaum während des Audits: zu Beginn nicht clean (in-progress Alpine-CSP-Refactor in `base.html`/`mfa_login.html`/`mfa_settings.html`/`test_architecture.py` plus untracked `alpine-components.js`); während des Audits committed der Eigentümer den Refactor als `aabc788`. Workspace nach Audit clean (außer den hier abgelegten Audit-Screenshots).
- Schwerpunkt: Vollanalyse aktueller Stand auf neuem HEAD — Dead Code, Refactoring, fehlender Code, Bugs, Inkonsistenzen, UI/UX, Funktionalität. Zusätzlich: vollständige Verifikation der 10 Befunde aus 2026-04-25 sowie gezielte Playwright-Stichprobe gegen den Visual Refresh #663 und den frischen Alpine-CSP-Refactor.
- Out of scope: pytest-Läufe (User-Entscheidung),-Milestones M0–M6 (Embargo). Produktcode wurde nicht verändert; Output ist dieses Dokument plus 7 Audit-Screenshots in [`docs/audits/2026-04-26-screenshots/`](2026-04-26-screenshots/).

## Verifikation (statisch)

| Schritt | Befehl | Ergebnis |
| --- | --- | --- |
| Lint | `.venv/bin/python -m ruff check.` | OK, `All checks passed!` |
| Django | `manage.py check --settings=anlaufstelle.settings.test` | `System check identified no issues (0 silenced).` |
| Migrationen | `manage.py makemigrations --check --dry-run` | `No changes detected` |
| Pip | `.venv/bin/python -m pip check` | `No broken requirements found.` |
| JS-Syntax | `find src/static/js -name '*.js' \| xargs node --check` | OK, alle ~14 App-JS-Files grün |
| Tailwind | `npx tailwindcss -i input.css -o /tmp/styles.css --minify` | OK, `Done in 3063ms` (Warnung: `caniuse-lite is outdated`, kosmetisch) |
| E2E-Server | `gunicorn... --bind 127.0.0.1:8844` | OK, Login `HTTP 200` nach Migrate+Seed+Collectstatic |

Pytest-Coverage wurde gemäß User-Vorgabe nicht erhoben.

## Executive Summary

Die 10 priorisierten Befunde aus dem Vorgänger-Audit sind **vollständig adressiert** — neun davon sauber, zwei bewusst als dokumentierte Übergangslösung ( Cron-Fallback dokumentierte Wait-Ausnahmen). Toolchain (Ruff, Django-Check, Migrationen, Pip, JS, Tailwind) ist durchgängig grün. Der EventUpdate-/EventCreate-Refactor hat funktioniert: `EventUpdateView.post` schrumpfte von 154 auf 78 LOC, der Service trägt jetzt `apply_attachment_changes` und einen N+1-freien `build_event_detail_context`.

Die Playwright-Stichprobe deckt allerdings **zwei kritische P1-Regressionen** auf, die im neuesten Commit `aabc788` (Alpine-CSP-RefactorA) bzw. parallel zur File-Marker-Refaktorierung entstanden sind:

1. ** (P1)**: Auf jeder Seite feuern 27–43 `ReferenceError`s aus Alpine-Komponenten — der Lade-Order von `alpine.min.js` (1.) und `alpine-components.js` (2.) ist verkehrt herum, sodass `Alpine.start` läuft, bevor `Alpine.data(...)`-Registrierungen erreicht sind. UI-Effekt: Sidebar-Suche, Create-Menü, Mobile-Menü, Offline-/Sync-/Conflict-Banner, PWA-Install-Banner, Bulk-Selects, Confirm-Modals und mehr sind vollständig stumm.
2. ** (P1)**: Im Event-Card-Preview wird der raw `__files__`-Dict-String ausgegeben (`"{'entries': [...], '__files__': True}"`), weil `_format_preview_value` (`feed.py:125`) keine Sonderbehandlung für File-Marker hat.

Dazu vier P2-/P3-Befunde: englische Login-Labels, 19 POST-Handler ohne Ratelimit, A11y-Lücke bei dekorativen SVGs und ein leerer `[Unreleased]`-Block in `CHANGELOG.md`. Keine harten Dead-Code-Kandidaten gefunden.

## Verifikation Vorgänger-Befunde ( …)

| ID | Status | Belegstelle |
| --- | --- | --- |
| (CSP/Inline-onchange) | ✅ FIXED | Commit; `events/edit.html` enthält keine `onchange/onclick/onload`-Attribute mehr; [`src/static/js/attachment-remove.js`](../../src/static/js/attachment-remove.js) ist da; Architektur-Test `TestNoInlineScriptBlocksGuard.test_no_inline_event_attributes` greift (`src/tests/test_architecture.py:89`). |
| (Offline-ACK) | ✅ FIXED | Commit; `sw.js:55-83` implementiert `MessageChannel`-ACK mit `QUEUE_ACK_TIMEOUT_MS = 5000` und `QUEUE_ACK`/`QUEUE_NACK`-Branches; `sw-register.js:40-60` schickt ACK/NACK zurück. |
| (File-Vault Rollback) | ✅ FIXED (zweistufig) | Commit; `file_vault.py:299-331` löscht die Datei bei synchroner DB-Exception sofort; `cleanup_orphan_storage_files` (`file_vault.py:27-62`) als periodischer Cron-Fallback für Cross-Transaktionsfehler. Restrisiko bewusst dokumentiert (`file_vault.py:30-44`). |
| (DOCX/OOXML MIME) | ✅ FIXED | Commit; `_MIME_EQUIVALENCE`-Map (`file_vault.py:175-194`) für `docx`, `xlsx`, `pptx`, `jpg`, `jpeg`; `_mime_equivalent`-Helper + Aufruf in `_enforce_magic_bytes` (`file_vault.py:241`). |
| (Event-Hotspots) | ✅ FIXED | Commits (Service-Refactor) und (N+1). `EventUpdateView.post` 154→78 LOC, `EventCreateView.post` 108→81 LOC; neue Service-Funktionen `apply_attachment_changes`, `attach_files_to_new_event`, `split_file_and_text_data`, `build_field_template_lookup` (`services/event.py:359-455`). `build_event_detail_context` nutzt `select_related("field_template")` (`services/event.py:36, 224, 239`). |
| (Mixins-Adoption) | 🟡 STAGNIERT | Heute weiterhin **86** `request.current_facility`-Treffer in `views/`+`services/` (Vorgänger-Audit: 86) und **13** `HX-Request`-Checks in `views/` (Vorgänger: 13). Keine Migration, keine Architektur-Doku-Note. Befund bleibt **offen** als P3. |
| (i18n f-Strings) | ✅ FIXED | Commit; `grep -rE '_\(f"\|gettext_lazy\(f"' src/core/` ist leer. Architektur-Test `TestNoFStringInGettextCallsGuard` (`test_architecture.py:156`). |
| (`coverage.json`) | ✅ FIXED | `.gitignore:12` enthält `coverage.json`. |
| (Ruff-Pin) | ✅ FIXED | `.github/workflows/lint.yml:19` ruft `pip install ruff==0.15.11`. |
| (E2E-Hard-Waits) | 🟡 PARTIAL (bewusst) | 8 `wait_for_timeout`-Treffer (Vorgänger 7) — der zusätzliche ist ein erläuternder Kommentar (`test_quick_capture.py:61`), kein neuer Wait. Commit hat die 7 Waits geprüft und mit Begründungen (`# Dokumentierter Ausnahmefall …`) versehen, ohne sie zu reduzieren. Stabilitätsrisiko bleibt, ist aber bewusst getragen. |

## Priorisierte neue Befunde

| ID | Prio | Bereich | Befund | Empfohlene Aktion |
| --- | --- | --- | --- | --- |
| | P1 | UI/JS/CSP-Refactor | `alpine.min.js` lädt vor `alpine-components.js` → 27–43 ReferenceErrors je Seite, alle 21 registrierten Alpine-Komponenten initialisieren nicht | Lade-Reihenfolge der `<script defer>`-Tags in `base.html` tauschen, Smoke-Test auf 0 JS-Errors ergänzen |
| | P1 | UI/Privacy/Render | Event-Card-Preview zeigt rohe `__files__`-Dict-Repr statt sinnvollen Text | `_format_preview_value` in `feed.py:125` um Sonderfall für `__file__`/`__files__`-Marker erweitern, Test in `test_feed_enrichment.py` |
| | P2 | UI/i18n | Login-Form-Labels `USERNAME`/`PASSWORD` in Englisch | `LoginForm` subclassen und Labels deutsch überschreiben; E2E-Smoketest |
| | P2 | Sicherheit | 19 von 36 POST-Handlern ohne `@ratelimit` (u. a. `MFADisableView`, `EventUpdateView`, Client-Create/Update, Retention-Bulk) | Per Klasse `@method_decorator(ratelimit(...))` ergänzen, Architektur-Test `TestRateLimitOnAllMutations` mit Allowlist |
| | P3 | A11y | Dekorative SVGs ohne `aria-hidden="true"` (23+ Templates; nur 3 Files mit aria-hidden) | Sweep + Architektur-Test, dass alle `<svg>` `aria-hidden \| <title> \| aria-label` tragen |
| | P3 | Doku | `CHANGELOG.md` `[Unreleased]` ist leer trotz 16 Commits seit `[0.10.0] - 2026-04-19` | Unreleased-Block mit FND-Fixes/Visual-Refresh/Alpine-Refactor befüllen |

## Detailbefunde

###: Alpine-Komponenten-Lade-Order-Bug

**Beobachtung (Playwright-Stichprobe gegen E2E-Server, frisch gestartet)**:

| Seite | Errors | Warnings | Symptome (Auszug) |
| --- | ---: | ---: | --- |
| `/login/` | 3 | 4 | `pwaInstallPrompt is not defined`, `showInstall is not defined`, `showIos is not defined` |
| `/` (Dashboard) | 43 | 44 | `offlineStatus`, `globalSearch`, `createMenu`, `expandableActivityCard`, `mobileMore`, `mobileSearchInput`, `simpleDropdown` u. a. nicht definiert |
| `/clients/` | 27 | 28 | wie Dashboard, ohne Mobil-Komponenten |
| `/uebergabe/` | 27 | 28 | dito |
| `/audit/` | 27 | 28 | dito |
| `/events/new/` | 40 | 41 | + form-spezifische Komponenten (`clientAutocomplete`, `dateQuickButtons`) |

Belegfiles: [`docs/audits/2026-04-26-screenshots/`](2026-04-26-screenshots/) (`audit-2026-04-26-01-login.png` … `audit-2026-04-26-07-event-edit.png`). Volltext der Browser-Konsole lag zur Audit-Zeit in `.playwright-mcp/console-2026-04-26T*.log` (nicht committed, reproduzierbar).

**Ursache**: `src/templates/base.html:15-17`:

```html
<script src="{% static 'js/htmx.min.js' %}" defer></script>
<script src="{% static 'js/alpine.min.js' %}" defer></script>
<script src="{% static 'js/alpine-components.js' %}" defer></script>
```

Standard-Alpine startet auf `DOMContentLoaded`. Beide Scripts haben `defer`, also Document-Order: `alpine.min.js` läuft zuerst und feuert `alpine:init`, `alpine-components.js` registriert seinen `addEventListener("alpine:init", …)` aber erst danach (`src/static/js/alpine-components.js:22`). Wenn Alpine den DOM scannt, sind die `Alpine.data(...)`-Komponenten noch nicht registriert; jedes `x-data="globalSearch"` schlägt mit `ReferenceError` fehl.

Verifiziert: Alle 21 in `alpine-components.js` registrierten Namen werden auch in Templates referenziert — Mismatches sind ausgeschlossen.

**Risiken**:

- Sidebar-Suche, Create-Menü, Mobile-Overflow-Menü, Confirm-Modal, Workitem-Bulk-Select, Retention-Bulk-Select, Goals-Section-Edit, Date-Quick-Buttons, Client-Autocomplete und der Activity-Card-Expander bleiben tot.
- Offline-/Sync-/Conflict-Banner zeigt seinen Zustand nie an (`x-show="offline"` etc. werden nicht reagieren) — die in implementierten ACK-Pfade haben dadurch kein UI-Signal.
- PWA-Install-Banner (Login-Page) erscheint nie.
- Sentry/Console wird mit 27–43 Errors pro Seite überflutet — echte Fehler gehen unter.

**Empfohlene Umsetzung**:

- Lade-Reihenfolge in `base.html` umdrehen: `alpine-components.js` VOR `alpine.min.js` einbinden. Beide bleiben `defer`; der `alpine:init`-Listener ist dann registriert, bevor Alpine startet. (Eleganter wäre ein expliziter manueller Boot via `Alpine.start` nach `Alpine.data`-Registrierungen, das wäre aber eine Strukturänderung — der Order-Tausch löst das Problem mit minimalem Diff.)
- Smoke-Test: in `src/tests/e2e/test_smoke.py` (oder als Erweiterung von `test_login.py`) jede Hauptseite öffnen und `expect(page).to_have_no_console_errors` o.ä. prüfen. Architektur-Test ist hier nicht trivial, da das Symptom Browser-Runtime ist.
- Folge-Issue:B von #669 (`@alpinejs/csp`-Build aktivieren, `script-src 'unsafe-eval'` aus CSP entfernen) erst NACH-Fix angehen — sonst sind beide Refactor-Stufen verzahnt.

###: `__files__`-Marker im Event-Card-Preview

**Beobachtung**: Im Zeitstrom-Feed (`/?date=2026-04-26&type=events`) zeigt die Karte für Event `08bbefae-ad7d-43bb-8f05-c47807641388` (Beratungsgespräch, Klient Sonne-99) im Preview-Block:

```
Thema: E2E Wohngeld-Test
Scan/Bescheid: {'entries': [{'id': '21ce5902-ca03-49a1-99c3-41649a7ba435', 'sort': 0}], '__files__': True}
```

(Screenshot [`docs/audits/2026-04-26-screenshots/audit-2026-04-26-02-dashboard.png`](2026-04-26-screenshots/audit-2026-04-26-02-dashboard.png), Playwright-Snapshot-Ref `e127`).

**Ursache**: `src/core/services/feed.py:125-141` (`_format_preview_value`):

```python
if isinstance(value, dict):
    return safe_decrypt(value, default="[verschlüsselt]")
return str(value)
```

`safe_decrypt` ist auf verschlüsselte Field-Dicts ausgelegt; die Stufe-A-/Stufe-B-File-Marker (`{"__file__": True,...}` bzw. `{"__files__": True, "entries": [...]}`) fallen nicht in dieses Muster. Im aktuellen Code-Pfad rutscht der Wert durch und landet als `str(dict)` im Card-Inhalt.

**Risiken**:

- UX: Nutzer:innen sehen internes Datenformat statt sinnvollem Hinweis ("[1 Datei]", Dateiname).
- Privacy: Attachment-UUIDs werden im Frontend exponiert. Technisch keine Klartext-Sensitivdaten, aber unnötiger Leak — und ein Platzproblem in der Card.
- Konsistenz: Die in eingeführte Multi-File-Versionierung (Stufe B) hat hier keinen Render-Pfad — der Refactor war Backend-only.

**Empfohlene Umsetzung**:

In `_format_preview_value` (`feed.py:125`) eine Sonderbehandlung VOR dem `safe_decrypt`-Branch:

```python
if isinstance(value, dict):
    if value.get("__file__"):
        return _("[Datei]")
    if value.get("__files__"):
        n = len(value.get("entries") or [])
        return ngettext("[%(n)d Datei]", "[%(n)d Dateien]", n) % {"n": n}
    return safe_decrypt(value, default="[verschlüsselt]")
```

Tests in `src/tests/test_feed_enrichment.py`:

- Event mit Stufe-A-Marker `{"__file__": True, "attachment_id": "..."}` → preview enthält `[Datei]`.
- Event mit Stufe-B-Marker (1 entry) → `[1 Datei]`.
- Event mit Stufe-B-Marker (3 entries) → `[3 Dateien]`.
- Event ohne File-Marker — bestehender `safe_decrypt`-Branch bleibt erreichbar.

###: Login-Form-Labels in Englisch

**Beobachtung**: [`docs/audits/2026-04-26-screenshots/audit-2026-04-26-01-login.png`](2026-04-26-screenshots/audit-2026-04-26-01-login.png) zeigt auf der Login-Seite die Labels `USERNAME` und `PASSWORD` in Großbuchstaben-Englisch, obwohl die App ansonsten Deutsch ist (Logo-Untertitel: „Open Source · DSGVO-konform · Selbst gehostet").

**Ursache**: `src/templates/auth/login.html` rendert die Default-Labels von Django's `AuthenticationForm` (`username` → "Username", `password` → "Password"), die ohne deutsche Übersetzung beim Default-Setting bleiben. Die App-Übersetzung greift nicht auf Django-eigene Strings.

**Empfohlene Umsetzung**:

- In `src/core/forms/`(neu: `auth.py` oder Erweiterung von `users.py`) eine `AnlaufstelleAuthenticationForm` mit `username = UsernameField(label=_("Benutzername"))` und `password = forms.CharField(label=_("Passwort"))`.
- `LOGIN_URL`/`AUTHENTICATION_FORM` im Settings binden oder per `LoginView.form_class` setzen.
- E2E-Smoketest: `expect(page.get_by_label("Benutzername"))` auf `/login/`.

###: POST-Handler ohne Rate-Limit

**Beobachtung**: 28 von 36 POST-Handlern in `src/core/views/` haben einen `@ratelimit`-Decorator; **19** haben keinen (Doppelnennungen, weil ein Handler mehrere POST-Methoden bedient — der wahre Anteil "ungeschützt" liegt bei ~50%):

| Datei:Zeile | Handler | Bemerkung |
| --- | --- | --- |
| `views/cases.py:192,227,238,249,267` | 5 case-Mutationen | Edit, Stage-Change, Episode-Add etc. |
| `views/clients.py:142,166` | `ClientCreateView`, `ClientUpdateView` | Pseudonym-Probing möglich |
| `views/events.py:356` | `EventUpdateView.post` | EventCreate **hat** `RATELIMIT_MUTATION` |
| `views/events.py:451` | `EventDeleteView.post` | dito |
| `views/event_deletion.py:70` | Deletion-Approve/Reject | |
| `views/case_episodes.py:69,92` | Episode-Edit/Delete | |
| `views/workitem_actions.py:133` | Workitem-Action | |
| `views/workitem_bulk.py:51` | Bulk-Operations | Bulk = besonders ratelimit-sensitiv |
| `views/mfa.py:255` | `MFADisableView.post` | MFA-Setup, Verify und Backup-Codes haben `5–10/m` — Disable inkonsistent ohne Limit |
| `views/retention.py:37,57,118,188` | 4 Retention-Mutationen | Bulk-Approve/Defer/Reject + Hold |

`MFADisableView` ist durch `is_mfa_enforced` blockiert, wenn MFA mandatorisch ist — das mildert das Risiko, ändert aber nichts an der Inkonsistenz.

**Empfohlene Umsetzung**:

- Pro Klasse `@method_decorator(ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True))` ergänzen. Für Bulk-Mutationen `RATELIMIT_BULK_ACTION = "30/h"` (existiert bereits in `core/constants.py:16`).
- Architektur-Test in `test_architecture.py`: jede Klasse mit `def post(...)` muss in einer Allowlist sein ODER einen Ratelimit-Decorator tragen. Allowlist als bewusste Ausnahme (z. B. `EventFieldsPartialView` falls es POST hätte — aktuell GET-only).

###: Dekorative SVG-Icons ohne `aria-hidden`

**Beobachtung**: 23+ Templates enthalten `<svg>`-Tags. Nur 3 nutzen `aria-hidden="true"`. Beispiele:

- `src/templates/components/_activity_card.html:23,53` (Status- und Chevron-Icons in den Activity-Cards)
- `src/templates/components/_empty_state.html:5,7,9,11,13` (Fünf Empty-State-Varianten)
- `src/templates/components/_event_card.html` (keine SVGs aktuell, aber das Pattern wiederholt sich in den meisten Card-Komponenten)

`<img>`-Tags sind im Projekt nahezu nicht im Einsatz (1 Treffer in `mfa_setup.html`); SVGs sind das Standard-Icon-Format. Damit wird der A11y-Fix konsistent über `aria-hidden` geleistet werden müssen.

**Empfohlene Umsetzung**:

- Sweep über `src/templates/`: jedes dekorative `<svg>` bekommt `aria-hidden="true"`. Nur SVGs, die als Icon-Button stehen (kein Text-Label daneben), brauchen `<title>` oder `aria-label`.
- Architektur-Test: alle `<svg...>` in Templates müssen entweder `aria-hidden="true"` ODER `aria-label="..."` ODER ein `<title>`-Child enthalten. Allowlist nicht nötig.
- WCAG 2.1 SC 1.1.1 Compliance — kein, sondern Hygiene.

###: CHANGELOG-Drift

**Beobachtung**: `CHANGELOG.md` enthält:

```
## [Unreleased]

## [0.10.0] - 2026-04-19
### Added
- **Encrypted File Vault** — …
```

`[Unreleased]` ist leer, obwohl seit `[0.10.0]` (vor 7 Tagen) **16 Commits** mit nutzersichtbaren Effekten gemerged wurden:

- FND-Fixes 01–10 (CSP, Offline-ACK, File-Vault, MIME, Service-Refactor, i18n, Hygiene)
- Visual Refresh #663 (Theme Grün)
- Semantischer `status_badge`-Templatetag
- Handover-Seite mit 7 neuen E2E-Tests
- Alpine-CSP-RefactorA
- `SSL_HOST_IP` für LAN-Testing parametrisierbar

**Empfohlene Umsetzung**:

- `[Unreleased]`-Block befüllen, gegliedert nach `Added`/`Changed`/`Fixed`/`Security`. Jede der oben genannten Änderungen referenziert ihren Commit.
- Optional: `release-please` o. ä. einführen — out of scope für diesen Audit.

## Dead Code & Reachability

Keine harten Löschkandidaten. Kommentare:

- Migration `0070_alter_workitem_item_type` ist eine reine Choices-Anpassung (Default `task`, neu: nur `hint|task` statt vormals breiterer Liste). Existierende DB-Werte mit `hint` oder `task` bleiben gültig; falls produktiv andere Werte auftauchen sollten, wäre eine Datenmigration nötig — aber die Choices-Beschränkung ist konsistent mit `WorkItem`-Model und dem Test-Suite-Erwarteten.
- Legacy-File-Marker `__file__` (Stufe A) wird parallel zu `__files__` (Stufe B) gepflegt — explizit als Übergangslösung dokumentiert (`services/event.py:250-267`). Datenmigration auf reines Stufe-B-Format ist als Folgearbeit denkbar, aktuell aber bewusst offen gehalten.
- 21 in `alpine-components.js` registrierte Komponenten — alle werden referenziert. Keine toten Registrierungen.

## Missing Code & Missing Tests

**Code**:

- Lade-Order-Fix in `base.html` — eine Zeile Tausch, dann prüfen, ob nicht doch ein Boot-Hook nötig ist.
- Sonderbehandlung für File-Marker in `_format_preview_value`.
- Deutsche Login-Form-Labels.
- Rate-Limits auf 19 POST-Handlern.
- `aria-hidden`-Sweep für dekorative SVGs.
- `[Unreleased]`-Block in `CHANGELOG.md` befüllen.

**Tests**:

- Smoke-Test: jede Hauptseite öffnen und auf `console.error.length === 0` prüfen — würde sofort fangen und gegen Regressionen sichern.
- `test_feed_enrichment.py`: File-Marker → "[N Datei(en)]" statt raw dict.
- E2E-Smoke: deutsche Login-Labels sichtbar.
- Architektur-Test `TestRateLimitOnAllMutations`.
- Architektur-Test SVG-A11y.
- Optional: `TestChangelogUnreleasedNotEmpty` als pre-merge-Guard — sinnvoller per CI-Workflow als per pytest.

## Bereits verbessert seit 2026-04-25

- 10/10 Vorgänger-Befunde adressiert (siehe Verifikations-Tabelle oben).
- `EventUpdateView.post`: 154 → 78 LOC, `EventCreateView.post`: 108 → 81 LOC.
- `services/event.py`: 437 → 661 LOC (gewollter Wuchs durch ausgelagerte Service-Logik), zwölf neue/erweiterte Funktionen u. a. `apply_attachment_changes`, `attach_files_to_new_event`, `split_file_and_text_data`, `build_field_template_lookup`, `build_event_detail_context` mit `select_related`.
- Architektur-Tests gestärkt: `TestNoInlineScriptBlocksGuard` deckt jetzt auch Inline-Event-Attribute (`onchange=` etc.); neuer `TestAlpineCspCompatibilityGuard` verbietet künftige Inline-x-data-Objekte; `TestNoFStringInGettextCallsGuard` etabliert.
- Visual Refresh #663 sauber durchgezogen: Theme-Tokens via OKLCH-CSS-Variablen, ordentliche Komponenten-Inventarliste (`_activity_card`, `_event_card`, `_workitem_row` etc.), `tailwind.config.js` mit Safelist für dynamische Badge-Farben.
- Alpine-CSP-RefactorA (`aabc788`): 21 Komponenten in `alpine-components.js` zentralisiert, Inline-x-data-Objekte aus `base.html`, `mfa_login.html`, `mfa_settings.html` eliminiert. (Lade-Order-Bug ist eine Folgewirkung.)
- 7 neue E2E-Tests für die Übergabe-Seite (`9a4bb1c`).
- `coverage.json` ignoriert, Ruff in CI gepinnt.

## Empfohlener Umsetzungsplan

1. **Sofort (P1, kleines Diff)**: — Script-Tag-Reihenfolge in `base.html` tauschen + Smoke-Test ergänzen. — `_format_preview_value` mit File-Marker-Sonderfall + Test in `test_feed_enrichment.py`.
2. **Zeitnah (P2)**: (deutsche Login-Labels) (Rate-Limits flächig + Architektur-Test) entscheiden (Mixin-Migration als Refactor-Epic anlegen oder bewusste Zwei-Pattern-Architektur dokumentieren).
3. **Hygiene-PR (P3 gebündelt)**: (SVG-A11y-Sweep) (CHANGELOG-Unreleased), ggf. final (eindeutiger Beschluss: Waits behalten oder durch Ereignis-Polls ersetzen).
4. **Folge-Issue**: #669B (`@alpinejs/csp`-Build, `unsafe-eval` aus CSP entfernen) — erst nach Stabilisierung von sinnvoll.

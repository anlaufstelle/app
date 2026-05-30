# Tiefenanalyse 2026-04-25

## Scope und Baseline

- Geprüfter Workspace: `/work/anlaufstelle`
- Baseline während der Erstellung: `e3bb039` (`refactor: Magic Numbers nach core.constants konsolidiert`)
- Arbeitsbaum war nicht clean: fremde uncommitted Änderungen in `src/core/templatetags/core_tags.py`, `src/templates/core/cases/partials/status_badge.html` sowie untracked `coverage.json`
- Produktcode wurde für dieses Audit nicht verändert; dieses Dokument ist der einzige geplante Output.

Der Schwerpunkt lag auf Dead Code, Refactoring, fehlendem Code, Bugs, Inkonsistenzen, UI/UX und Funktionalität. Die Analyse berücksichtigt die bestehenden Audit-Dokumente vom 2026-04-21 und 2026-04-23, bewertet aber den aktuellen Stand neu.

## Verifikation

| Check | Ergebnis |
| --- | --- |
| `.venv/bin/python -m ruff check.` | OK, `All checks passed!` |
| `.venv/bin/python src/manage.py check --settings=anlaufstelle.settings.test` | OK, keine System-Check-Probleme |
| `.venv/bin/python src/manage.py makemigrations --check --dry-run --settings=anlaufstelle.settings.test` | OK, `No changes detected`; Warnung wegen nicht erreichbarer lokaler PostgreSQL-DB |
| `find src/static/js... node --check` | OK, keine JS-Syntaxfehler |
| `npx tailwindcss -i src/static/css/input.css -o /tmp/anlaufstelle-styles.css --minify` | OK; Warnung: `caniuse-lite is outdated` |
| `.venv/bin/python -m pip check` | OK, keine kaputten Requirements |
| `.venv/bin/python -m pytest -m "not e2e" -x --cov=core --cov-report=json` | Lokal blockiert durch fehlendes PostgreSQL auf `localhost:5432`; kein Code-Failure ableitbar |

`coverage.json` ist untracked und stammt aus einem älteren Lauf vom 2026-04-23 (`91.83%`). Für dieses Audit wurde keine aktuelle Coverage-Zahl abgeleitet, weil der lokale Testlauf am DB-Setup scheitert.

## Executive Summary

Der aktuelle Stand ist strukturell deutlich besser als im Audit vom 2026-04-23: Ruff ist grün, Django-Systemchecks sind grün, Migrationen sind konsistent, Docker-Healthcheck und CI-Caches existieren, die letzten AuditLog-Lücken für `close_case`, `reopen_case` und `delete_milestone` wurden geschlossen, und Magic Numbers wurden teilweise zentralisiert.

Die verbleibenden Hauptprobleme liegen nicht in Syntax oder Migrationen, sondern in produktionsnaher Funktionalität: ein CSP-blockierter Inline-Handler bricht die Attachment-Entfernung im Event-Edit, die Offline-Queue bestätigt Speicherung auch dann, wenn sie clientseitig fehlschlägt, File-Storage ist nicht rollback-sicher, und die neue Attachment-Versionierung enthält weiterhin View-/Service-Hotspots sowie N+1-Query-Risiken.

Keine hochsicheren Dead-Code-Kandidaten sollten ohne Produktentscheidung gelöscht werden. Die relevanten "Dead-Code-nahen" Punkte sind eher halb eingeführte Abstraktionen, stale Artefakte und Guard-Lücken.

## Priorisierte Befunde

| ID | Prio | Bereich | Befund | Empfohlene Aktion |
| --- | --- | --- | --- | --- |
| | P1 | UI/Funktionalität/CSP | Attachment-Entfernung im Event-Edit hängt an `onchange=...`, wird aber durch CSP ohne `unsafe-inline` blockiert. | Handler in externe JS-Datei verschieben und Architekturtest für Inline-Event-Attribute ergänzen. |
| | P1 | Offline/Funktionalität | Service Worker meldet "lokal verschlüsselt gespeichert", bevor die Page bestätigt hat, dass IndexedDB/Session-Key wirklich gespeichert haben. | ACK-Protokoll via `MessageChannel` oder Fehlerrückgabe statt Success-Banner. |
| | P2 | File-Storage/Konsistenz | Verschlüsselte Datei wird vor DB-Commit geschrieben; bei späterem Rollback bleiben Orphan-Dateien möglich. | Temp-Datei plus `transaction.on_commit`, oder Cleanup-Liste bei Exception/Rollback. |
| | P2 | Upload-Validierung | MIME-Prüfung verlangt Gleichheit von Browser-MIME und libmagic-MIME; erlaubte Containerformate wie DOCX können falsch abgewiesen werden. | MIME-Äquivalenzmap je Extension und Tests für DOCX/OOXML ergänzen. |
| | P2 | Performance/Refactoring | Event-Attachment-Versionierung erzeugt komplexe View-Logik und potenzielle N+1-Queries. | Attachment-Update/Anzeige in Service kapseln und Attachments vorladen. |
| | P3 | Architektur | `FacilityScopedViewMixin` und `HTMXPartialMixin` existieren, aber alte Views nutzen weiter direkte Patterns. | Entweder Migration als Refactoring-Epic planen oder partielle Adoption bewusst dokumentieren. |
| | P3 | Qualität/i18n | Einige Übersetzungen verwenden `_(f"...")`, was gettext-Extraktion erschwert. | Auf Platzhalter-Strings mit `%`-Interpolation umstellen. |
| | P3 | Repo-Hygiene | `coverage.json` ist stale, untracked und nicht in `.gitignore`. | Entweder ignorieren oder bewusst committen und automatisiert aktualisieren. |
| | P3 | CI-Reproduzierbarkeit | CI installiert `ruff` unpinned, während `requirements-dev.txt` `ruff==0.15.11` pinnt. | Lint-Workflow aus `requirements-dev.txt` installieren lassen oder Version pinnen. |
| | P3 | E2E-Stabilität | Es gibt noch 7 echte `page.wait_for_timeout`-Aufrufe in E2E-Tests. | Schrittweise auf ereignisbasierte Assertions/Locator-Waits umstellen. |

## Detailbefunde

###: CSP blockiert Attachment-Entfernen

In `src/templates/core/events/edit.html:67` bis `:79` wird die Entfernen-Checkbox über ein inline `onchange` gesteuert. Gleichzeitig definiert `src/anlaufstelle/settings/base.py:236` bis `:244` eine CSP mit `script-src 'self' 'unsafe-eval'`, aber ohne `unsafe-inline`.

Damit wird dieser Handler in Browsern mit aktiver CSP blockiert. Nutzer können die Checkbox optisch aktivieren, aber das versteckte Feld `<field>__remove` wird nicht zuverlässig gesetzt. Im Backend kommt dann keine Entfernen-Liste an.

Konkrete Risiken:

- Attachment-Entfernung wirkt im UI möglich, hat aber keine Wirkung.
- Der bestehende Architekturtest `TestNoInlineScriptGuard` prüft nur `<script>`-Blöcke, nicht `onchange=`, `onclick=` oder andere Inline-Event-Attribute.
- Die Regression passt exakt zum bekannten Muster aus früheren CSP-Problemen: sichtbarer UI-Button, aber stumm blockiertes JavaScript.

Empfohlene Umsetzung:

- Markup auf `data-attachment-remove` und `data-remove-target="<field>__remove"` umstellen.
- Delegierten Listener in `src/static/js/...` registrieren.
- Architekturtest ergänzen: Templates dürfen keine Inline-Event-Attribute `\son[a-z]+\s*=` enthalten.
- Browser-/E2E-Test für "bestehendes Attachment entfernen" ergänzen.

###: Offline-Queue bestätigt Speicherung ohne ACK

`src/static/js/sw.js:101` bis `:123` sendet `QUEUE_REQUEST` an Clients und gibt sofort einen 200-HTMX-Response mit Erfolgshinweis zurück. `src/static/js/sw-register.js:38` bis `:55` versucht danach `window.offlineQueue.enqueueRequest(...)`, loggt Fehler aber nur per `console.warn`. `src/static/js/offline-queue.js:68` bis `:82` kann legitimerweise Fehler werfen, z. B. bei fehlendem Session-Key oder multipart uploads.

Das ist funktional kritisch: Der Nutzer sieht "Ihre Eingaben wurden lokal verschlüsselt", obwohl der Client die Queue eventuell gar nicht persistiert hat.

Konkrete Risiken:

- Datenverlust bei offline POST, wenn `offlineQueue` nicht geladen ist.
- Datenverlust bei abgelaufenem oder fehlendem Crypto-Session-Key.
- Kein sichtbarer Fehler für Nutzer, nur Konsolenwarnung.
- Tests validieren aktuell nicht den negativen ACK-Pfad.

Empfohlene Umsetzung:

- Service Worker muss auf ein explizites ACK/NACK warten, z. B. via `MessageChannel` mit Timeout.
- Bei NACK/Timeout sollte der Response 503 oder ein roter HTMX-Fehlerbanner sein, nicht ein gelber Success-Banner.
- `sw-register.js` sollte Fehler nicht nur loggen, sondern an den Service Worker zurückmelden.
- Tests: "NoSessionKey" und "offlineQueue fehlt" müssen einen sichtbaren Fehler statt Success ergeben.

###: File-Storage ist nicht transaktional rollback-sicher

`src/core/services/file_vault.py:200` bis `:229` validiert Uploads, schreibt dann per `encrypt_file(uploaded_file, output_path)` auf die Platte und erstellt erst danach `EventAttachment.objects.create(...)`. Aufrufer wie `src/core/views/events.py:238` bis `:265` legen Event und Attachments zwar in `transaction.atomic`, aber das Dateisystem rollt nicht mit der DB zurück.

Wenn nach dem Disk-Write ein DB-Fehler, ein späterer Attachment-Fehler oder ein Event-Save-Fehler passiert, kann eine verschlüsselte Datei ohne DB-Referenz liegen bleiben. Bei Multi-Upload erhöht sich das Risiko, weil spätere Dateien einen Rollback nach bereits geschriebenen Vorgängern auslösen können.

Empfohlene Umsetzung:

- In `store_encrypted_file` erst in eine temporäre Datei schreiben.
- DB-Record und finaler Dateiname müssen über einen klaren Commit-Pfad verbunden werden.
- Bei Exceptions alle in der aktuellen Operation geschriebenen Dateien löschen.
- Alternativ finalen Move per `transaction.on_commit` durchführen und bei Rollback Temp-Datei entfernen.
- Test: künstlich nach `encrypt_file` oder nach erstem Attachment eine Exception auslösen und sicherstellen, dass keine Orphan-Datei bleibt.

###: MIME-Prüfung kann erlaubte DOCX-Dateien ablehnen

`src/core/services/file_vault.py:142` bis `:152` akzeptiert nur, wenn `detected_mime == declared_mime` oder libmagic `application/octet-stream` liefert. Gleichzeitig sind DOCX-Dateien standardmäßig erlaubt (`pdf,jpg,jpeg,png,docx`). OOXML/DOCX ist ein ZIP-basiertes Containerformat; Browser und libmagic liefern dafür je nach System unterschiedliche MIME-Werte.

Das ist sicherheitsseitig nachvollziehbar, aber aktuell zu streng modelliert. Die Tests decken PDF/PNG/PE ab, aber nicht die erlaubten Office-Container.

Empfohlene Umsetzung:

- Extension-basierte Äquivalenzmap pflegen, z. B. `.docx` erlaubt `application/vnd.openxmlformats-officedocument.wordprocessingml.document`, `application/zip` und bekannte OOXML-Detektionen.
- Mismatch-Logging beibehalten, aber nur echte Widersprüche ablehnen.
- Tests für valide DOCX und "EXE als DOCX/PDF" ergänzen.

###: Event-Attachment-Code bleibt Hotspot

Aktuelle Strukturmetriken zeigen weiterhin klare Hotspots:

| Element | Größe |
| --- | ---: |
| `src/core/views/events.py` | 439 logische LOC |
| `src/core/services/event.py` | 437 logische LOC |
| `EventUpdateView` | 224 Zeilen |
| `EventUpdateView.post` | 154 Zeilen |
| `EventCreateView.post` | 108 Zeilen |
| `create_event` | 100 Zeilen |

Die Attachment-Versionierung ist fachlich sinnvoll, liegt aber noch zu stark in View-Flows. Zusätzlich lädt `src/core/services/event.py:163` bis `:170` je Entry ein Attachment und baut dann Versionsketten über weitere Queries. Das ist bei vielen Attachments und Historien ein N+1-Muster.

Empfohlene Umsetzung:

- Service-Funktion für "apply attachment mutations" einführen: Add, Replace, Remove, Preserve.
- `EventUpdateView.post` auf Orchestrierung reduzieren: Forms validieren, Service aufrufen, redirect/render.
- `build_event_detail_context` mit vorab geladenen Attachments arbeiten lassen.
- Query-Count-Test für Event-Detail mit mehreren File-Entries und Versionsketten ergänzen.

###: Teilweise eingeführte Mixins erzeugen Inkonsistenz

`FacilityScopedViewMixin` und `HTMXPartialMixin` existieren, aber der Code enthält weiterhin 86 direkte Treffer für `request.current_facility` in Views/Services und 13 direkte `HX-Request`-Checks in Views. Der Kommentar in `src/core/views/mixins.py` sagt, dass alte Views bewusst unangetastet bleiben. Das ist vertretbar, aber es ist dann eine dokumentierte Übergangsarchitektur, keine abgeschlossene Konsolidierung.

Empfohlene Umsetzung:

- Entscheidung treffen: Migration als technisches Refactoring-Epic oder Mixins nur für neue/split Views.
- Wenn Migration: modulweise vorgehen und pro Modul Tests unverändert grün halten.
- Wenn keine Migration: README/Architekturhinweis ergänzen, damit neue Beiträge nicht beide Patterns zufällig vermischen.

###: i18n-Strings mit f-Strings

`src/core/forms/events.py:217` und `src/core/models/document_type.py:263` verwenden `_(f"...")`. Dadurch sieht gettext nicht den stabilen Quellstring, sondern Python baut den String bereits vorher dynamisch.

Empfohlene Umsetzung:

- Auf `_("Dateityp.%(ext)s nicht erlaubt. Erlaubt: %(allowed)s") % {...}` umstellen.
- Gleiche Regel in Review-Guidelines oder Architekturtest aufnehmen, falls i18n hohe Priorität hat.

###: Stale Coverage-Artefakt

`.gitignore:8` bis `:12` ignoriert `.coverage`, `coverage.xml` und `htmlcov/`, aber nicht `coverage.json`. Im Workspace liegt `coverage.json` untracked und stale vom 2026-04-23.

Empfohlene Umsetzung:

- `coverage.json` in `.gitignore` aufnehmen, wenn es nur lokales Artefakt ist.
- Alternativ bewusst als generiertes Audit-Artefakt versionieren und im CI reproduzieren.

###: Ruff-Version in CI nicht reproduzierbar

`.github/workflows/lint.yml:19` installiert `ruff` ohne Version, während `requirements-dev.txt:133` `ruff==0.15.11` pinnt. Dadurch kann CI durch neue Ruff-Releases anders bewerten als lokale/dev-Umgebungen.

Empfohlene Umsetzung:

- Lint-Workflow sollte `pip install -r requirements-dev.txt` oder explizit `ruff==0.15.11` verwenden.
- Danach lokale `.venv` angleichen; aktuell meldet die lokale Umgebung `ruff 0.15.7`.

###: E2E-Hard-Waits

Es gibt noch 7 echte `page.wait_for_timeout`-Aufrufe:

- `src/tests/e2e/test_pwa_offline.py:183`
- `src/tests/e2e/test_quick_capture.py:66`
- `src/tests/e2e/test_quick_capture.py:177`
- `src/tests/e2e/test_quick_capture.py:202`
- `src/tests/e2e/test_quick_capture.py:258`
- `src/tests/e2e/test_quick_capture.py:326`
- `src/tests/e2e/test_quick_capture.py:393`

Das ist keine akute Produktfunktion, aber ein Stabilitätsproblem für CI. Besonders die 6 bis 7 Sekunden langen Waits machen die Tests langsam und trotzdem flaky.

Empfohlene Umsetzung:

- Auf `expect(locator).to_be_visible(...)`, `expect(...).to_have_text(...)`, `page.wait_for_response(...)` oder IndexedDB-spezifische Polls umstellen.
- Die Offline-IDB-Stelle braucht vermutlich einen expliziten Test-Hook oder ein Custom-Polling auf Queue-Count statt pauschaler 500 ms.

## Dead Code und Reachability

Keine harte Löschliste gefunden, die ohne fachliche Rückfrage sicher entfernt werden sollte.

Begründung:

- Die neuen Mixins sind nicht tot, sondern partiell eingeführt.
- `AuditLog.objects.create` ist breit verteilt, aber nicht automatisch dead code; Services dürfen AuditLogs direkt schreiben.
- `coverage.json` ist kein Code, aber ein stale Artefakt.
- Die Attachment-Legacy-Formate `__file__` und neues `__files__` sind bewusst rückwärtskompatibel; Legacy-Pfad nicht ohne Datenmigration löschen.

Konkrete Dead-Code-nahe Aufräumkandidaten:

- Stale `coverage.json` entfernen oder ignorieren.
- Nach vollständiger Migration auf `__files__` eine Datenmigration planen und danach Legacy-Branch `__file__` entfernen.
- Wenn Mixins nicht breit migriert werden sollen, Kommentare/Architekturhinweise schärfen, damit sie nicht als "vergessener Refactor" wirken.

## Missing Code und Missing Tests

Fehlender Code:

- ACK/NACK-Protokoll zwischen Service Worker und Page für Offline-Queue.
- Rollback-sicherer File-Cleanup für verschlüsselte Uploads.
- CSP-konformer Attachment-Remove-Handler in externer JS-Datei.
- MIME-Äquivalenzlogik für erlaubte Containerformate.

Fehlende Tests:

- Architekturtest gegen Inline-Event-Attribute in Templates.
- Browser-/E2E-Test für Attachment entfernen im Event-Edit.
- Offline-Queue-Test für fehlenden Session-Key und fehlendes `offlineQueue`.
- File-Vault-Test für Orphan-Cleanup nach Rollback.
- File-Vault-Test für valide DOCX/OOXML.
- Query-Count-Test für Event-Detail mit mehreren Attachments und Versionsketten.

## Bereits Verbessert Seit Dem Vorherigen Audit

- Ruff ist auf dem aktuellen Workspace grün.
- Django-Systemchecks und Migration-Check sind grün.
- Seed-/Retention-Hotspots wurden weiter refaktoriert.
- Docker-Healthcheck ist vorhanden.
- E2E-CI nutzt npm/Playwright-Caches.
- Security-Headers wurden gehärtet.
- AuditLog-Lücken für Case Close/Reopen und Milestone Delete wurden geschlossen.
- Magic Numbers wurden teilweise in `core.constants` konsolidiert.

## Empfohlener Umsetzungsplan

1. P1 zuerst: Inline-`onchange` entfernen, externe JS-Implementierung bauen, Architekturtest ergänzen.
2. P1 danach: Offline-Queue nur bei bestätigter Persistierung als Erfolg melden.
3. P2: File-Vault rollback-sicher machen und MIME-Äquivalenz für DOCX/OOXML ergänzen.
4. P2: Event-Attachment-Update und Detail-Anzeige aus der View-Logik lösen, Query-Count absichern.
5. P3: Repo-/CI-Hygiene nachziehen: `coverage.json`, Ruff-Pin, i18n-f-Strings, E2E-Hard-Waits.


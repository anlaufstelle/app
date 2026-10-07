# Barrierefreiheits-Audit — Anlaufstelle (WCAG 2.1 Stufe AA)

**Stand 2026-09-06** · Nachweis-Dokument zu #1059 (Stufe 2 + 3).
Geprüfter Stand des Hauptzweigs: `378a614b` (Stand main 2026-09-06). Werkzeuge: axe-core 4.13.0 (Regelumfang siehe § 1.2), Gate `make test-a11y` / `src/tests/e2e/test_accessibility.py`, Bestandsschutz-Baseline `src/tests/e2e/a11y_baseline.json`.

Dieses Dokument ist der Nachweis der Barrierefreiheit. § 1 (Scope/Methodik), § 2 (automatisierte Befunde und Korrekturen) und § 4 (bewusste Ausnahmen) sind belegt; § 3 ist ein Prüfprotokoll für den Screenreader-Handtest, dessen Ergebnis offen ist und nachgetragen wird.

---

## 1 Scope und Methodik

### 1.1 Prüfgegenstand

Geprüft wurde die Weboberfläche der Anlaufstelle im Zustand des Hauptzweigs vom 2026-09-06 — alle Rollen (Assistenz, Fachkraft, Anwendungsbetreuung, Betreiber-Administration) und alle Endgeräte-Breiten, wie sie das gemeinsame Layout (Sidebar für große Bildschirme, Bottom-Navigation für kleine) bedient.
Prüfmaßstab ist **WCAG 2.1 Stufe AA**. Die Prüfung fand in zwei Schritten statt:

1. **Automatisierter Gate-Lauf** mit axe-core in der bestehenden End-to-End-Test-Umgebung (Chromium, deutsche Oberfläche, angemeldete Testkonten).
2. **Manuelles Quelltext-Audit aller Vorlagen** in acht thematischen Paketen, jeder Befund von einer zweiten Instanz am Code gegengeprüft.

Der dritte Schritt — die Prüfung mit echten Screenreadern (NVDA unter Windows, VoiceOver unter macOS/iOS) — steht aus; § 3 hält den Rahmen dafür bereit.

### 1.2 Automatisierter Lauf: Werkzeug und Regelumfang

| | |
|---|---|
| Werkzeug | axe-core 4.13.0 (Deque Systems, MPL-2.0), im Projekt mitgeliefert unter `src/static/js/vendor/axe.min.js` |
| Ausführung | Playwright/Chromium, Einbindung same-origin über die statische Auslieferung der Anwendung |
| Regelumfang (`runOnly`, Tags) | `wcag2a`, `wcag2aa`, `wcag21a`, `wcag21aa` |
| Erfasste Schweregrade | alle (`minor` bis `critical`); die berichteten Verstöße sind ausschließlich `critical` und `serious` |
| Testmarker / Aufruf | `pytest -m "e2e and a11y"` bzw. `make test-a11y` |
| Bestandsschutz-Baseline | `src/tests/e2e/a11y_baseline.json` (`{page_key: {regel: knoten}}`) |

Der Lauf ist als **Gate** angelegt: neue Verstöße lassen den Test scheitern; die bekannten Verstöße sind über die Baseline dokumentiert und werden mit den Korrekturen aus § 2 abgebaut. Zusätzlich prüft derselbe Testlauf auf drei Seiten den **Skip-Link** funktional (Tabulator → Skip-Link fokussiert, Eingabetaste → Fokus im Hauptinhalt).

### 1.3 Seiten-Matrix des automatisierten Laufs

Gescannt wurden 25 Seiten-Zustände (inklusive zweier Sonderzustände des Kontaktformulars und dreier Rollen-Startseiten):

| Nr. | Seiten-Zustand | Pfad | Rolle/Zustand |
|---|---|---|---|
| 1 | `login` | `/login/` | anonym |
| 2 | `password_reset` | `/password-reset/` | anonym |
| 3 | `start_staff` | `/ (Startseite Fachkraft)` | Fachkraft |
| 4 | `zeitstrom_assistant` | `/ (Startseite Assistenz)` | Assistenz |
| 5 | `start_facility_admin` | `/verwaltung/` | Anwendungsbetreuung |
| 6 | `client_list` | `/clients/` | angemeldet |
| 7 | `client_detail` | `/clients/<id>/` | angemeldet |
| 8 | `client_create` | `/clients/new/` | angemeldet |
| 9 | `case_list` | `/cases/` | angemeldet |
| 10 | `case_detail` | `/cases/<id>/` | angemeldet |
| 11 | `event_create` | `/events/new/` | angemeldet |
| 12 | `event_create_dynamic_fields` | `/events/new/ nach Dokumenttyp-Wahl` | angemeldet |
| 13 | `event_create_errors` | `/events/new/ mit Pflichtfeld-Fehler` | angemeldet |
| 14 | `event_detail` | `/events/<id>/` | angemeldet |
| 15 | `workitem_inbox` | `/workitems/` | angemeldet |
| 16 | `attachment_list` | `/attachments/` | angemeldet |
| 17 | `statistics` | `/statistics/` | angemeldet |
| 18 | `audit_log` | `/audit/` | angemeldet |
| 19 | `retention` | `/retention/` | angemeldet |
| 20 | `search` | `/search/?q=…` | angemeldet |
| 21 | `account` | `/account/` | angemeldet |
| 22 | `dsgvo` | `/dsgvo/` | angemeldet |
| 23 | `offline_conflicts` | `/offline/conflicts/` | angemeldet |
| 24 | `system_dashboard` | `/system/` | Betreiber-Administration |
| 25 | `system_compliance` | `/system/compliance/` | Betreiber-Administration |

Ohne einen einzigen automatisiert erkennbaren Verstoß blieben `login`, `password_reset` und `offline_conflicts`. Der `zeitstrom`-Schlüssel fehlte im ersten artefaktbasierten Baseline-Lauf (Ready-Selektor); er ist über `start_staff` und `zeitstrom_assistant` doppelt abgedeckt und wurde im zweiten Lauf bereinigt. Der finale axe-Lauf (axe-Endstand) scannt **26 Seiten-Zustände** (die Matrix aus 25 plus den nachgeführten `zeitstrom`).

### 1.4 Manuelles Quelltext-Audit: Vollständigkeit

Alle Oberflächen-Vorlagen des Produkts wurden in acht Paketen vollständig gelesen (kein Stichprobenverfahren):

| Paket | Inhalt |
|---|---|
| Layout & gemeinsame Bausteine | Grundgerüst, Navigation (Sidebar/mobil), Suchfeld, Blätterleiste, Formularfeld-Makros, Aktionsmenüs, Zeitfilter |
| Anmeldung & Zugänge | Anmeldung, Passwort-Zurücksetzen, Zwei-Faktor-Einrichtung/-Anmeldung/-Backup-Codes, Sperr-Wiederherstellung, Zugriffsverwaltung |
| Personen & Fälle | Listen, Detailseiten, Formulare, Zusammenführen, Papierkorb, Ziele, Offline-Ansichten |
| Ereignisse & Zeitstrom | Kontakt dokumentieren, Detailansicht, Konfliktprüfung, Übergabe, Zeitstrom-Startseite, Offline-Erfassung |
| Aufgaben, Dateien & Import | Aufgaben-Eingang und -Formular, Dateiliste, Import-Assistent |
| Konfiguration & Felder | Dokumentationstypen, Feldvorlagen, Bedingungs-Editor, Einstellungen |
| Statistik, Audit, Suche, Startseiten | Auswertungen, Audit-Log, Suchergebnisse, Rollen-Startseiten, Profil |
| Aufbewahrung, System & Rest | Löschfristen, DSGVO-Paket, System- und Compliance-Übersicht, Wartung, Löschanträge |

Farbkontraste wurden dort, wo axe nichts melden konnte (nicht gescannte Seiten, Nicht-Text-Kontraste), aus den Design-Tokens der Stilvorlage nach der WCAG-Leuchtdichteformel **nachgerechnet**; die Ausgangswerte stehen in § 2.3.

### 1.5 Ausschlüsse

| Ausgeschlossen | Begründung |
|---|---|
| Betreiber-Administration unter `/admin-mgmt/` | Standard-Administrationsoberfläche des eingesetzten Web-Rahmenwerks (Fremdcode), nicht Teil der Fachanwendung; nur für technische Betreiberrollen erreichbar. Weder gescannt noch im Quelltext geprüft. |
| PDF-Vorlagen (Berichte, DSGVO-Auskunft, Übergabe-Ausdruck) | Anderes Zielformat; PDF-Barrierefreiheit (PDF/UA) ist eine eigenständige Prüfung mit eigenen Werkzeugen und wird gesondert geplant. |
| Reine E-Mail-Vorlagen (Einladung, Passwort-Zurücksetzen) | Enthalten keine HTML-Struktur, sondern reinen Text; die WCAG-Kriterien für Webseiten greifen nicht. Gelesen und bestätigt. |
| Native mobile Anwendung | Eigener Prüfgegenstand (WCAG über native Plattform-Kriterien), nicht Teil dieses Audits. |

---

## 2 Automatisierte Befunde und Korrekturen

### 2.1 Ergebnis in Zahlen — vorher und nachher

**Vorher (axe-Erstlauf 2026-09-05 und Vorlagen-Audit 2026-09-06):**

| | Anzahl |
|---|---|
| Automatisiert gemeldete Verstoß-Einträge (Regel × Seite), axe-Erstlauf 2026-09-05 (Gate-Einführung), Rohmessung vor dem Pinnen — `zeitstrom` scheiterte in diesem Lauf am Ready-Selektor und fehlt hier noch | 34, mit 70 betroffenen Elementen auf 22 von 25 gescannten Seiten |
| Befunde insgesamt nach Vorlagen-Audit und Zusammenführung | **47** (51 gemeldet, 4 Cross-Batch-Dubletten zusammengeführt) |
| davon schwerwiegend (`critical`) | 7 |
| davon erheblich (`serious`) | 22 |
| davon mittel (`moderate`) | 15 |
| davon gering (`minor`) | 3 |
| Fix-Typ: mechanisch / Semantik+JS | 39 mechanisch, 8 semantik_js |

Die automatisierte Prüfung fand **11** dieser Befunde; die übrigen **36** stammen aus dem Quelltext-Audit — überwiegend Zustände, die nur bei Interaktion entstehen (Aufklapp-Menüs, Formularfehler, Live-Aktualisierungen), sowie Kontraste auf Seiten, die der automatisierte Lauf nicht abdeckt.

**axe-Baseline (Gate, axe-Erstlauf 2026-09-05 (Gate-Einführung), gepinnt):** 23 Seiten · 38 Regel-Einträge · **75 Knoten**. Gegenüber der Rohmessung oben (34 Einträge/70 Knoten/22 Seiten) kam nach Korrektur des zunächst fehlgeschlagenen `zeitstrom`-Laufs ein abgeleiteter, nicht selbst gemessener Eintrag hinzu (+4 Regel-Einträge/+5 Knoten: `aria-allowed-attr`, `color-contrast`, `link-name`×2, `select-name`) — die beiden Zahlenpaare beschreiben also denselben automatisierten Lauf vor und nach dieser Ergänzung, keinen Widerspruch. Drei gescannte Seiten sind bereits sauber und stehen bewusst nicht in der Baseline: `login`, `password_reset`, `offline_conflicts`.

**Nachher (axe-Endstand):** **0 Verstoß-Knoten.** Die Baseline ist von 38 Regel-Einträgen/75 Knoten auf **0** abgebaut; `src/tests/e2e/a11y_baseline.json` ist leer (`{}`). Der zuletzt verbliebene Eintrag (`event_create_errors`, Regel `listitem`) stammte aus der Fehlerausgabe „Dieses Feld ist zwingend erforderlich.“ auf dem Kontaktformular mit ausgelöstem Pflichtfeld-Fehler und ist in § 2.5 gelöst.

### 2.2 Befundliste

Reihenfolge nach Schwere. Status: „behoben" (Fix-Welle B2/B2' — Commit-Bereich auf dem Hauptzweig main siehe § 2.4), „offen" (bewusst, mit Grund) oder „Handtest § 3" (nur mit Screenreader entscheidbar). Die Korrekturwellen B2/B2' sind in § 2.4 beschrieben.

| ID | Erfolgskriterium | Schwere | Welle | Status / Fix |
|---|---|---|---|---|
| A11Y-001 | 4.1.2 Name, Rolle, Wert | critical | B2 | behoben — `aria-label` am Zuordnungs-Select |
| A11Y-002 | 1.3.1 / 3.3.2 / 4.1.2 | critical | B2 | behoben — `for`/`id` an den Datumsfeldern der Zeitraumleiste |
| A11Y-003 | 4.1.2 Name, Rolle, Wert | critical | B2 | behoben — `aria-label` am Dokumentationstyp-Filter |
| A11Y-004 | 4.1.2 Name, Rolle, Wert | critical | B2 | behoben — `role="combobox" aria-autocomplete="list" aria-haspopup="listbox"` an der globalen Suche |
| A11Y-005 | 1.3.1 / 3.3.2 / 4.1.2 | critical | B2 | behoben — `for`/`id` im Bedingungs-Editor |
| A11Y-006 | 4.1.2 Name, Rolle, Wert | critical | B2 | behoben — `aria-label` am Versions-Select der Konfliktprüfung |
| A11Y-007 | 1.3.1 / 4.1.2 | critical | B2 | behoben — drei `for`/`id`-Paare der Offline-Kontakterfassung |
| A11Y-008 | 1.4.3 Kontrast Text | serious | B2' | behoben — Aktive Uhrzeitspanne erhält `text-accent` (6,07:1 statt 4,31:1) |
| A11Y-009 | 1.4.3 Kontrast Text | serious | B2 | behoben — `opacity-70` entfernt (volle Deckkraft = 7,13:1) |
| A11Y-010 | 1.4.3 Kontrast Text | serious | B2 | behoben — `green-600`→`green-700` (3,22:1→4,94:1) |
| A11Y-011 | 1.4.3 Kontrast Text | serious | B2 | behoben — `yellow-600`→`yellow-800` (2,93:1→6,88:1) |
| A11Y-012 | 1.1.1 / 4.1.2 | serious | B2 | behoben — Statuspunkte erhalten `role="img"` |
| A11Y-013 | 2.4.4 Linkzweck | serious | B2 | behoben — Datums-Pfeile erhalten `aria-label` |
| A11Y-014 | 1.4.11 Kontrast Nicht-Text | serious | B2 | behoben — Rahmen-Token `--border` abgedunkelt (1,44:1→3,37:1 auf Karte, 1,30:1→3,04:1 auf Seite) |
| A11Y-015 | 4.1.2 Name, Rolle, Wert | serious | B2' | behoben — `aria-expanded/aria-haspopup/aria-controls` am „Weitere Aktionen“-Trigger |
| A11Y-016 | 2.4.4 Linkzweck (im Kontext) | serious | B2 | behoben — Pagination-Glyphen erhalten `aria-label` |
| A11Y-017 | 4.1.2 Name, Rolle, Wert | serious | B2' | behoben — Zeitfilter als `aria-pressed`-Gruppe (kein halbes Tab-Pattern); Auswahlzustand programmatisch bestimmbar |
| A11Y-018 | 3.3.2 / 1.3.1 | serious | B2 | behoben — Platzhalter-only-Felder in „Ziele“ erhalten `aria-label` |
| A11Y-019 | 1.3.1 / 4.1.2 | serious | B2 | behoben — dynamische Labels per `:for`/`:id` mit `offline-create-field-<slug>` |
| A11Y-020 | 1.3.1 / 4.1.2 | serious | B2 | behoben — acht Offline-WorkItem-Feldpaare per `id_prefix` |
| A11Y-021 | 1.3.1 / 4.1.2 | serious | B2 | behoben — Inline-Edit-Labels per `offline-edit-field-<slug>` |
| A11Y-022 | 1.4.3 Kontrast Text | serious | B2 | behoben — `amber-600`→`amber-700` (3,19:1→5,05:1) |
| A11Y-023 | 4.1.3 Statusmeldungen | serious | B2 | behoben — Offline-Speicherfehler als `role="alert"`, Stufenhinweis als Status |
| A11Y-024 | 1.4.3 Kontrast Text | serious | B2 | behoben — `opacity-80` in der Übergabe-Zusammenfassung entfernt (6,07:1) |
| A11Y-025 | 1.4.3 Kontrast Text | serious | B2 | behoben — `orange-600`→`orange-700` (3,59:1→5,23:1) |
| A11Y-026 | 4.1.2 / 1.3.1 | serious | B2' | behoben — Aufgaben-Formular nutzt das gemeinsame Personen-Autocomplete (#1989); Tastaturpfad bleibt Handtest M-10 |
| A11Y-027 | 1.3.1 / 4.1.2 | serious | B2 | behoben — Person-Select der Offline-Aufgabe mit Label verbunden |
| A11Y-028 | 4.1.2 Name, Rolle, Wert | serious | B2' | behoben — Sidebar-„Neu erstellen“ mit Zustand + Fokus-Rückgabe |
| A11Y-029 | 4.1.2 Name, Rolle, Wert | serious | B2' | behoben — mobiles „Mehr“/Sucheinblendung mit Zustand + Fokus-Rückgabe |
| A11Y-030 | 4.1.2 Name, Rolle, Wert | moderate | B2 | behoben — Compliance-Statuspunkte erhalten `role="img"` |
| A11Y-031 | 3.3.1 / 4.1.2 | moderate | B2 | behoben — Auth-Felder über `_form_field` mit `aria-invalid`/`aria-describedby`; Fokus aufs erste invalide Feld (Handtest M-06) |
| A11Y-032 | 4.1.2 Name, Rolle, Wert | moderate | B2 | behoben — MFA-Toggle mit `aria-expanded`/`aria-controls` |
| A11Y-033 | 3.3.2 / 1.3.1 | moderate | B2 | behoben — Personen-ID-Filter erhält `aria-label` |
| A11Y-034 | 3.3.1 / 1.3.1 | moderate | B2 | behoben — Episoden-Formular über den geteilten Formular-Renderer |
| A11Y-035 | 4.1.3 Statusmeldungen | moderate | B2' | behoben — Fallliste meldet Trefferzahl per Live-Region (Handtest M-04) |
| A11Y-036 | 4.1.3 Statusmeldungen | moderate | B2' | behoben — Personenliste meldet Trefferzahl per Live-Region (Handtest M-04) |
| A11Y-037 | 1.3.1 Info und Beziehungen | moderate | B2 | behoben — Vergleichstabelle mit `scope="row"/"col"` |
| A11Y-038 | 1.3.1 Info und Beziehungen | moderate | B2 | behoben — Bestandszahlen-Matrix mit `scope="row"/"col"` |
| A11Y-039 | 4.1.3 Statusmeldungen | moderate | B2 | behoben — fünf Offline-Sync-Meldungen als `role="status"` (Handtest M-05) |
| A11Y-040 | 4.1.2 Name, Rolle, Wert | moderate | B2' | behoben — Verlauf-Details-Toggle mit `aria-expanded`/`aria-controls` |
| A11Y-041 | 4.1.2 Name, Rolle, Wert | moderate | B2 | behoben — aktiver Zeitraum-Pill mit `aria-current="true"` |
| A11Y-042 | 1.3.1 Überschriftenhierarchie | moderate | B2 | behoben — drei Diagramm-Überschriften auf `h2` angehoben |
| A11Y-043 | 4.1.2 / 1.4.1 | moderate | B2 | behoben — Zukunfts-Links mit `aria-disabled="true" tabindex="-1"` |
| A11Y-044 | 4.1.2 Name, Rolle, Wert | moderate | B2' | behoben — Sprachumschalter mit `aria-pressed` |
| A11Y-045 | 4.1.2 (ARIA-Fehlgebrauch) | minor | B2 | behoben — `role="alert"` vom statischen Hinweiskasten entfernt |
| A11Y-046 | 1.1.1 Nicht-Text-Inhalt | minor | B2 | behoben — Favoriten-Stern mit sr-only-Text „Als Favorit markiert“ |
| A11Y-047 | 2.4.4 Linkzweck (im Kontext) | minor | B2 | behoben — „Verwalten“-Link mit Personennamen (Handtest M-12) |

**Verteilung über die Erfolgskriterien** (Mehrfachnennung möglich, ein Befund kann zwei Kriterien verletzen):

| Kriterium | Titel | Befunde |
|---|---|---|
| 1.1.1 | Nicht-Text-Inhalt | 2 |
| 1.3.1 | Info und Beziehungen | 14 |
| 1.4.1 | Benutzung von Farbe | 1 |
| 1.4.3 | Kontrast (Minimum) | 7 |
| 1.4.11 | Kontrast von Nicht-Text-Inhalten | 1 |
| 2.4.4 | Linkzweck (im Kontext) | 3 |
| 3.3.1 | Fehlererkennung | 2 |
| 3.3.2 | Beschriftungen oder Anweisungen | 4 |
| 4.1.2 | Name, Rolle, Wert | 25 |
| 4.1.3 | Statusmeldungen | 4 |

### 2.3 Kontraste — vorher/nachher

Die „Vorher“-Werte stammen aus den Design-Tokens der Stilvorlage bzw. der Farbpalette des CSS-Rahmenwerks nach der WCAG-Leuchtdichteformel (Vorlagen-Audit 2026-09-06); die „Nachher“-Werte aus den rechnerischen Nachweisen der Fix-Wellen mechanisch (B2) und Semantik/JS (B2'), siehe § 2.4. „Soll“ richtet sich nach Textgröße und -stärke (4,5:1 für Fließtext, 3:1 für großen Text ab 18,66 px fett bzw. 24 px, 3:1 für Bedienelement-Begrenzungen).

| ID | Element | Ist (vorher) | Nachher | Bewertung |
|---|---|---|---|---|
| A11Y-011 | Schaltfläche „Hold aufheben“, weißer Text | `yellow-600`: **2,93:1** | `yellow-800`: **6,875:1** | bestanden |
| A11Y-022 | Schaltfläche „Antrag zurückziehen“ | `amber-600`: **3,19:1** | `amber-700`: **5,051:1** | bestanden |
| A11Y-010 | Schaltfläche „Freigeben“ | `green-600`: **3,22:1** | `green-700`: **4,943:1** | bestanden |
| A11Y-025 | Schaltfläche „Wartungsmodus aktivieren“ | `orange-600`: **3,59:1** | `orange-700`: **5,229:1** | bestanden |
| A11Y-009 | Dokumentationstyp-Schnellvorlagen | `accent` bei 70 %: 3,63:1 | volles `accent`: **7,128:1** (Karte) / **6,436:1** (Seite) | bestanden |
| A11Y-024 | Schichtzeitraum in der Übergabe | `accent` bei 80 %: 4,01:1 | volles `accent`: **6,070:1** | bestanden |
| A11Y-008 | Uhrzeitspanne im aktiven Zeitfilter | `text-ink-muted`: 4,31:1 | `text-accent`: **6,07:1** | bestanden |
| A11Y-014 | Rahmen `--border` (Karte `#fff`) | 1,44:1 | **3,367:1** | bestanden (Nicht-Text 3:1) |
| A11Y-014 | Rahmen `--border` (Seite `--bg`) | 1,30:1 | **3,040:1** | bestanden (Nicht-Text 3:1) |
| — (Nachtrag) | Feed-Karten-Labels | `text-ink-muted` bei 70 %: 2,82:1 | volles `text-ink-muted`: **5,066:1** (Karte) / **4,574:1** (Seite) | bestanden |
| — (Nachtrag) | Übergabe-Zeitstempel | `text-ink-muted`: 4,314:1 | `text-ink`: **16,021:1** | bestanden |

Die beiden mit „— (Nachtrag)" markierten Zeilen sind keine der 47 Befunde aus § 2.2: Es sind datenabhängige Kontrastknoten (Feed-Karten-Labels, Übergabe-Zeitstempel), die erst während der Fix-Wellen am gerenderten Feed bzw. an der Übergabe-Zusammenfassung auffielen und deshalb keine eigene A11Y-ID tragen; behoben als Teil der Fix-Welle mechanisch (B2, siehe § 2.4).

Zusätzlich bestätige (bewusst akzeptierte) Ausgangswerte ohne Korrekturbedarf: gedämpfter Fließtext 5,07:1 (Kartenweiß) bzw. 4,57:1 (Seitenhintergrund), Akzentgrün auf Weiß 7,13:1, Orange auf Weiß bei ≥ 20 px fett 3,56:1 (große Schrift, Schwelle 3:1), Statusabzeichen 4,57–8,75:1 — Einzelheiten in § 4 (A-09, A-10).

### 2.4 Korrektur-Wellen

Zählweise: je Welle alle Commits auf dem Hauptzweig (main), einschließlich der jeweiligen ersten Korrekturrunde und der unmittelbar dadurch nötigen Folge-Commits (Integrationsnachträge) — belegt per `git log --oneline`.

| Welle | Inhalt | Befunde | Commits auf main (Basis + erste Korrekturrunde + Integrationsnachträge) |
|---|---|---|---|
| B2 — Fix-Welle mechanisch | Rein additive Auszeichnungen: Beschriftungen, `aria-label`, `for`/`id`, Tabellen-Zeilenköpfe, Farbklassen, der Rahmen-Token | 37 | 14 — `af01a9f3`…`40de9876` (7 Basis-Commits) + `073b7693` (Kontrast-Nachtrag Übergabe-Zeitstempel) + `ee497379`…`fe945d77` (4 Commits der ersten Korrekturrunde) + `47128fe2`, `68400340` (2 Integrationsnachträge: PWA-Shell-Cache-Version, E2E-Selektoren) |
| B2' — Fix-Welle Semantik/JS | Zustandsbehaftete Bedienmuster (Aufklapp-/Auswahlzustände, Kombinationsfeld), Live-Bereiche nach Teilaktualisierung, Fokus-Manager, Alpine-/JS-Anbindung | 10 | 12 — `b1a8c70f`…`2d768a15` (5 Basis-Commits) + `f94f2822`…`58e0d9ee` (4 Commits der ersten Korrekturrunde) + `b6081408`, `b514fbfa`, `c4873161` (3 Integrationsnachträge: E2E-Selektoren, Kommentar-Angleichung, Eingrenzung eines Rendering-Tests nach dem Zusammenführen mit der mechanischen Welle) |

### 2.5 Gelöst in #2154: `role="alert"` überschrieb die Listenrolle

Auf dem Kontaktformular mit ausgelöstem Pflichtfeld-Fehler meldete die automatisierte Prüfung ein Listenelement außerhalb einer Liste (`listitem`, erheblich, ein Element, Text „Dieses Feld ist zwingend erforderlich.“). Sämtliche Fehlerausgaben der beteiligten Vorlagen verwendeten nachweislich einen Listen-Container — die Stelle ließ sich weder im Vorlagen-Audit noch im Rahmen der Korrektur-Wellen im Quelltext bestimmen, weil ein reines Markup-Grep sie nicht sieht: `role="alert"` direkt am `<ul id="id_occurred_at-error" ...>` überschrieb dessen implizite `list`-Rolle; die Kind-`<li>` behielten ihre implizite `listitem`-Rolle, standen dadurch aber ohne Listen-Elternteil im Accessibility-Tree — nur eine Live-DOM-Prüfung wie axe deckt das auf. Gelöst in #2154: Die Rolle `alert` sitzt jetzt auf einem umschließenden `<div>`, das `<ul>` bekommt seine Listenrolle über `role="list"` explizit zurück; `id`/`aria-describedby` bleiben unverändert auf dem `<ul>` gerichtet. Dieselbe Fehlerklasse (`<ul ... role="alert">`) fand sich repo-weit an über einem Dutzend weiteren Stellen (Formularfeld-Partial, dynamische Zusatzfelder, Personen-Autocomplete, Aufgaben-/Fall-Formular, mehrere Konfigurations-Partials, Passwort-Formulare) und wurde im selben Zug behoben.

---

## 3 Prüfprotokoll Screenreader (NVDA / VoiceOver) — Handtest, offen

> **Noch nicht durchgeführt.** Dieser Abschnitt ist das vorbereitete Prüfprotokoll für den Screenreader-Handtest (Stufe 3). Er wird nach dem Handtest mit Ergebnissen gefüllt; erst dann ist das Audit vollständig. Die Prüfschritte M-01…M-15 entsprechen den manuellen Prüfpunkten aus dem Vorlagen-Audit 2026-09-06, ergänzt um M-16…M-18 (Skip-Link-Gegenprobe, Zoom/Textabstand, vollständige Tastaturprobe); sie decken sich mit den Testfällen ENT-A11Y-01…12 in `docs/testing/manual-test-matrix-b.md`.

### 3.1 Prüfumgebung (auszufüllen)

| | |
|---|---|
| NVDA-Version / Windows / Browser | _(auszufüllen)_ |
| VoiceOver / macOS / Safari | _(auszufüllen)_ |
| VoiceOver / iOS / Safari | _(auszufüllen)_ |
| Prüfdatum, prüfende Person | _(auszufüllen)_ |
| Stand der Anwendung (Version) | _(auszufüllen — nach Abschluss der Korrektur-Wellen)_ |

### 3.2 Prüfschritte

Jede Zeile wird mit **bestanden / nicht bestanden / nicht prüfbar** und einer Bemerkung abgeschlossen; zusätzlich werden Screenreader-Version und Browser notiert. Die Spalte „Bezug“ verweist auf die Befunde aus § 2, deren Korrektur hier gegengeprüft wird. Die Prüfpunkte M-01 bis M-15 stammen aus dem Vorlagen-Audit (manuell zu prüfende Befunde); M-16 bis M-18 sind Ergänzungen dieses Protokolls für Kriterien, die kein Werkzeug abdeckt (Skip-Link-Gegenhören, Vergrößerung/Textabstände, reine Tastaturbedienung).

| Nr. | Seite / Bedienelement | Prüfschritt | Erwartung | Bezug | Ergebnis |
|---|---|---|---|---|---|
| M-01 | Globale Kopfzeilen-Suche (jede Seite) | Fokus ins Suchfeld, 3 Zeichen tippen, mit Pfeil-ab in die Ergebnisse | Rolle „Kombinationsfeld“, Zustand „erweitert“ und Trefferanzahl werden angesagt; Pfeiltasten wandern durch die Optionen | A11Y-004 | offen |
| M-02 | Zeitstrom-Startseite, Zeitfilter | Mit Tabulator in die Filterleiste, mit Pfeil links/rechts wechseln | Der aktive Filter wird als ausgewählt/gedrückt angesagt | A11Y-008, A11Y-017 | offen |
| M-03 | Menü „Neu erstellen“, mobiles Menü „Mehr“, Menü „Weitere Aktionen“ | Mit Eingabetaste öffnen, mit Escape schließen | Zustand „erweitert“/„reduziert“ wird angesagt; Escape schließt; Fokus kehrt auf die auslösende Schaltfläche zurück | A11Y-015, A11Y-028, A11Y-029 | offen |
| M-04 | Personen- und Fallliste, Live-Suche | Suchbegriff tippen, zwei Sekunden warten, Fokus nicht bewegen | Die neue Trefferzahl wird ohne Fokuswechsel angesagt — genau einmal | A11Y-035, A11Y-036 | offen |
| M-05 | Offline-Ansichten (Person, Ereignis, Aufgabe) | Abgleich auslösen, Fehlerfall provozieren | Abgleichstatus und Fehlermeldungen werden angesagt, sobald sie erscheinen | A11Y-023, A11Y-039 | offen |
| M-06 | Kontakt dokumentieren, Pflichtfeld leer absenden | Formular ohne Pflichtfeld absenden | Fokus landet beim fehlerhaften Feld; Fehlertext wird mit Feldnamen vorgelesen | A11Y-031, § 2.5 | offen |
| M-07 | System-/Compliance-Übersicht | Statusliste linear durchlesen | Jeder Eintrag nennt seinen Status (OK/Warnung/Kritisch/Unbekannt) | A11Y-012, A11Y-030 | offen |
| M-08 | Personen zusammenführen (Vergleichs-/Bestätigungsansicht) | Tabellennavigation durch die Matrix | Beim Zellenwechsel werden Zeilen- und Spaltenüberschrift angesagt | A11Y-037, A11Y-038 | offen |
| M-09 | Statistik-Übersicht | Überschriftennavigation, Zeitraum-Schalter durchgehen | Keine übersprungenen Überschriftenebenen; gewählter Zeitraum als „aktuell“ | A11Y-041, A11Y-042 | offen |
| M-10 | Aufgaben-Formular, Feld „Person“ | Namen tippen, mit Pfeil-ab/Enter einen Treffer wählen | Rolle, Zustand und hervorgehobener Eintrag werden angesagt; Enter übernimmt, Escape schließt | A11Y-026 | offen |
| M-11 | Zeitstrom, Datumsnavigation | Zu den Pfeilen taben, in die Zukunft blättern versuchen | Pfeile tragen verständliche Namen; gesperrter Vorwärtspfeil als nicht verfügbar oder nicht fokussierbar | A11Y-013, A11Y-043 | offen |
| M-12 | Listen mit Blätterleiste, Zugriffsverwaltung | Linkliste des Screenreaders öffnen | „Vorherige Seite“/„Nächste Seite“ statt Pfeilzeichen; „Verwalten“ dem Personennamen zuordenbar | A11Y-016, A11Y-047 | offen |
| M-13 | Sprachumschalter DE/EN | Umschalter fokussieren | Aktiver Sprachstand wird als gedrückt/aktuell angesagt | A11Y-044 | offen |
| M-14 | Alle Formularfelder — Sichtprüfung | Feldgrenzen bei 200 % Zoom und unter heller Umgebung beurteilen | Feldgrenzen sind ohne Mausfokus erkennbar | A11Y-014 | offen |
| M-15 | Backup-Codes | Seite frisch laden, Codes kopieren | Statischer Hinweiskasten wird nicht unterbrechend angesagt; Kopieren gibt wahrnehmbare Rückmeldung | A11Y-045 (+ UX-Punkt `copyCodes()`) | offen |
| M-16 | Skip-Link auf drei Seiten | Erste Tabulator-Betätigung nach dem Laden | Skip-Link ist sichtbar und springt in den Hauptinhalt | automatisiert abgedeckt; hier nur gegengehört | offen |
| M-17 | Vergrößerung auf 200 % (1.4.4) und Textabstände (1.4.12) | Zoom auf 200 %, Textabstände per Nutzerstil erhöhen | Kein Inhalts- oder Funktionsverlust, kein waagerechtes Scrollen im Fließtext | 1.4.4 / 1.4.12, noch nicht geprüft | offen |
| M-18 | Bedienung ausschließlich mit der Tastatur (2.1.1, 2.1.2, 2.4.3) | Eine vollständige Dokumentation ohne Maus durchführen | Alle Schritte erreichbar, keine Tastaturfallen, nachvollziehbare Fokusreihenfolge | Gesamtprobe | offen |

### 3.3 Gesamturteil

| | |
|---|---|
| Bestandene Prüfschritte | _(auszufüllen)_ |
| Nicht bestandene Prüfschritte | _(auszufüllen)_ |
| Daraus abgeleitete neue Befunde | _(auszufüllen)_ |
| Bewertung WCAG 2.1 AA | _(auszufüllen)_ |

> **Abnahme durch Maintainer:** Das Gesamturteil wird nach dem Screenreader-Handtest von einem Maintainer abgenommen; das Ergebnis wird hier nachgetragen.

---

## 4 Bewusste Ausnahmen

Die folgenden Punkte sind geprüft und **absichtlich nicht** als Verstoß gewertet. Sie stehen hier, damit die Entscheidung nachvollziehbar bleibt und in späteren Prüfungen nicht erneut aufgerollt werden muss.

| Nr. | Punkt | Entscheidung und Begründung |
|---|---|---|
| A-01 | Spaltenköpfe einfacher Tabellen ohne `scope="col"` | Bei Tabellen mit einer einzigen Kopfzeile und ohne verbundene Zellen ist die Zuordnung eindeutig; die einschlägige WCAG-Technik verlangt die Angabe dort nicht. Betrifft unter anderem DSGVO-Paket, Systemübersicht, rechtliche Sperren, Aussperrungen, Organisation, Aufbewahrung und Audit-Log. **Ausgenommen** sind die zweidimensionalen Vergleichsmatrizen beim Zusammenführen von Personen — dort ist die fehlende Zeilenüberschrift ein echter Mangel (A11Y-037, A11Y-038, behoben). |
| A-02 | Filter, die sich beim Ändern einer Auswahlliste selbst absenden | Es wird nur ein Teilbereich der Seite ausgetauscht; der Fokus bleibt stehen, die Seite wechselt nicht. Damit liegt keine Kontextänderung im Sinne von 3.2.2 vor. Durchgängiges Muster der Anwendung. |
| A-03 | Kein Fokus-Einschluss im Anlege-Dialog | Rolle und Beschriftung des Dialogs sind korrekt ausgezeichnet; die Tastaturbedienung bleibt über die sichtbare Abbrechen-Schaltfläche jederzeit möglich. Fokus-Einschluss ist eine Empfehlung der ARIA-Praxisleitfäden, kein Erfolgskriterium. **Als Verbesserung vorgemerkt.** |
| A-04 | Aktionsmenüs ohne Menü-Rollen | Die Aufklappbereiche enthalten gewöhnliche Verweise und Schaltflächen; das ARIA-Menü-Muster mit Pfeiltasten-Steuerung ist dafür nicht erforderlich und würde die Bedienung eher erschweren. Gefordert und behoben ist nur die fehlende Zustandsangabe (A11Y-015). |
| A-05 | Wiederkehrender Linktext „Öffnen“ in den Kachel-Übersichten | Der Linkzweck ist aus dem umgebenden Listeneintrag programmatisch bestimmbar; damit ist 2.4.4 (Stufe AA) erfüllt. „Aus dem Linktext allein verständlich“ wäre 2.4.9 und damit Stufe AAA — außerhalb des Prüfmaßstabs. **Als Verbesserung vorgemerkt.** |
| A-06 | Kopieren der Backup-Codes ohne Rückmeldung | Die fehlende Rückmeldung trifft alle Nutzenden gleichermaßen und ist keine behinderungsspezifische Barriere. Als Bedienbarkeits-Verbesserung weitergereicht und in Prüfschritt M-15 mitbeobachtet. |
| A-07 | Kein Skip-Link auf den Fehlerseiten „Einrichtung archiviert“/„Einrichtung fehlt“ | Beide Seiten enthalten genau ein Bedienelement (Abmelden); es gibt nichts zu überspringen. Kein Verstoß gegen 2.4.1. |
| A-08 | Pflichtfeld-Kennzeichnung mit Sternchen ohne Legende | Jedes Feld trägt eine eigene Beschriftung, die Pflichtangabe wird zusätzlich programmatisch übermittelt. Eine erläuternde Legende ist eine Empfehlung, kein AA-Kriterium. **Als Verbesserung vorgemerkt.** |
| A-09 | Gedämpfter Fließtext knapp über der Kontrastschwelle | 4,57:1 auf dem Seitenhintergrund und 5,07:1 auf Kartenweiß bestehen die Anforderung von 4,5:1. Der geringe Abstand ist bekannt: bei abweichender Schriftdarstellung bleibt ein Restrisiko. **Bewusst akzeptiert**, mit der Auflage, diese Farbe bei künftigen Anpassungen nicht weiter aufzuhellen. |
| A-10 | Orange auf Weiß in den Kachelzahlen | 3,56:1 unterschreitet zwar 4,5:1, die Zahlen sind aber durchgehend mindestens 20 px fett gesetzt und fallen damit unter die Schwelle für großen Text (3:1). Die einzige Ausnahme — eine 13-px-Schaltfläche im Wartungsmodus — ist als A11Y-025 erfasst und behoben. |
| A-11 | Farbliche Hervorhebung von Kennzahlen (etwa Konten ohne zweiten Faktor) | Die Information steht bereits als Zahl im Text; die Farbe verstärkt nur. Kein Verstoß gegen 1.4.1. |
| A-12 | Statusabzeichen mit Farbe | Alle Abzeichen kombinieren Farbe **und** Text; die Farbe ist nie alleiniger Bedeutungsträger. |
| A-13 | Fokusführung nach Teilaktualisierungen statt Ansage | Die Anwendung setzt den Fokus nach dem Austausch eines Teilbereichs in den neuen Inhalt; das ist ein anerkanntes, gleichwertiges Mittel zu 4.1.3 und wird in der Entwicklungsdokumentation als Konvention geführt. Nur dort, wo weder Fokusführung noch Ansage greift, lag ein Befund vor (A11Y-023, A11Y-035, A11Y-036, A11Y-039) — alle behoben. |
| A-14 | Vollständige Kombinationsfeld-Auszeichnung der globalen Suche | Die Korrektur A11Y-004 behebt den automatisiert erkannten Verstoß. Ob der Ergebnisbereich darüber hinaus als Auswahlliste (`role="listbox"`/`option`) ausgezeichnet werden sollte, wird **erst nach der Screenreader-Probe M-01 entschieden** — eine Umstellung ohne Hörprobe verschlechtert erfahrungsgemäß mehr, als sie verbessert. |

### 4.1 Als vorbildlich bestätigte Bausteine

Diese Bausteine wurden gegengelesen und sind ohne Beanstandung; sie dienen als Referenz für neue Oberflächen:

- das gemeinsame Formularfeld-Makro (Beschriftung, Fehlerverknüpfung, Pflichtfeld- und Fehlerzustand werden vollständig übermittelt),
- die Personen-Suchhilfe in den Fachformularen (vollständiges Kombinationsfeld-Muster mit Pfeiltasten-, Eingabe- und Escape-Bedienung),
- das Seitengerüst (Skip-Link, Bereichsauszeichnungen, zentraler Meldungsbereich),
- die Einstellungs-Formulare (Feldgruppen mit Gruppenbeschriftung),
- die aufklappbaren Bereiche der Übergabe und der dynamischen Felder (Zustandsangabe und Bereichszuordnung korrekt),
- der Optionen-Editor der Konfiguration (Beschriftung wird beim Duplizieren einer Zeile mitgeführt).

### 4.2 Noch nicht abschließend geprüfte Kriterien (Pipeline-Grenzen)

Für folgende Kriterien reicht weder die automatisierte Prüfung noch das Quelltext-Audit; sie sind Teil von Stufe 3 (§ 3):

- **1.4.4 Textgröße ändern** und **1.4.10 Reflow** — Verhalten bei 200 % Vergrößerung und schmalem Ansichtsfenster (M-17),
- **1.4.12 Textabstand** — Verhalten bei erhöhten Zeilen- und Wortabständen (M-17),
- **2.1.1/2.1.2 Tastaturbedienbarkeit** und **2.4.3 Fokus-Reihenfolge** in vollständigen Arbeitsabläufen (M-18),
- **2.4.7 Fokus sichtbar** in allen Zuständen (die Fokusringe sind im Quelltext korrekt angelegt, die Sichtbarkeit auf farbigen Flächen ist aber zu bestätigen),
- **3.2.3/3.2.4 Konsistenz** über alle Rollen-Startseiten hinweg.

**Grenzen der Pipeline (bewusst):** axe prüft kein Screenreader-Verhalten — die Beurteilung der Ansagen ist dem Handtest (§ 3) vorbehalten. Farbkontrast auf Bildern ist ausgenommen (kein Text-/Bedienelement-Kontrast); der CSP-Build der Anwendung arbeitet ohne Inline-Expressions, was für die Barrierefreiheit keine Einschränkung darstellt, aber die Auszeichnungsmuster (String-Getter statt Inline-Ausdrücke) bestimmt.

---

*Dieses Dokument entstand aus dem automatisierten Gate-Lauf (axe-Erstlauf 2026-09-05, Gate-Einführung), dem Vorlagen-Audit 2026-09-06, den Fix-Wellen mechanisch (B2) und Semantik/JS (B2') sowie dem axe-Endstand 2026-09-06 auf main. Änderungen an Befunden gehören in diese Quellen, nicht in die Fließtext-Fassung.*

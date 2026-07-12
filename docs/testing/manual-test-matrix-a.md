# Manuelle Test-Matrix — Sektion A

> Teil der [Manual-Test-Matrix](manual-test-matrix.md) (Refs #1071 Block B). Setup-Block, Status-Legende, TC-ID-Schema, Browser-/Mobile-Konventionen und die Anhänge stehen im [Hub](manual-test-matrix.md); die Gesamtübersicht in [`test-matrix-index.md`](test-matrix-index.md).

---

## SEKTION A — Anwender-Smoke

> **Zielgruppe:** Sozialarbeiter:innen im Pilotbetrieb. Sprache bewusst frei von Fachjargon — kein „RLS", „HTMX", „CSP". Stattdessen: „Daten anderer Einrichtung", „Live-Aktualisierung", „Sicherheitsregeln".
>
> **Gliederung:** Tagesablauf einer Fachkraft mit gesamten ~12 End-to-End-Workflows. Jeder Workflow ist mehrschrittig und hat **Pause-Punkte** zum Abhaken zwischendurch.
>
> Diese Sektion wird beim Pilotbetrieb (mit echten Anwender:innen) durchgespielt. Bei Auffälligkeiten werden Issues mit Label `pilot-feedback` angelegt.

### Vormittag (VORM)

#### SMK-A-VORM-01 — Tagesstart

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Tagesablauf | fachkraft | C/F/S | ⚪ | `test_workflow_complete.py`, `test_dashboard.py` |


**Vorbereitung:**
- Privates Browser-Fenster, kein aktiver Login.
- TOTP-Authenticator bereit (z.B. Handy mit App).

**Ablauf:**

1. ☐ `http://localhost:8844/` aufrufen → Login-Seite erscheint.
2. ☐ Anmelden mit `fachkraft` / `anlaufstelle2026`.
3. ☐ Zweite Sicherheitsstufe: 6-stelligen Code aus TOTP-App eingeben.
4. ☐ **Live-Aktualisierung:** Der „Zeitstrom" zeigt die jüngsten Aktivitäten der Einrichtung.
5. ☐ Im Hauptmenü auf **„Übergabe"** klicken — die Übergabe der Vorschicht lesen.
6. ☐ Wichtige offene Punkte mental oder auf Papier notieren.
7. ☐ Im Menü auf **„Aufgaben"** wechseln — die eigene Inbox öffnet sich.
8. ☐ Filter „Heute fällig" aktivieren, alle eigenen offenen Aufgaben sichten.
9. ☐ Ein Aufgaben-Element öffnen (z.B. ein offener Beratungstermin).

**Sicherheitsregeln im Hintergrund:**
- Anmeldungen werden protokolliert (Auditierbarkeit).
- Sitzung läuft automatisch nach 30 Minuten Inaktivität ab.

**Erwartung:**
- Du bist eingeloggt, hast die Übergabe gelesen und kennst die heutigen Aufgaben.

**Status:** ☐ Offen

---

#### SMK-A-VORM-02 — Erstkontakt anonym

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Tagesablauf | fachkraft | C/F/S | ✓ | `test_min_contact_stage_anonymous.py` |

**Voraussetzung:** SMK-A-VORM-01

**Vorbereitung:**
- Eingeloggt als `fachkraft`. Eine Person betritt die Anlaufstelle, möchte anonym bleiben.

**Ablauf:**

1. ☐ Im Menü „Klient:innen" → **„Neue:r Klient:in"** klicken.
2. ☐ Stage **„anonym"** wählen — Pseudonymsfeld bleibt leer/optional.
3. ☐ Bei „Alters-Cluster" zutreffende Spanne auswählen (z.B. 25–34).
4. ☐ Speichern → Klient:in wird mit zufälliger interner Kennung angelegt.
5. ☐ Sofort ein **„Event"** (Beratungsgespräch) anlegen.
6. ☐ Dokumenten-Typ **„Beratung"** wählen — die passenden Felder erscheinen.
7. ☐ Inhaltsfelder ausfüllen, Sensitivitätsstufe **„mittel"** belassen.
8. ☐ Speichern → Event erscheint sofort in der Klient:innen-Timeline.

**Sicherheitsregeln im Hintergrund:**
- Pseudonyme statt Klarnamen schützen die Anonymität (Datenminimierung).
- Sensitivitätsstufe steuert, wer das Event später im „Zeitstrom" sieht.

**Erwartung:**
- Anonyme:r Klient:in existiert mit einem dokumentierten Beratungsgespräch.

**Status:** ☐ Offen

---

#### SMK-A-VORM-03 — Klient:in identifizieren (Stage-Wechsel)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Tagesablauf | fachkraft | C/F | ⚪ | `test_contact_stage.py` |

**Voraussetzung:** SMK-A-VORM-02 (anonyme:r Klient:in existiert)

**Vorbereitung:**
- Klient:in aus VORM-02 sucht erneut die Anlaufstelle auf und gibt nun einen Namen an.

**Ablauf:**

1. ☐ Über die Suche oder Klient:innen-Liste die Person aus VORM-02 öffnen.
2. ☐ Auf **„Bearbeiten"** klicken.
3. ☐ Pseudonym eintragen (z.B. „Vornamen-Initial + Geburts-Jahr").
4. ☐ Stage von **„anonym"** auf **„identifiziert"** ändern.
5. ☐ Speichern.
6. ☐ Detail-View prüfen: Stage-Wechsel ist sichtbar.

**Sicherheitsregeln im Hintergrund:**
- Stage-Wechsel wird im Audit-Protokoll vermerkt (Wer hat wann was geändert).

**Erwartung:**
- Klient:in erscheint nun mit Pseudonym in der Liste, Stage „identifiziert".

**Status:** ☐ Offen

---

### Mittag (MITT)

#### SMK-A-MITT-01 — WorkItem-Triage

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Tagesablauf | fachkraft | C/F/S | ✓ | `test_workitem_ui.py` |

**Voraussetzung:** SMK-A-VORM-01

**Vorbereitung:**
- Inbox enthält mehrere offene Aufgaben (Seed liefert Beispiele).

**Ablauf:**

1. ☐ Aufgaben-Inbox öffnen.
2. ☐ Drei abgearbeitete Aufgaben anhaken (Status auf **„erledigt"**).
3. ☐ Dabei den **Live-Tausch** beobachten: Status ändert sich ohne Seiten-Reload.
4. ☐ Eine offene Aufgabe **markieren** (Checkbox links).
5. ☐ Eine zweite Aufgabe markieren.
6. ☐ Auf **„Bulk-Aktion: Zuweisen"** klicken → an Lead-Kollegin reassignen.
7. ☐ Bestätigen.
8. ☐ Ein hochpriorisiertes Element öffnen → Status auf **„in Arbeit"**.

**Sicherheitsregeln im Hintergrund:**
- Bulk-Aktionen funktionieren nur innerhalb der eigenen Einrichtung.

**Erwartung:**
- Inbox ist aufgeräumt, drei erledigt, zwei reassignt, eine in Bearbeitung.

**Status:** ☐ Offen

---

#### SMK-A-MITT-02 — Suche & Verlauf

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Tagesablauf | fachkraft | C/F/S | ✓ | `test_clients_search.py`, `test_fuzzy_search.py` |

**Voraussetzung:** SMK-A-VORM-01

**Vorbereitung:**
- Eine bekannte Klient:in aus dem Seed (z.B. Pseudonym mit dem Anfangsbuchstaben „M").

**Ablauf:**

1. ☐ In das Suchfeld oben rechts „M" tippen.
2. ☐ Live-Vorschläge erscheinen — auf einen passenden Treffer klicken.
3. ☐ Klient:innen-Detailansicht öffnet sich.
4. ☐ Timeline runterscrollen — alle Beratungs-Events sind chronologisch sichtbar.
5. ☐ Ein Event aus dem Vormonat anklicken → Detail öffnet sich.
6. ☐ Inhalte lesen, dann „Zurück zur Klient:in".
7. ☐ Über „Ähnliche Suche" einen Tippfehler ausprobieren („Mlle" statt „Mella") — System findet trotzdem den richtigen Treffer.

**Sicherheitsregeln im Hintergrund:**
- Suche filtert automatisch nur Klient:innen der eigenen Einrichtung.

**Erwartung:**
- Du hast den vollständigen Verlauf der Klient:in eingesehen, inkl. einem alten Event.

**Status:** ☐ Offen

---

### Nachmittag (NACH)

#### SMK-A-NACH-01 — Case-Episode abschließen

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Tagesablauf | leitung | C/F/S | ⚪ | `test_workflow_complete.py`, `test_cases.py` |

**Voraussetzung:** SMK-A-VORM-01 (mit `leitung`-Login statt `fachkraft`)

**Vorbereitung:**
- Existierender Fall (Case) mit aktiver Episode, mehreren Goals und Milestones.

**Ablauf:**

1. ☐ Im Menü „Fälle" → den entsprechenden Fall öffnen.
2. ☐ Aktiven Goals scrollen, ein Milestone als **„erreicht"** ankreuzen.
3. ☐ Ein zweites Milestone ebenfalls als erreicht markieren.
4. ☐ Goal als **„abgeschlossen"** markieren, sobald alle Milestones erledigt sind.
5. ☐ Auf **„Episode schließen"** klicken.
6. ☐ Outcome wählen (z.B. „erfolgreich", „abgebrochen") und kurze Notiz.
7. ☐ Speichern.
8. ☐ Falls Fall komplett abgeschlossen: **„Fall schließen"** mit Begründung.

**Sicherheitsregeln im Hintergrund:**
- Nur Lead-Rolle darf Fälle schließen.
- Schließvorgang wird auditiert.

**Erwartung:**
- Episode ist geschlossen, Goals abgehakt, ggf. Fall ebenfalls geschlossen.

**Status:** ☐ Offen

---

#### SMK-A-NACH-02 — Quick-Statistik & CSV-Export

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Tagesablauf | leitung | C/F/S || `test_export_statistics.py`, `test_statistics_dashboard.py` |

**Voraussetzung:** SMK-A-VORM-01 (mit `leitung`-Login)

**Vorbereitung:**
- Eingeloggt als `leitung`.

**Ablauf:**

1. ☐ Im Menü auf **„Statistik"** klicken.
2. ☐ Dashboard mit Diagrammen lädt (Anzahl Klient:innen, Events, Trends).
3. ☐ Filter auf **„Aktuelles Quartal"** setzen.
4. ☐ Diagramme aktualisieren sich (Live-Tausch).
5. ☐ Auf **„CSV-Export"** klicken → Datei wird heruntergeladen.
6. ☐ CSV in Tabellenkalkulation öffnen → Spalten korrekt, Werte plausibel.

**Sicherheitsregeln im Hintergrund:**
- CSV enthält nur Daten der eigenen Einrichtung.
- Export wird auditiert.

**Erwartung:**
- CSV liegt im Download-Ordner, Inhalt entspricht der Bildschirm-Statistik.

**Status:** ☐ Offen

---

### Abend (ABEND)

#### SMK-A-ABEND-01 — Datei-Upload und Logout

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Tagesablauf | fachkraft | C/F/S | ✓ | `test_attachment_versioning_stage_b.py`, `test_logout_cleanup.py` |

**Voraussetzung:** SMK-A-VORM-02 (Klient:in mit Event vorhanden)

**Vorbereitung:**
- Ein PDF (z.B. Beratungs-Notiz, max. 25 MB) und die EICAR-Datei (SETUP-07) bereithalten.

**Ablauf:**

1. ☐ Das Event aus VORM-02 öffnen.
2. ☐ Auf **„Anhang hinzufügen"** klicken → das harmlose PDF auswählen.
3. ☐ Upload-Status erscheint, kurz danach **„Virusprüfung erfolgreich"**.
4. ☐ Anhang ist in der Liste sichtbar, herunterladbar.
5. ☐ Erneut **„Anhang hinzufügen"** → diesmal die EICAR-Test-Datei.
6. ☐ Upload wird **abgewiesen** mit Hinweis „Datei enthält Schadcode".
7. ☐ Im Menü oben rechts auf **„Abmelden"** klicken.
8. ☐ Login-Seite erscheint, lokale Daten sind gelöscht (in den Browser-Entwicklertools sichtbar).

**Sicherheitsregeln im Hintergrund:**
- Alle Uploads werden auf Schadcode geprüft (ClamAV).
- Beim Abmelden wird der lokale Speicher des Browsers geleert (`Clear-Site-Data: storage`).

**Erwartung:**
- Sauberer Logout, lokale Browser-Daten sind weg.

**Status:** ☐ Offen

---

### Krise (CRIS)

#### SMK-A-CRIS-01 — Krisen-Eskalation

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Tagesablauf | fachkraft | C/F/S | ✓ | `test_crisis_escalation.py` |

**Voraussetzung:** SMK-A-VORM-01

**Vorbereitung:**
- Eine Klient:in in akuter Krisensituation kommt in die Einrichtung.

**Ablauf:**

1. ☐ Klient:in suchen oder neu anlegen (Stage „identifiziert").
2. ☐ Schnell-Erfassung **„Quick-Capture"** öffnen.
3. ☐ Dokumenten-Typ **„Krise"** oder **„Notfall"** wählen.
4. ☐ Sensitivitätsstufe **„hoch"** setzen.
5. ☐ Kurze Beschreibung der Krise eintragen.
6. ☐ Speichern.
7. ☐ Aufgabe **„Nachsorge: Krise XYZ"** erstellen, Priorität **„hoch"**, Lead-Kollegin als Verantwortliche.
8. ☐ Lead bekommt das WorkItem in ihrer Inbox.

**Sicherheitsregeln im Hintergrund:**
- Hochsensitive Events sind im allgemeinen Zeitstrom unsichtbar — nur Lead/Admin sehen Inhalte.

**Erwartung:**
- Krisenfall ist dokumentiert, Lead-Kollegin hat Aufgabe in Inbox.

**Status:** ☐ Offen

---

### Streetwork-Offline (OFFL)

#### SMK-A-OFFL-01 — Streetwork ohne Internet

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Tagesablauf | fachkraft | C/S | ✓ | `test_offline_apis.py`, `test_offline_login_bootstrap.py`, `test_offline_store.py`, `test_pwa_offline.py` |


**Vorbereitung:**
- Smartphone oder Tablet mit Browser, mobil ins WLAN.

**Ablauf:**

1. ☐ Mit `fachkraft` einloggen, vollständig laden lassen.
2. ☐ Zur eingebauten Klient:innen-Übersicht navigieren — Daten werden lokal gespeichert.
3. ☐ Im DevTools → Network → **„Offline"** aktivieren (Netz simuliert weg).
4. ☐ Eine bekannte Klient:in über das Offline-Menü öffnen (`/offline/clients/<id>/`).
5. ☐ Detailansicht öffnet sich aus lokalem Cache.
6. ☐ Ein neues Event lokal anlegen — wird im lokalen Speicher abgelegt.
7. ☐ Network → **„Online"** wieder aktivieren.
8. ☐ Sync-Vorgang sollte automatisch starten — Konflikte (falls vorhanden) auf der Konflikt-Seite reviewen.

**Sicherheitsregeln im Hintergrund:**
- Offline-Daten sind im lokalen Speicher verschlüsselt (mit dem Passwort der Anmeldung).
- IndexedDB-Stores (`anlaufstelle-offline`) halten Klienten/Events als `{iv,ct}` (AES-GCM); der Schlüssel liegt non-extractable in `anlaufstelle-crypto` — kein Klartext.
- Beim nächsten Passwort-Wechsel verfallen die offline-Daten automatisch.

**Erwartung:**
- Du hast offline gearbeitet, beim Wieder-Online sind die Daten synchron.

> 🤖 **Automatisiert abgedeckt** (Playwright, echtes `context.set_offline`): `test_pwa_offline.py` (Offline-Banner sichtbar bei Netz-Aus, Service-Worker-`/offline/`-Fallback, Offline-`POST` → SW-Queue + Feedback) und `test_offline_store.py` (verschlüsselter IndexedDB-Store, Sync-Konflikt-Review). Vollständige Offline-/PWA-Fälle: ENT-OFFL-01…12 / ENT-PWA-01…06 in [Matrix B](manual-test-matrix-b.md).

**Status:** ☐ Offen

---

### Mobile-Smoke (MOBI)

#### SMK-A-MOBI-01 — Mobile Vormittag (komplette Tagesstart-Sequenz)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Tagesablauf | fachkraft | C | ✓ | `test_mobile.py`, `test_layout.py` |


**Vorbereitung:**
- Browser auf iPhone-Viewport umgestellt.

**Ablauf:**

1. ☐ Komplette Sequenz aus SMK-A-VORM-01 durchspielen — Login, MFA, Übergabe, WorkItems.
2. ☐ Achten auf: Touch-Targets ≥ 44px, kein horizontales Scrollen, Menü-Hamburger funktioniert.
3. ☐ Die Klient:innen-Liste wird als Card-Layout (kein Tabellen-Scroll) gerendert.
4. ☐ Die Übergabe ist auf Mobil lesbar (Stats-Grid passt sich an).
5. ☐ WorkItem-Inbox: Status-Toggle per Tap funktioniert, Bulk-Aktionen erreichbar.

**Erwartung:**
- Voller Tagesstart-Workflow auf iPhone-Viewport ohne Layout-Brüche.

**Status:** ☐ Offen

---

#### SMK-A-MOBI-02 — Mobile Nachmittag (Case-Update + Foto-Upload)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Tagesablauf | fachkraft | C | ✓ | `test_mobile_workflows.py` |

**Voraussetzung:** Case mit Episode aus NACH-01.

**Vorbereitung:**
- Foto in der Mobile-Galerie (oder Test-Bild auf iPhone-Emulator).

**Ablauf:**

1. ☐ Case auf Mobile öffnen.
2. ☐ Goal/Milestone-Toggle per Tap.
3. ☐ Episode mit Outcome schließen.
4. ☐ Auf einem Event innerhalb des Cases einen Anhang hinzufügen.
5. ☐ Beim Datei-Picker **„Foto aufnehmen"** wählen (mobile-spezifisch).
6. ☐ Foto wird hochgeladen, Virus-Check passiert.
7. ☐ Anhang erscheint in der Liste mit Vorschau (PNG/JPEG).

**Erwartung:**
- Case-Update + Foto-Upload auf Mobile funktioniert ohne Layout-Probleme.

**Status:** ☐ Offen

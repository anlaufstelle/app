# Anlaufstelle – Benutzerhandbuch

Dieses Handbuch richtet sich an Fachkräfte, Leitungen und Assistenzen in Kontaktläden, Notschlafstellen und anderen niedrigschwelligen sozialen Einrichtungen, die mit Anlaufstelle arbeiten.

---

## Inhaltsverzeichnis

1. [Login und Passwort](#1-login-und-passwort)
2. [Startseite – Dashboard](#2-startseite--dashboard)
3. [Kontakt dokumentieren (Event erstellen)](#3-kontakt-dokumentieren-event-erstellen)
4. [Klientel verwalten](#4-klienten-verwalten)
5. [Hinweise und Aufgaben (WorkItems)](#5-hinweise-und-aufgaben-workitems)
6. [Suche](#6-suche)
7. [Statistik und Export](#7-statistik-und-export)
8. [PWA installieren (App auf dem Startbildschirm)](#8-pwa-installieren-app-auf-dem-startbildschirm)
9. [Rollen und Berechtigungen](#9-rollen-und-berechtigungen)
10. [Fallmanagement](#10-fallmanagement)

---

## 1. Login und Passwort

### Anmelden

1. Rufen Sie die Anlaufstelle-Adresse Ihrer Einrichtung im Browser auf (z. B. `https://anlaufstelle.meine-einrichtung.de`).
2. Sie werden automatisch zur Anmeldeseite weitergeleitet (`/login/`).
3. Geben Sie Ihren **Benutzernamen** und Ihr **Passwort** ein.
4. Klicken Sie auf **Anmelden**.

> **Hinweis:** Wenn Ihnen beim ersten Login mitgeteilt wurde, dass Sie das Passwort ändern müssen, werden Sie direkt nach der Anmeldung auf die Passwort-Änderungsseite weitergeleitet.

### Passwort ändern

1. Klicken Sie oben rechts auf Ihren Namen oder das Benutzer-Menü.
2. Wählen Sie **Passwort ändern** (URL: `/password/`).
3. Geben Sie Ihr aktuelles Passwort ein.
4. Geben Sie das neue Passwort zweimal ein.
5. Klicken Sie auf **Speichern**.

> **Tipp:** Wählen Sie ein sicheres Passwort (mindestens 12 Zeichen, Groß-/Kleinbuchstaben, Ziffern). Das System prüft die Mindestanforderungen.

### Abmelden

Klicken Sie oben rechts auf **Abmelden**. Aus Datenschutzgründen werden Sie nach einer konfigurierten Inaktivitätszeit automatisch abgemeldet (Standard: 30 Minuten).

---

## 2. Startseite – Dashboard

Nach dem Login landen Sie auf dem **Dashboard** (`/`). Das Dashboard gibt Ihnen einen personalisierten Überblick über Ihren Arbeitsalltag.

### Widgets

Das Dashboard besteht aus vier Widgets:

| Widget | Beschreibung |
|--------|-------------|
| **Meine Aufgaben** | Ihre offenen und laufenden Aufgaben, sortiert nach Priorität und Fälligkeit |
| **Übersicht** | Kennzahlen: Kontakte heute, offene Fälle, eigene Aufgaben, Aufgaben gesamt |
| **Heute** | Kompakter Tagesfeed mit den letzten Aktivitäten und Kontakten. Über „Alle anzeigen" gelangen Sie zum vollständigen Aktivitätslog. |
| **Letzte Klientel** | Kürzlich besuchte Klientelprofile für den Schnellzugriff |

### Widgets ein- und ausblenden

1. Klicken Sie auf das **Zahnrad-Symbol** neben der Überschrift „Dashboard".
2. Im Dropdown können Sie einzelne Widgets per Toggle ein- oder ausblenden.
3. Ihre Einstellung wird automatisch gespeichert und bleibt auch nach erneutem Login erhalten.

### Hausverbot-Banner

Wenn aktive Hausverbote in Ihrer Einrichtung bestehen, werden diese als rotes Banner unterhalb der Überschrift angezeigt — auf dem Dashboard ebenso wie im Aktivitätslog.

### Aktivitätslog und Timeline

Neben dem Dashboard stehen Ihnen zwei weitere Ansichten zur Verfügung:

**Aktivitätslog** (`/aktivitaetslog/`): Der vollständige Tagesfeed aller dokumentierten Kontakte und Systemaktivitäten. Erreichbar über die Sidebar-Navigation oder den Link „Alle anzeigen" im Dashboard-Widget.

- **Datum wechseln:** Navigieren Sie mit den Pfeil-Buttons zum vorherigen oder nächsten Tag.
- **Feed-Typ filtern:** Wählen Sie „Alle", „Events" oder „Aktivitäten" im Dropdown.

**Timeline** (`/timeline/`): Schichtbasierte Ansicht mit TimeFilter-Tabs (z. B. „Frühdienst", „Spätdienst", „Nachtdienst"). Die Timeline zeigt nur Kontakte (Events), keine Systemaktivitäten.

1. Klicken Sie auf den gewünschten Schicht-Tab.
2. Die Ereignisliste aktualisiert sich sofort und zeigt nur Kontakte aus diesem Zeitfenster.

> **Hinweis:** Der Tab, der als Standard markiert ist, wird automatisch beim Öffnen der Timeline aktiviert.

---

## 3. Kontakt dokumentieren (Event erstellen)

Ein **Event** ist ein einzelner dokumentierter Kontakt – z. B. ein Beratungsgespräch, eine Spritzentauschausgabe oder ein anonymer Besuch.

### Neuen Kontakt erfassen

1. Klicken Sie in der Sidebar auf **Neu** und wählen Sie **Neuer Kontakt** (oder navigieren Sie zu `/events/new/`).
2. **Dokumentationstyp wählen:** Wählen Sie aus der Liste den passenden Typ (z. B. „Kontakt", „Krisengespräch", „Spritzentausch"). Die verfügbaren Typen sind von Ihrer Einrichtung konfiguriert.
3. **Felder ausfüllen:** Nach der Typauswahl werden die zugehörigen Eingabefelder geladen. Füllen Sie alle relevanten Felder aus.
4. **Zeitpunkt:** Das Feld „Zeitpunkt" ist automatisch auf die aktuelle Uhrzeit gesetzt. Sie können es anpassen, wenn Sie einen Kontakt nachträglich erfassen.
5. **Klientel zuordnen (optional):**
   - Für einen **anonymen Kontakt** (ohne Pseudonym): Aktivieren Sie die Option „Anonym". Es wird kein Klientel verknüpft.
   - Für einen **identifizierten Klientel**: Beginnen Sie im Klientel-Feld mit der Eingabe des Pseudonyms. Es erscheint eine Vorschlagsliste – wählen Sie den passenden Klientel aus.
   - Wenn der Klientel noch nicht erfasst ist, legen Sie ihn zuerst unter **Klientel** an (siehe [Abschnitt 4](#4-klienten-verwalten)).
6. Klicken Sie auf **Speichern**.

Sie werden zur Detailansicht des neu erstellten Eintrags weitergeleitet. Eine Erfolgsmeldung bestätigt die Speicherung.

> **Tipp:** Wenn Sie von der Klientel-Detailseite aus einen Kontakt erfassen, ist der Klientel bereits vorausgefüllt.

### Kontakt bearbeiten

1. Öffnen Sie das Ereignis (über den Aktivitätslog oder die Klientel-Chronik).
2. Klicken Sie auf **Bearbeiten**.
3. Ändern Sie die gewünschten Felder und klicken Sie auf **Speichern**.

> **Hinweis:** Bearbeitungen werden in der Änderungshistorie des Ereignisses protokolliert. Frühere Versionen bleiben im Verlauf sichtbar.

### Kontakt löschen

1. Öffnen Sie das Ereignis.
2. Klicken Sie auf **Löschen**.
3. Geben Sie eine Begründung ein.
4. Bestätigen Sie die Löschung.

> **Wichtig:** Wenn der Kontakt einem **qualifizierten Klientel** zugeordnet ist, wird kein sofortiges Löschen durchgeführt. Stattdessen wird automatisch ein **Löschantrag** gestellt, der von einer Leitung oder Administration genehmigt werden muss (4-Augen-Prinzip). Sie erhalten eine entsprechende Rückmeldung.

---

## 4. Klientel verwalten

Klientel werden in Anlaufstelle **ausschließlich mit Pseudonymen** erfasst – keine Klarnamen. Das Pseudonym ist der primäre Identifikator.

### Kontaktstufen

Jeder Klientel hat eine **Kontaktstufe**:

| Stufe | Beschreibung |
|---|---|
| **Identifiziert** | Pseudonym bekannt, grundlegende Daten (Altersgruppe) vorhanden |
| **Qualifiziert** | Zusätzliche persönliche Daten vorhanden (eingeschränkter Zugriff, Audit-Log) |

> **Hinweis:** Der Zugriff auf qualifizierte Klientelprofile wird automatisch im Audit-Log protokolliert.

### Neuen Klientel anlegen

1. Navigieren Sie zu **Klientel** (`/clients/`) und klicken Sie auf **Neues Klientel** (oder direkt zu `/clients/new/`).
2. Füllen Sie das Formular aus:
   - **Pseudonym:** Einzigartiger Name innerhalb Ihrer Einrichtung (z. B. ein selbstgewählter Spitzname). Das Pseudonym darf innerhalb einer Einrichtung nur einmal vorkommen.
   - **Kontaktstufe:** Wählen Sie „Identifiziert" oder „Qualifiziert".
   - **Altersgruppe:** „Unter 18", „18–26", „27+" oder „Unbekannt".
   - **Notizen:** Interne Anmerkungen zum Klientel (optional).
3. Klicken Sie auf **Speichern**.

### Klientel suchen

1. Navigieren Sie zu **Klientel** (`/clients/`).
2. Geben Sie im Suchfeld ein Teil des Pseudonyms ein. Die Liste filtert sich automatisch.
3. Optional können Sie zusätzlich nach **Kontaktstufe** oder **Altersgruppe** filtern.
4. Klicken Sie auf einen Eintrag, um die Detailansicht zu öffnen.

> **Tipp:** Die Schnellsuche über das globale Suchfeld (Lupe oben) findet Klientel und Ereignisse gleichzeitig.

### Klientel-Detailseite und Chronik

Die Detailseite eines Klientel (`/clients/<id>/`) zeigt:

- **Stammdaten:** Pseudonym, Kontaktstufe, Altersgruppe, Notizen
- **Chronik:** Alle bisherigen dokumentierten Kontakte dieses Klientel, neueste zuerst
- **Offene WorkItems:** Aufgaben und Hinweise, die diesem Klientel zugeordnet sind

Von der Detailseite aus können Sie direkt einen neuen Kontakt für diesen Klientel erfassen oder eine neue Aufgabe anlegen.

### Klientel bearbeiten

1. Öffnen Sie die Klientel-Detailseite.
2. Klicken Sie auf **Bearbeiten**.
3. Ändern Sie die gewünschten Felder (z. B. Kontaktstufe hochsetzen).
4. Klicken Sie auf **Speichern**.

> **Hinweis:** Eine Änderung der Kontaktstufe wird automatisch im Audit-Log protokolliert.

---

## 5. Hinweise und Aufgaben (WorkItems)

**WorkItems** dienen der teaminternen Kommunikation. Es gibt zwei Typen:

| Typ | Beschreibung |
|---|---|
| **Hinweis** | Information für das Team, die keine direkte Handlung erfordert |
| **Aufgabe** | Konkreter Arbeitsauftrag, der erledigt werden muss |

### Status-Lebenszyklus

```
Offen → In Bearbeitung → Erledigt
              ↓
          Verworfen
```

WorkItems können drei Prioritätsstufen haben: **Normal**, **Wichtig** oder **Dringend**. Dringende Aufgaben erscheinen oben in der Liste.

### Posteingang öffnen

Navigieren Sie zu **Aufgaben** (`/workitems/`). Die Inbox zeigt:

- **Offen:** Neue Aufgaben und Hinweise, die Ihnen zugewiesen sind oder noch keiner Person zugewiesen wurden
- **In Bearbeitung:** Aufgaben, die Sie (oder Kolleg:innen) übernommen haben
- **Abgeschlossen:** In den letzten 7 Tagen erledigte oder verworfene Einträge

### Status ändern

Direkt in der Inbox-Karte eines WorkItems können Sie den Status per Klick ändern:

- **„In Bearbeitung nehmen"** – übernimmt die Aufgabe automatisch auf Ihren Namen
- **„Erledigt"** – schließt die Aufgabe ab
- **„Verwerfen"** – für Aufgaben, die nicht mehr relevant sind

Die Liste aktualisiert sich ohne Seitenneuladung.

### Neue Aufgabe oder Hinweis erstellen

1. Klicken Sie in der Inbox auf **Neue Aufgabe** (oder navigieren Sie zu `/workitems/new/`).
2. Füllen Sie das Formular aus:
   - **Typ:** „Aufgabe" oder „Hinweis"
   - **Titel:** Kurze, prägnante Beschreibung
   - **Beschreibung:** Ausführlichere Details (optional)
   - **Priorität:** Normal, Wichtig oder Dringend
   - **Zugewiesen an:** Eine bestimmte Person aus Ihrer Einrichtung (optional – leer lassen, wenn die Aufgabe für alle gilt)
   - **Klientel:** Falls die Aufgabe ein bestimmtes Klientel betrifft (optional)
3. Klicken Sie auf **Speichern**.

> **Tipp:** Wenn Sie von der Klientel-Detailseite aus eine neue Aufgabe erstellen, ist der Klientel bereits vorausgefüllt.

### Aufgabe bearbeiten

1. Öffnen Sie die Aufgabe per Klick auf den Titel.
2. Auf der Detailseite klicken Sie auf **Bearbeiten**.
3. Ändern Sie die gewünschten Felder und speichern Sie.

---

## 6. Suche

Die **Suche** durchsucht gleichzeitig Klientel (nach Pseudonym) und Ereignisse (nach Klientel-Pseudonym und Inhaltsfeldern). Sie ist auf zwei Wegen erreichbar:

### Globale Suche (Schnellsuche)

Das Suchfeld ist **permanent in der Sidebar** sichtbar (Desktop). Auf dem Smartphone öffnet sich über das Such-Icon in der unteren Navigation ein Overlay.

1. Beginnen Sie mit der Eingabe im Suchfeld. Nach einer kurzen Verzögerung erscheinen Ergebnisse als Dropdown.
2. Es werden maximal **5 Klientel** und **5 Ereignisse** angezeigt.
3. Klicken Sie auf einen Treffer, um direkt zur Detailseite zu springen.
4. Über **„Alle Ergebnisse anzeigen"** gelangen Sie zur vollständigen Suchseite.

> **Tipp:** Mit der Escape-Taste schließen Sie das Such-Dropdown.

### Vollständige Suchseite

Für umfangreichere Recherchen steht weiterhin die Suchseite unter `/search/` zur Verfügung. Sie zeigt alle Treffer (bis zu 20 Klientel und 20 Ereignisse) und ist auch über den Link „Alle Ergebnisse anzeigen" in der Schnellsuche erreichbar.

> **Hinweis:** Felder, die als verschlüsselt konfiguriert sind, werden in der Suche nicht durchsucht.

---

## 7. Statistik und Export

> **Zugriff:** Statistik und Exporte sind nur für **Leitungen** und **Administratoren** verfügbar.

### Statistik-Dashboard

1. Navigieren Sie zu **Statistik** (`/statistics/`).
2. Wählen Sie einen Zeitraum:
   - **Letzter Monat** (Standard)
   - **Letztes Quartal** (90 Tage)
   - **Letztes Halbjahr** (182 Tage)
   - **Benutzerdefiniert:** Geben Sie Start- und Enddatum manuell ein.
3. Das Dashboard aktualisiert sich automatisch und zeigt aggregierte Kennzahlen zu Kontaktzahlen, Dokumentationstypen und Klientelgruppen.

### Jahresnavigation

Um die Daten eines bestimmten Jahres zu sehen:

1. Klicken Sie auf **Jahr**.
2. Mit den Pfeil-Buttons links und rechts neben der Jahreszahl navigieren Sie zum vorherigen oder nächsten Jahr.
3. Beim aktuellen Jahr wird der Zeitraum vom 01.01. bis heute angezeigt, bei vergangenen Jahren das vollständige Jahr (01.01. – 31.12.).

### Trend-Charts

Unterhalb der Kennzahlen zeigt das Dashboard drei interaktive Diagramme:

| Diagramm | Beschreibung |
|----------|-------------|
| **Kontakte im Zeitverlauf** | Liniendiagramm mit monatlicher Aufschlüsselung der Kontakte (Gesamt, Anonym, Identifiziert, Qualifiziert) |
| **Dokumentationstypen** | Balkendiagramm mit der Verteilung nach Dokumentationstyp (z. B. Kontakt, Beratungsgespräch, Spritzentausch) |
| **Altersgruppen** | Ringdiagramm mit der demografischen Verteilung nach Altersgruppe |

Die Charts aktualisieren sich automatisch beim Wechsel des Zeitraums.

**Datenquellen-Anzeige:** Im Liniendiagramm zeigt die Legende, ob ein Datenpunkt aus einem **Snapshot** (vorberechnete Monatsdaten) oder aus **Live-Daten** (aktuelle Datenbankabfrage) stammt. Snapshots stellen sicher, dass historische Trends auch nach Ablauf von Löschfristen erhalten bleiben.

**Dokumentationstyp-Filter:** Über das Dropdown „Alle Dokumentationstypen" oberhalb der Charts können Sie die Ansicht auf einen bestimmten Dokumentationstyp einschränken.

> **Hinweis:** Die Charts werden beim Drucken nicht angezeigt. Nutzen Sie für Berichte die PDF- oder CSV-Exportfunktionen.

### CSV-Export

Der CSV-Export enthält alle Ereignisse des gewählten Zeitraums. Felder, deren Sensitivitätsstufe die Berechtigung des exportierenden Benutzers übersteigt, werden als „[Eingeschränkt]" angezeigt.

1. Öffnen Sie das Statistik-Dashboard und wählen Sie den gewünschten Zeitraum.
2. Klicken Sie auf **CSV exportieren**.
3. Die Datei wird sofort heruntergeladen (Dateiname: `export_YYYY-MM-DD_YYYY-MM-DD.csv`).

> **Hinweis:** Jeder Export wird im Audit-Log protokolliert.

### PDF-Bericht

Der PDF-Bericht erstellt einen strukturierten Halbjahresbericht für die interne Dokumentation.

1. Wählen Sie im Statistik-Dashboard den Zeitraum.
2. Klicken Sie auf **PDF-Bericht**.
3. Die PDF-Datei wird heruntergeladen (Dateiname: `bericht_YYYY-MM-DD_YYYY-MM-DD.pdf`).

### Jugendamt-Sachbericht

Der Jugendamt-Export erstellt einen standardisierten Sachbericht im Jugendamt-Format.

1. Wählen Sie im Statistik-Dashboard den Zeitraum.
2. Klicken Sie auf **Jugendamt-Bericht**.
3. Die PDF-Datei wird heruntergeladen (Dateiname: `jugendamt_YYYY-MM-DD_YYYY-MM-DD.pdf`).

> **Tipp:** Für Halbjahresberichte wählen Sie den Zeitraum „Letztes Halbjahr" und passen Sie Start- und Enddatum bei Bedarf manuell auf den 01.01. bzw. 30.06. (oder 01.07. – 31.12.) an.

### Löschanträge prüfen (Leitung / Admin)

Wenn eine Fachkraft oder Assistenz einen Kontakt eines qualifizierten Klientel löschen möchte, entsteht ein **Löschantrag**, der von einer Leitung oder Administration genehmigt werden muss.

1. Offene Löschanträge sehen Sie unter `/events/deletion-requests/`.
2. Klicken Sie auf einen Antrag, um Details und das betroffene Ereignis einzusehen.
3. Klicken Sie auf **Genehmigen** oder **Ablehnen** und bestätigen Sie.

> **Wichtig:** Sie können Ihren eigenen Löschantrag nicht selbst genehmigen (4-Augen-Prinzip).

---

## 8. PWA installieren (App auf dem Startbildschirm)

Anlaufstelle kann wie eine native App auf dem Startbildschirm Ihres Smartphones oder Tablets installiert werden – ohne App Store.

### Auf Android (Chrome)

1. Öffnen Sie Anlaufstelle in Chrome.
2. Tippen Sie auf das Dreipunkt-Menü (⋮) oben rechts.
3. Wählen Sie **App installieren** oder **Zum Startbildschirm hinzufügen**.
4. Bestätigen Sie mit **Installieren**.

Die App erscheint nun als Symbol auf Ihrem Startbildschirm und öffnet sich ohne Browser-Adressleiste im Vollbildmodus.

### Auf iOS (Safari)

1. Öffnen Sie Anlaufstelle in Safari.
2. Tippen Sie auf das Teilen-Symbol (Quadrat mit Pfeil nach oben).
3. Scrollen Sie in der Aktionsliste nach unten und wählen Sie **Zum Home-Bildschirm**.
4. Vergeben Sie ggf. einen Namen und tippen Sie auf **Hinzufügen**.

### Auf Desktop (Chrome / Edge)

1. Öffnen Sie Anlaufstelle im Browser.
2. In der Adressleiste erscheint rechts ein Installations-Symbol (Bildschirm mit Pfeil).
3. Klicken Sie darauf und bestätigen Sie mit **Installieren**.

Die installierte App verhält sich wie ein normales Programm und ist über das Startmenü oder den Desktop erreichbar.

> **Hinweis:** Die App ist eine **Progressive Web App (PWA)** – sie benötigt weiterhin eine Internetverbindung zum Arbeiten. Es handelt sich nicht um eine Offline-Anwendung.

> **Firefox (Android):** Firefox bietet zwar "Installieren" an, die App öffnet sich aber immer mit Adressleiste. Für den echten App-Modus ohne Adressleiste verwenden Sie **Chrome, Edge oder Samsung Internet** (Android) bzw. **Safari** (iOS).

---

## 9. Rollen und Berechtigungen

Anlaufstelle unterscheidet vier Rollen. Ihre Rolle wird von der Administration festgelegt.

### Rollenübersicht

| Rolle | Anzeigename | Kurzbeschreibung |
|---|---|---|
| `admin` | Administrator | Vollzugriff auf alle Bereiche und Einstellungen |
| `lead` | Leitung | Alle Fachkraft-Funktionen plus Auswertungen und Leitungsaufgaben |
| `staff` | Fachkraft | Standardrolle für Mitarbeitende in der Dokumentation |
| `assistant` | Assistenz | Eingeschränkte Erfassung ohne Zugriff auf qualifizierte Klienteldaten |

### Was darf wer?

| Funktion | Assistenz | Fachkraft | Leitung | Admin |
|---|---|---|---|---|
| Dashboard / Startseite einsehen | Ja | Ja | Ja | Ja |
| Anonyme Kontakte dokumentieren | Ja | Ja | Ja | Ja |
| Identifizierte Kontakte dokumentieren | Ja | Ja | Ja | Ja |
| Klientel anlegen und suchen | Ja | Ja | Ja | Ja |
| Qualifizierte Klienteldetails einsehen | Nein | Ja (eigene Einrichtung) | Ja | Ja |
| Eigene Ereignisse bearbeiten | Ja | Ja | Ja | Ja |
| Fremde Ereignisse bearbeiten | Nein | Ja | Ja | Ja |
| WorkItems erstellen und bearbeiten | Ja | Ja | Ja | Ja |
| Suche verwenden | Ja | Ja | Ja | Ja |
| Statistik und Export | Nein | Nein | Ja | Ja |
| Klienteldaten exportieren (Art. 15 / 20 DSGVO) | Nein | Nein | Ja | Ja |
| DSGVO-Dokumentationspaket herunterladen | Nein | Nein | Nein | Ja |
| Löschanträge stellen | Ja | Ja | Ja | Ja |
| Löschanträge genehmigen | Nein | Nein | Ja | Ja |
| Pseudonyme verwalten / Kontaktstufe ändern | Nein | Nein | Ja | Ja |
| Audit-Log einsehen | Nein | Nein | Nein | Ja |
| Fallmanagement (Fälle, Episoden, Ziele) | Nein | Ja | Ja | Ja |
| Fälle schließen / wiedereröffnen | Nein | Nein | Ja | Ja |
| Benutzer und Einstellungen verwalten | Nein | Nein | Nein | Ja |

> **Hinweis:** Der Zugriff ist immer auf die eigene Einrichtung beschränkt. Mitarbeitende einer Einrichtung können keine Daten anderer Einrichtungen derselben Organisation sehen.

### Audit-Log (nur Admin)

Das Audit-Log (`/audit/`) protokolliert automatisch sicherheitsrelevante Aktionen: Anmeldungen, Zugriffe auf qualifizierte Daten, Exporte, Löschungen und Stufenwechsel. Es kann nicht verändert werden und dient der Nachvollziehbarkeit im Sinne der DSGVO.

---

## 10. Fallmanagement

Nicht jeder Kontakt mit einem Klientel steht für sich allein. Wenn die Zusammenarbeit mit einer Person über einen längeren Zeitraum läuft – z. B. ein Beratungsprozess, eine Krisenbegleitung oder eine Wohnungsvermittlung – können Sie diese Arbeit in einem **Fall** bündeln.

Ein Fall ist eine **Klammer** um thematisch zusammengehörige Kontakte. Das Fallmanagement ist **optional**: Sie können Anlaufstelle genauso ohne Fälle nutzen, wenn Ihre Einrichtung keine laufenden Beratungsprozesse dokumentiert.

> **Zugriff:** Fallmanagement steht **Fachkräften**, **Leitungen** und **Administratoren** zur Verfügung. Assistenzen haben keinen Zugriff.

### Fallliste

1. Navigieren Sie zu **Fälle** (`/cases/`).
2. Sie sehen eine Tabelle aller Fälle Ihrer Einrichtung, sortiert nach Erstellungsdatum (neueste zuerst).
3. **Suche:** Geben Sie im Suchfeld einen Titel ein – die Liste filtert sich automatisch während der Eingabe.
4. **Status-Filter:** Wählen Sie im Dropdown „Offen", „Geschlossen" oder „Alle Status", um die Anzeige einzuschränken.

### Neuen Fall erstellen

1. Klicken Sie auf der Fallliste auf **Neuer Fall** (oder navigieren Sie zu `/cases/new/`).
2. Füllen Sie das Formular aus:
   - **Titel** (Pflichtfeld): Eine kurze Bezeichnung für den Fall (z. B. „Wohnungssuche", „Suchtberatung").
   - **Klientel:** Beginnen Sie mit der Eingabe des Pseudonyms – es erscheint eine Vorschlagsliste. Wählen Sie das passende Klientel aus. Ein Fall kann auch ohne Klientel erstellt werden.
   - **Beschreibung:** Ausführlichere Informationen zum Fall (optional).
   - **Fallverantwortlich:** Wählen Sie die zuständige Person aus dem Dropdown (optional). Nur Fachkräfte, Leitungen und Administratoren Ihrer Einrichtung stehen zur Auswahl.
3. Klicken Sie auf **Fall erstellen**.

Sie werden zur Detailseite des neuen Falls weitergeleitet.

> **Tipp:** Wenn Sie von der Klientel-Detailseite aus einen neuen Fall erstellen, ist der Klientel bereits vorausgefüllt.

### Fall-Detailseite

Die Detailseite eines Falls (`/cases/<id>/`) gliedert sich in drei Bereiche:

**Kopfbereich:**
- Titel und Status-Badge (Offen / Geschlossen)
- Link zum zugehörigen Klientel
- Fallverantwortlicher, Erstellt am, Erstellt von
- Beschreibung (falls vorhanden)
- Buttons: **Bearbeiten**, **Schließen** bzw. **Wiedereröffnen**

**Linke Spalte – Kontakte:**
- Alle dem Fall zugeordneten Kontakte (Events), chronologisch sortiert
- Möglichkeit, weitere Kontakte des Klientel zuzuordnen oder bestehende zu entfernen

**Rechte Spalte – Episoden und Wirkungsziele:**
- Liste der Episoden (Phasen innerhalb des Falls)
- Liste der Wirkungsziele mit Meilensteinen

### Fall bearbeiten

1. Öffnen Sie die Fall-Detailseite.
2. Klicken Sie auf **Bearbeiten**.
3. Ändern Sie Titel, Klientel, Beschreibung oder Fallverantwortlichen.
4. Klicken Sie auf **Speichern**.

### Fall schließen und wiedereröffnen

Wenn die Arbeit an einem Fall abgeschlossen ist, kann der Fall geschlossen werden.

1. Öffnen Sie die Fall-Detailseite.
2. Klicken Sie auf **Schließen**. Der Fall wird mit dem aktuellen Zeitstempel als geschlossen markiert.

Ein geschlossener Fall kann jederzeit wieder geöffnet werden:

1. Öffnen Sie den geschlossenen Fall.
2. Klicken Sie auf **Wiedereröffnen**.

> **Wichtig:** Fälle schließen und wiedereröffnen können nur **Leitungen** und **Administratoren**.

### Kontakte zuordnen und entfernen

Auf der Fall-Detailseite sehen Sie unterhalb der zugeordneten Kontakte eine Liste der **nicht zugeordneten Kontakte** des Klientel. So ordnen Sie einen Kontakt zu:

1. Klicken Sie bei dem gewünschten Kontakt auf die Zuordnen-Schaltfläche.
2. Der Kontakt wird sofort in die Fallliste verschoben (ohne Seitenneuladen).

Um einen Kontakt wieder aus dem Fall zu entfernen:

1. Klicken Sie beim zugeordneten Kontakt auf **Entfernen**.
2. Der Kontakt wird aus dem Fall gelöst und erscheint wieder in der Liste der nicht zugeordneten Kontakte.

> **Hinweis:** Das Zuordnen und Entfernen ändert nur die Fall-Zugehörigkeit. Der Kontakt selbst bleibt unverändert erhalten.

### Episoden

Eine **Episode** ist eine abgrenzbare Phase innerhalb eines Falls. Wenn z. B. ein Klientel dreimal im Jahr in eine Krisensituation gerät, können diese als drei separate Episoden innerhalb desselben Falls dokumentiert werden.

**Neue Episode erstellen:**

1. Klicken Sie auf der Fall-Detailseite (rechte Spalte) auf **Neue Episode**.
2. Füllen Sie das Formular aus:
   - **Titel** (Pflichtfeld): Bezeichnung der Phase (z. B. „Krisenepisode März 2026").
   - **Beginn** (Pflichtfeld): Startdatum der Episode.
   - **Beschreibung:** Zusätzliche Details (optional).
   - **Ende:** Enddatum (optional – leer lassen, wenn die Episode noch läuft).
3. Klicken Sie auf **Speichern**.

> **Hinweis:** Episoden können nur für **offene** Fälle erstellt werden.

**Episode bearbeiten:** Klicken Sie bei der Episode auf **Bearbeiten**, um Titel, Beschreibung, Beginn oder Ende anzupassen.

**Episode abschließen:** Klicken Sie auf **Abschließen**. Das Enddatum wird automatisch auf das heutige Datum gesetzt.

Jede Episode zeigt ihren Status:
- **aktiv** (grün) – noch kein Enddatum
- **abgeschlossen** (grau) – Enddatum gesetzt

### Wirkungsziele und Meilensteine

**Wirkungsziele** dokumentieren, was die Arbeit am Fall erreichen soll – z. B. „Stabile Wohnsituation" oder „Anbindung an Suchtberatung". Jedes Ziel kann in konkrete **Meilensteine** untergliedert werden.

**Neues Wirkungsziel erstellen:**

1. Auf der Fall-Detailseite (rechte Spalte, Bereich „Wirkungsziele") geben Sie den Titel in das Textfeld ein.
2. Klicken Sie auf **Hinzufügen**.
3. Das Ziel erscheint sofort in der Liste (ohne Seitenneuladen).

**Wirkungsziel bearbeiten:** Klicken Sie auf das Bearbeiten-Symbol neben dem Zieltitel. Es öffnet sich ein Inline-Formular, in dem Sie Titel und Beschreibung anpassen können.

**Ziel als erreicht markieren:** Klicken Sie auf **Ziel erreicht**. Das Ziel wird mit dem heutigen Datum als erreicht markiert und erhält ein grünes Badge. Falls ein Ziel fälschlicherweise als erreicht markiert wurde, können Sie dies mit **Nicht erreicht** rückgängig machen.

**Meilensteine hinzufügen:**

1. Unterhalb eines Wirkungsziels geben Sie den Meilenstein-Titel in das Textfeld ein.
2. Klicken Sie auf **+**.
3. Der Meilenstein erscheint als Checklisten-Eintrag.

**Meilenstein abhaken:** Klicken Sie auf den Kreis neben dem Meilenstein. Abgehakte Meilensteine werden durchgestrichen dargestellt. Erneutes Klicken hebt die Markierung wieder auf.

**Meilenstein löschen:** Fahren Sie mit der Maus über den Meilenstein und klicken Sie auf das **×**-Symbol.

> **Beispiel:** Wirkungsziel „Stabile Wohnsituation" mit den Meilensteinen:
> - Erstberatung Wohnhilfe abgeschlossen
> - Antrag gestellt
> - Wohnung gefunden

### Berechtigungen im Fallmanagement

| Funktion | Assistenz | Fachkraft | Leitung | Admin |
|---|---|---|---|---|
| Fallliste einsehen | Nein | Ja | Ja | Ja |
| Fall erstellen und bearbeiten | Nein | Ja | Ja | Ja |
| Fall schließen / wiedereröffnen | Nein | Nein | Ja | Ja |
| Kontakte zuordnen / entfernen | Nein | Ja | Ja | Ja |
| Episoden verwalten | Nein | Ja | Ja | Ja |
| Wirkungsziele und Meilensteine | Nein | Ja | Ja | Ja |

---

*Anlaufstelle – Dokumentationssystem für niedrigschwellige soziale Hilfsangebote*

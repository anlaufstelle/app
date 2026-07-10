# Anlaufstelle – Benutzerhandbuch

Dieses Handbuch richtet sich an Fachkräfte, Leitungen und Assistenzen in Kontaktläden, Notschlafstellen und anderen niedrigschwelligen sozialen Einrichtungen, die mit Anlaufstelle arbeiten.

---

## Inhaltsverzeichnis

1. [Login und Passwort](#1-login-und-passwort)
2. [Startseite – Zeitstrom](#2-startseite--zeitstrom)
 - [Arbeitszentrale](#2a-arbeitszentrale)
3. [Kontakt dokumentieren (Event erstellen)](#3-kontakt-dokumentieren-event-erstellen)
 - [Dateien-Übersicht](#3a-dateien-übersicht)
4. [Personen verwalten](#4-personen-verwalten)
5. [Hinweise und Aufgaben (WorkItems)](#5-hinweise-und-aufgaben-workitems)
6. [Suche](#6-suche)
7. [Statistik und Export](#7-statistik-und-export)
8. [PWA installieren und Offline arbeiten](#8-pwa-installieren-und-offline-arbeiten)
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

> **Konto gesperrt nach mehreren Fehlversuchen?** Nach **10 fehlgeschlagenen Anmeldungen** wird Ihr Konto automatisch gesperrt. Sie sehen dann eine entsprechende Hinweis-Seite und können sich nicht mehr anmelden. Auf der Login-Seite finden Sie drei Selbsthilfe-Wege ([Refs #869](https://github.com/anlaufstelle/app/issues/869)):
>
> - **Passwort vergessen?** — Klassischer Reset per E-Mail. Wenn Sie ein neues Passwort setzen, wird Ihr Konto automatisch entsperrt.
> - **Konto entsperren per E-Mail** — Schickt Ihnen einen Entsperr-Link an Ihre hinterlegte E-Mail-Adresse, der 30 Minuten gültig ist. Kein neues Passwort nötig.
> - **Backup-Code einlösen** — Wenn Sie 2FA aktiviert haben, geben Sie Username + einen Ihrer Backup-Codes ein. Jeder Code ist einmalig.
>
> Wenn keiner dieser Wege funktioniert, bitten Sie eine Administratorin um Entsperrung. Alle Vorgänge werden im Audit-Log protokolliert.

### Passwort ändern

1. Klicken Sie oben rechts auf Ihren Namen oder das Benutzer-Menü.
2. Wählen Sie **Passwort ändern** (URL: `/password-change/`).
3. Geben Sie Ihr aktuelles Passwort ein.
4. Geben Sie das neue Passwort zweimal ein.
5. Klicken Sie auf **Speichern**.

> **Tipp:** Wählen Sie ein sicheres Passwort (mindestens 12 Zeichen, Groß-/Kleinbuchstaben, Ziffern). Das System prüft die Mindestanforderungen.

> **Einladung neuer Nutzer:** Die erste Einladung an neue Nutzer kommt jetzt per Token-Link (kein Klartext-Initialpasswort mehr). Beim ersten Klick auf den Link legen Sie selbst ein Passwort fest.

### Zwei-Faktor-Authentifizierung (2FA)

Die Anlaufstelle unterstützt zeitbasierte Einmalcodes (TOTP) als zweiten Faktor beim Login. Eine Administratorin kann 2FA für einzelne Benutzer oder einrichtungsweit verpflichtend machen; unabhängig davon kann jede/r Benutzer/in 2FA freiwillig aktivieren.

**Einrichtung (einmalig):**

1. Oben rechts auf Ihren Namen → **Zwei-Faktor-Authentifizierung** (URL: `/mfa/settings/`).
2. **2FA einrichten** klicken → QR-Code wird angezeigt.
3. Eine Authenticator-App installieren und den QR-Code scannen. Getestete Apps:
 - **Google Authenticator** (Android/iOS)
 - **Microsoft Authenticator** (Android/iOS)
 - **Authy** (Android/iOS/Desktop)
 - **FreeOTP / FreeOTP+** (Android, Open Source)
 - **1Password**, **Bitwarden**, **Proton Pass** (als integrierter Authenticator)
4. Den 6-stelligen Code aus der App eingeben und **Bestätigen & aktivieren** klicken.

> **Tipp:** Falls der QR-Code nicht scannbar ist, tippen Sie auf **Secret manuell eingeben** und übertragen die Zeichenkette in die App (Feld „Secret" / „Schlüssel" — Base32, ohne Leerzeichen). Wählen Sie in der App den Typ **TOTP / zeitbasiert**.

**Login mit 2FA:**

1. Benutzername und Passwort wie gewohnt eingeben.
2. Auf der folgenden Seite den aktuell in der App angezeigten 6-stelligen Code eintippen.
3. Der Code ist jeweils **30 Sekunden** gültig — falls er abläuft, einfach den nächsten nehmen.

**2FA deaktivieren:**

Unter `/mfa/settings/` → **2FA deaktivieren**. Dies ist **nicht möglich**, wenn Ihre Einrichtung 2FA verpflichtend vorschreibt oder Ihr Konto individuell als 2FA-pflichtig markiert ist — in diesem Fall wenden Sie sich an Ihre Administration.

**Backup-Codes (für den Notfall):**

Bei der 2FA-Einrichtung erhalten Sie **10 einmalig nutzbare Backup-Codes**. Bewahren Sie diese sicher auf — z. B. ausgedruckt im Geldbeutel oder im Passwort-Manager. Falls Sie Ihr Telefon verlieren oder die Authenticator-App zurückgesetzt wurde, können Sie am 2FA-Login einen Backup-Code statt eines TOTP-Codes eingeben. Jeder Code funktioniert nur einmal.

> **Alle Codes verbraucht oder verloren?** Wenden Sie sich an Ihre Administration — sie kann Ihr TOTP-Gerät zurücksetzen, danach richten Sie 2FA und neue Backup-Codes ein.

### Abmelden

Klicken Sie oben rechts auf **Abmelden**. Aus Datenschutzgründen werden Sie nach einer konfigurierten Inaktivitätszeit automatisch abgemeldet (Standard: 30 Minuten).

---

## 2. Startseite – Zeitstrom

Nach dem Login landen Sie auf dem **Zeitstrom** (`/`) — einem chronologischen Tagesfeed, der Kontakte, Systemaktivitäten, Aufgaben und Hausverbote in einer einheitlichen Ansicht zusammenführt.

### Was der Zeitstrom zeigt

Der Feed fasst vier Quellen des aktuellen Tages zusammen:

| Quelle | Was wird angezeigt |
|--------|--------------------|
| **Kontakte** (Events) | Dokumentationseinträge mit Vorschaufeldern |
| **Aktivitäten** | System-Operationen (erstellt, bearbeitet, gelöscht …) |
| **Aufgaben** (WorkItems) | Hinweise und Aufgaben mit Priorität und Status |
| **Hausverbote** | Aktive Hausverbote — zusätzlich als rotes Banner oben |

In der rechten Seitenspalte stehen Ihre **5 dringendsten offenen Aufgaben** (sortiert nach Priorität, Fälligkeit und Erstelldatum) — ein Schnellzugriff während der Schicht.

### Datum wechseln

- **Pfeil-Buttons** über dem Feed: zum vorherigen oder nächsten Tag.
- Link **Heute**: springt zurück auf den aktuellen Tag.

### Schichtfilter

Unter der Datumsnavigation stehen **TimeFilter-Tabs** (z. B. „Frühdienst", „Spätdienst", „Nachtdienst" — konfigurierbar durch die Administration). Beim Öffnen des Zeitstroms wird die Schicht, die zur aktuellen Uhrzeit passt, automatisch vorausgewählt; Nachtschichten mit Mitternachts-Überlappung werden korrekt behandelt.

Wenn ein Schichtfilter aktiv ist, erscheint oberhalb des Feeds ein aufklappbarer **Schichtübergabe-Block** mit Statistiken (Anzahl Kontakte, neue Aufgaben, neue Personen) und Highlights (Krisen-Ereignisse, neue Hausverbote, dringende Aufgaben).

### Feed filtern

Direkt über dem Feed gibt es zwei Dropdowns:

- **Typ**: Alle · Events · Aktivitäten · Aufgaben · Hausverbote
- **Dokumentationstyp**: nur Einträge eines bestimmten Typs (z. B. „Kontakt", „Krisengespräch") — Sie sehen nur die Typen, die Ihre Rolle einsehen darf

Filteränderungen laden nur den Feed neu (HTMX), ohne die ganze Seite zu aktualisieren.

### Hausverbot-Banner

Wenn aktive Hausverbote in Ihrer Einrichtung bestehen, werden diese als rotes Banner unterhalb der Überschrift angezeigt.

---

## 2a. Arbeitszentrale

Neben dem Zeitstrom gibt es die **Arbeitszentrale** (`/start/`) — eine rollenspezifische Übersichtsseite, die bestehende Daten zu kompakten Kacheln verdichtet. Sie erreichen sie über den Sidebar-Link **„Arbeitszentrale"** (oberhalb des Zeitstroms). Der Zeitstrom unter `/` bleibt unverändert — die Arbeitszentrale ist ein zusätzlicher Einstieg, keine Verdrängung.

Je nach Rolle erscheint eine andere Variante:

**Fachkraft / Assistenz** — Titel „Arbeitszentrale", heutige Aufgaben und Kontakte auf einen Blick:

- **Heutige Kontakte** — Anzahl der heute erfassten Ereignisse (führt zum Zeitstrom)
- **Meine Aufgaben** — eigene offene bzw. in Bearbeitung befindliche Aufgaben (führt zum Aufgaben-Posteingang)
- **Zuletzt bearbeitet** — Anzahl kürzlich aktualisierter Personen (führt zur Personenliste)
- darunter die Listen **„Meine offenen Aufgaben"** (bis zu 5, mit Fälligkeit) und **„Zuletzt bearbeitete Personen"** (bis zu 5)

**Leitung** — Titel „Leitungs-Arbeitszentrale", DSGVO-Workflows, Retention und Statistik:

- **Löschanträge** — ausstehende Anträge (führt zur Löschantrags-Liste)
- **Löschvorschläge** — ausstehende Retention-Vorschläge (führt zum Retention-Dashboard)
- **Legal Holds** — aktive Aufbewahrungssperren
- **Statistik** — Monat/Jahr des letzten Statistik-Snapshots

**Facility-Admin** — Titel „Admin-Arbeitszentrale", Benutzer, Sicherheit und Konfiguration:

- **Benutzer ohne MFA** — aktive Benutzer ohne bestätigte 2FA (orange hervorgehoben, sobald > 0)
- **Konfigurations-Warnungen** — z. B. MFA nicht einrichtungsweit erzwungen oder K-Anonymisierung deaktiviert; die einzelnen Hinweise stehen darunter als Liste

**Super-Admin** — Titel „System-Arbeitszentrale", Cross-Facility-Status und Audit-Aktivität:

- **Mandanten** — Anzahl Einrichtungen
- **Benutzer aktiv** — über alle Mandanten
- **Audit-Events 24h** — Audit-Einträge der letzten 24 Stunden
- **Kritische Events** — sicherheitsrelevante Aktionen der letzten 24 Stunden (fehlgeschlagene Logins, Sicherheitsverstöße, Löschungen, genehmigte Löschanträge; orange hervorgehoben, sobald > 0)

---

## 3. Kontakt dokumentieren (Event erstellen)

Ein **Event** ist ein einzelner dokumentierter Kontakt – z. B. ein Beratungsgespräch, eine Spritzentauschausgabe oder ein anonymer Besuch.

### Neuen Kontakt erfassen

1. Klicken Sie in der Sidebar auf **Neu** und wählen Sie **Neuer Kontakt** (oder navigieren Sie zu `/events/new/`).
2. **Dokumentationstyp wählen:** Wählen Sie aus der Liste den passenden Typ (z. B. „Kontakt", „Krisengespräch", „Spritzentausch"). Die verfügbaren Typen sind von Ihrer Einrichtung konfiguriert.
3. **Felder ausfüllen:** Nach der Typauswahl werden die zugehörigen Eingabefelder geladen. Füllen Sie alle relevanten Felder aus.
4. **Zeitpunkt:** Das Feld „Zeitpunkt" ist automatisch auf die aktuelle Uhrzeit gesetzt. Sie können es anpassen, wenn Sie einen Kontakt nachträglich erfassen.
5. **Person zuordnen (optional):**
 - Für einen **anonymen Kontakt** (ohne Pseudonym): Aktivieren Sie die Option „Anonym". Es wird keine Person verknüpft.
 - Für eine **Person mit Pseudonym**: Beginnen Sie im Personen-Feld mit der Eingabe des Pseudonyms. Es erscheint eine Vorschlagsliste – wählen Sie die passende Person aus.
 - Wenn die Person noch nicht erfasst ist, legen Sie sie zuerst unter **Personen** an (siehe [Abschnitt 4](#4-personen-verwalten)).
6. Klicken Sie auf **Speichern**.

Sie werden zur Detailansicht des neu erstellten Eintrags weitergeleitet. Eine Erfolgsmeldung bestätigt die Speicherung.

> **Tipp:** Wenn Sie von der Detailseite der Person aus einen Kontakt erfassen, ist die Person bereits vorausgefüllt.

### Schnell-Vorlagen (Quick-Templates)

Wenn Ihre Administration für wiederkehrende Dokumentationsmuster **Schnell-Vorlagen** eingerichtet hat (z. B. „Beratungsgespräch 30 Min", „Standard-Check-in"), erscheinen diese oben auf der Seite „Neuer Kontakt" als Buttons.

- **Klick auf einen Button** füllt das Formular mit den hinterlegten Werten und dem passenden Dokumentationstyp vor.
- **Alle Felder bleiben bearbeitbar** — die Vorlage liefert nur Defaults, keine Sperre.
- **Bereits ausgefüllte Felder werden nicht überschrieben** — die Vorlage füllt nur leere Felder.
- **Rollen-Filter:** Sie sehen nur Vorlagen, deren Dokumentationstyp Sie laut Ihrer Rolle einsehen dürfen. Assistenzen sehen daher keine Vorlagen für erhöhte/hohe Sensitivität.
- **Selbstheilend:** Wurde nach Anlage einer Vorlage eine Auswahl-Option deaktiviert, wird der betroffene Wert beim Anwenden stillschweigend verworfen — Sie wählen dann einfach einen aktuell gültigen Wert.

> **Hinweis:** Vorlagen werden von Administratoren im Django-Admin-Bereich angelegt und gepflegt. Wenn Sie feststellen, dass Sie dasselbe Dokumentationsmuster immer wieder tippen, bitten Sie die Administration, eine Schnell-Vorlage dafür zu hinterlegen.

### Dateianhänge

An jedes Ereignis kann eine Datei angehängt werden – z. B. ein eingescanntes Formular, ein Foto eines Dokuments oder ein PDF.

- **Hochladen:** Im Kontakt-Formular gibt es ein Feld für den Datei-Upload. Wählen Sie eine Datei von Ihrem Gerät aus und speichern Sie das Ereignis wie gewohnt.
- **Virenscan:** Vor dem Speichern wird jede Datei automatisch durch einen Virenscanner (ClamAV) geprüft. Infizierte Dateien werden abgelehnt — Sie erhalten eine Fehlermeldung und das Ereignis wird nicht gespeichert.
- **Verschlüsselung:** Alle Anhänge werden verschlüsselt gespeichert (AES-GCM). Das bedeutet: Die Datei ist auf dem Server nicht im Klartext lesbar, sondern nur über Anlaufstelle selbst abrufbar.
- **Maximale Größe:** Standardmäßig sind **bis zu 10 MiB pro Datei** erlaubt. Ihre Administration kann diesen Wert anpassen.
- **Unterstützte Formate:** PDF, Office-Dokumente und Bilder. Die genaue, für Ihre Einrichtung erlaubte Liste erfragen Sie bitte bei Ihrer Administration.
- **Herunterladen:** Öffnen Sie die Detailansicht des Ereignisses und klicken Sie auf den Datei-Link. Die Datei wird beim Abruf automatisch entschlüsselt und im Browser ausgeliefert.
- **Ersetzen (mit Versionshistorie):** Beim Bearbeiten eines Ereignisses können Sie die bestehende Datei durch eine neue ersetzen. Die alte Datei wird **nicht** gelöscht — sie bleibt als **Vorversion** erhalten und ist in der Ereignis-Detailansicht über ein aufklappbares Akkordeon weiterhin herunterladbar. Vorversionen werden erst beim vollständigen Löschen des Ereignisses entfernt.

> **Offline-Hinweis:** Ereignisse mit Datei-Anhängen können aktuell **nicht offline** gespeichert werden. Wenn Sie offline arbeiten, erscheint beim Anhängen einer Datei ein deutlicher Hinweis. Siehe [Abschnitt 8](#8-pwa-installieren-und-offline-arbeiten).

> **Zentrale Dateiübersicht:** Alle Dateianhänge Ihrer Einrichtung lassen sich über die **Dateien-Seite** in einer Liste durchsuchen und filtern — siehe [Abschnitt 3a: Dateien-Übersicht](#3a-dateien-übersicht).

### Kontakt bearbeiten

1. Öffnen Sie das Ereignis (über den Zeitstrom oder die Chronik der Person).
2. Klicken Sie auf **Bearbeiten**.
3. Ändern Sie die gewünschten Felder und klicken Sie auf **Speichern**.

> **Hinweis:** Bearbeitungen werden in der Änderungshistorie des Ereignisses protokolliert. Frühere Versionen bleiben im Verlauf sichtbar.

> **Gleichzeitige Bearbeitung:** Wenn jemand anderes den gleichen Datensatz gleichzeitig bearbeitet hat, erscheint eine Fehlermeldung. Laden Sie die Seite neu und tragen Sie Ihre Änderung erneut ein — so wird sichergestellt, dass keine Änderungen still überschrieben werden.

### Kontakt löschen

1. Öffnen Sie das Ereignis.
2. Klicken Sie auf **Löschen**.
3. Geben Sie eine Begründung ein.
4. Bestätigen Sie die Löschung.

> **Wichtig:** Wenn der Kontakt einer **Person mit qualifizierter Dokumentation** zugeordnet ist, wird kein sofortiges Löschen durchgeführt. Stattdessen wird automatisch ein **Löschantrag** gestellt, der von einer Leitung oder Administration genehmigt werden muss (4-Augen-Prinzip). Sie erhalten eine entsprechende Rückmeldung.

---

## 3a. Dateien-Übersicht

Die **Dateien-Seite** (`/attachments/`, Sidebar-Eintrag **Dateien**) zeigt alle Anhänge Ihrer Einrichtung in einer durchsuchbaren Liste — ohne vorher das jeweilige Ereignis öffnen zu müssen.

### Zugriff

- **Sidebar-Navigation → Dateien**
- Verfügbar für Assistenzen, Fachkräfte, Leitungen und Administratoren.

### Sichtbarkeit

Sie sehen nur Dateien, deren Ereignis **und** Feld Ihre Sensitivitätsrolle erreicht:

- Felder mit „hoher" Sensitivität bleiben für Fachkräfte einer niedrigeren Stufe verborgen — gleiches Gate wie im Ereignis-Detail.
- Anhänge gelöschter Ereignisse sind nicht enthalten.
- Facility-Scoping: Sie sehen ausschließlich Dateien Ihrer eigenen Einrichtung.

### Filter

Zwei Filter oberhalb der Tabelle:

- **Dokumentationstyp** — nur Anhänge zu Ereignissen eines bestimmten Typs (z. B. „Beratungsgespräch").
- **Personen** — alle Anhänge zu einem bestimmten Pseudonym.

Die Filter sind kombinierbar und aktualisieren die Liste via HTMX ohne kompletten Seitenreload.

### Herunterladen

Jede Zeile zeigt den Originaldateinamen. Ein Klick darauf liefert die entschlüsselte Datei — exakt wie in der Ereignis-Detailansicht. Downloads werden im Audit-Log protokolliert.

### Verschlüsselung

Alle Dateien liegen auf dem Server verschlüsselt (AES-GCM). Entschlüsselt wird erst beim Download-Request im Django-Prozess; direkt auf die Festplatte zugreifende Admins sehen nur die verschlüsselten Binärdaten.

### Vorversionen

Ersetzte Dateien bleiben als Vorversionen erhalten (siehe [Abschnitt 3 – Dateianhänge](#dateianhänge)). Die Dateien-Übersicht zeigt aktuell nur die jeweils aktuelle Version eines Eintrags; Vorversionen sind über den Eintrag in der Ereignis-Detailseite einsehbar.

---

## 4. Personen verwalten

Personen werden in Anlaufstelle **ausschließlich mit Pseudonymen** erfasst – keine Klarnamen. Das Pseudonym ist der primäre Identifikator.

### Kontaktstufen

Jede Person hat eine **Kontaktstufe**:

| Stufe | Beschreibung |
|---|---|
| **Identifiziert** | Pseudonym bekannt, grundlegende Daten (Altersgruppe) vorhanden |
| **Qualifiziert** | Zusätzliche persönliche Daten vorhanden (eingeschränkter Zugriff, Audit-Log) |

> **Hinweis:** Der Zugriff auf Personenprofile in qualifizierter Kontaktstufe wird automatisch im Audit-Log protokolliert.

### Neue Person anlegen

1. Navigieren Sie zu **Personen** (`/clients/`) und klicken Sie auf **Neue Person** (oder direkt zu `/clients/new/`).
2. Füllen Sie das Formular aus:
 - **Pseudonym:** Einzigartiger Name innerhalb Ihrer Einrichtung (z. B. ein selbstgewählter Spitzname). Das Pseudonym darf innerhalb einer Einrichtung nur einmal vorkommen.
 - **Kontaktstufe:** Wählen Sie „Identifiziert" oder „Qualifiziert".
 - **Altersgruppe:** „Unter 18", „18–26", „27+" oder „Unbekannt".
 - **Notizen:** Interne Anmerkungen zur Person (optional).
3. Klicken Sie auf **Speichern**.

### Personen suchen

1. Navigieren Sie zu **Personen** (`/clients/`).
2. Geben Sie im Suchfeld ein Teil des Pseudonyms ein. Die Liste filtert sich automatisch.
3. Optional können Sie zusätzlich nach **Kontaktstufe** oder **Altersgruppe** filtern.
4. Klicken Sie auf einen Eintrag, um die Detailansicht zu öffnen.

> **Tipp:** Die Schnellsuche über das globale Suchfeld (Lupe oben) findet Personen und Ereignisse gleichzeitig.

### Detailseite der Person und Chronik

Die Detailseite einer Person (`/clients/<id>/`) zeigt:

- **Stammdaten:** Pseudonym, Kontaktstufe, Altersgruppe, Notizen
- **Chronik:** Alle bisherigen dokumentierten Kontakte dieser Person, neueste zuerst
- **Offene WorkItems:** Aufgaben und Hinweise, die dieser Person zugeordnet sind

Von der Detailseite aus können Sie direkt einen neuen Kontakt für diese Person erfassen oder eine neue Aufgabe anlegen.

### Person bearbeiten

1. Öffnen Sie die Detailseite der Person.
2. Klicken Sie auf **Bearbeiten**.
3. Ändern Sie die gewünschten Felder (z. B. Kontaktstufe hochsetzen).
4. Klicken Sie auf **Speichern**.

> **Hinweis:** Eine Änderung der Kontaktstufe wird automatisch im Audit-Log protokolliert.

> **Gleichzeitige Bearbeitung:** Wenn jemand anderes den gleichen Datensatz gleichzeitig bearbeitet hat, erscheint eine Fehlermeldung. Laden Sie die Seite neu und tragen Sie Ihre Änderung erneut ein — so wird sichergestellt, dass keine Änderungen still überschrieben werden.

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
 - **Person:** Falls die Aufgabe eine bestimmte Person betrifft (optional)
3. Klicken Sie auf **Speichern**.

> **Tipp:** Wenn Sie von der Detailseite der Person aus eine neue Aufgabe erstellen, ist die Person bereits vorausgefüllt.

### Aufgabe bearbeiten

1. Öffnen Sie die Aufgabe per Klick auf den Titel.
2. Auf der Detailseite klicken Sie auf **Bearbeiten**.
3. Ändern Sie die gewünschten Felder und speichern Sie.

> **Gleichzeitige Bearbeitung:** Wenn jemand anderes den gleichen Datensatz gleichzeitig bearbeitet hat, erscheint eine Fehlermeldung. Laden Sie die Seite neu und tragen Sie Ihre Änderung erneut ein — so wird sichergestellt, dass keine Änderungen still überschrieben werden.

### Filter: Mir zugewiesen

In der Inbox-Ansicht können Sie den Filter **„Mir zugewiesen"** aktivieren. Dann sehen Sie ausschließlich Aufgaben und Hinweise, die Ihnen persönlich zugewiesen sind — allgemeine (nicht zugewiesene) Einträge und Einträge für andere Personen werden ausgeblendet.

Der Filter ist hilfreich, wenn Sie sich einen klaren Überblick über Ihre eigenen offenen Punkte verschaffen möchten, ohne von der gesamten Teamliste abgelenkt zu werden.

### Bulk-Edit

Wenn Sie mehrere Aufgaben gleichzeitig bearbeiten möchten, nutzen Sie den Bulk-Edit-Modus:

1. Wählen Sie in der Inbox die gewünschten Aufgaben über die **Checkbox** links neben jeder Karte aus.
2. Oben erscheint ein **Bulk-Dropdown** mit den verfügbaren Aktionen.
3. Ändern Sie zentral **Status**, **Priorität** oder **Zuweisung** für alle ausgewählten Einträge auf einmal.
4. Bestätigen Sie die Änderung — alle markierten Einträge werden gemeinsam aktualisiert.

### Erinnerung vs. Frist

Aufgaben kennen zwei unterschiedliche Zeit-Felder:

| Feld | Bedeutung |
|---|---|
| **Frist (due_date)** | Fälligkeitstermin — wann die Aufgabe spätestens erledigt sein muss |
| **Erinnerung (remind_at)** | Zeitpunkt, zu dem Sie benachrichtigt werden möchten — üblicherweise vor der Frist |

**Beispiel:** Die Frist ist der 15.04., die Erinnerung setzen Sie auf den 10.04. — so bekommen Sie fünf Tage vorher einen Hinweis und laufen nicht in den Terminstress am Fälligkeitstag.

Beide Felder sind optional. Sie können eine Aufgabe auch nur mit Frist oder nur mit Erinnerung anlegen.

### Wiederkehrende Fristen

Für wiederkehrende Aufgaben (z. B. monatliche Beratungsgespräche, wöchentliche Check-ins) können Sie einen **Wiederholungs-Rhythmus** hinterlegen (z. B. wöchentlich, monatlich).

Sobald Sie eine solche Aufgabe auf Status **„Erledigt"** setzen, wird automatisch eine **Folge-Aufgabe** mit demselben Titel und neuem Termin nach dem gewählten Rhythmus erzeugt. So müssen Sie die Aufgabe nicht manuell jedes Mal neu anlegen.

---

## 6. Suche

Die **Suche** durchsucht gleichzeitig Personen (nach Pseudonym) und Ereignisse (nach Personen-Pseudonym und Inhaltsfeldern). Sie ist auf zwei Wegen erreichbar:

### Globale Suche (Schnellsuche)

Das Suchfeld ist **permanent in der Sidebar** sichtbar (Desktop). Auf dem Smartphone öffnet sich über das Such-Icon in der unteren Navigation ein Overlay.

1. Beginnen Sie mit der Eingabe im Suchfeld. Nach einer kurzen Verzögerung erscheinen Ergebnisse als Dropdown.
2. Es werden maximal **5 Personen** und **5 Ereignisse** angezeigt.
3. Klicken Sie auf einen Treffer, um direkt zur Detailseite zu springen.
4. Über **„Alle Ergebnisse anzeigen"** gelangen Sie zur vollständigen Suchseite.

> **Tipp:** Mit der Escape-Taste schließen Sie das Such-Dropdown.

### Vollständige Suchseite

Für umfangreichere Recherchen steht weiterhin die Suchseite unter `/search/` zur Verfügung. Sie zeigt alle Treffer (bis zu 20 Personen und 20 Ereignisse) und ist auch über den Link „Alle Ergebnisse anzeigen" in der Schnellsuche erreichbar.

> **Hinweis:** Felder, die als verschlüsselt konfiguriert sind, werden in der Suche nicht durchsucht.

### Tippfehler-tolerante Suche (Fuzzy)

Die Suche findet auch Treffer, wenn Sie sich vertippen oder ein Name in einer ähnlichen Schreibweise hinterlegt ist. Beispiele:

- Die Eingabe **„Muller"** findet auch **„Müller"**.
- Die Eingabe **„Tomas"** findet auch **„Thomas"**.

Technisch basiert das auf einer Trigramm-Ähnlichkeit der PostgreSQL-Datenbank (pg_trgm) — Sie müssen sich darum nicht kümmern.

**Ähnlichkeits-Schwelle:** Pro Einrichtung kann Ihre Administration einstellen, wie „streng" die Fuzzy-Suche arbeitet (Wertebereich 0.0–1.0, Standard ca. 0.3). Ein **niedrigerer Wert** liefert mehr Treffer, aber auch mehr falsche Vorschläge; ein **höherer Wert** ist strenger und zeigt nur sehr ähnliche Begriffe.

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
3. Das Dashboard aktualisiert sich automatisch und zeigt aggregierte Kennzahlen zu Kontaktzahlen, Dokumentationstypen und Personengruppen.

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

### Beispiel-Sachbericht

Der Beispiel-Sachbericht erstellt einen strukturierten Sachbericht nach einem festen Beispiel-Layout. Er ist ein anpassbares Beispiel bzw. eine Vorlage — kein verbindlicher oder aktueller Behördennachweis und keine Garantie für landesspezifische Anforderungen.

1. Wählen Sie im Statistik-Dashboard den Zeitraum.
2. Klicken Sie auf **Beispiel-Sachbericht**.
3. Die PDF-Datei wird heruntergeladen (Dateiname: `jugendamt_YYYY-MM-DD_YYYY-MM-DD.pdf`).

> **Tipp:** Für Halbjahresberichte wählen Sie den Zeitraum „Letztes Halbjahr" und passen Sie Start- und Enddatum bei Bedarf manuell auf den 01.01. bzw. 30.06. (oder 01.07. – 31.12.) an.

### Datenschutzfreundliche externe Berichte (Leitung / Admin)

Für die Weitergabe an externe Stellen (z. B. Fördermittelgeber oder Kommune) gibt es unter `/statistics/external/` einen **externen Bericht mit Datenschutzprofil**. Er nutzt dieselben Zeitraum-Filter wie das Statistik-Dashboard, ist aber bewusst datensparsam:

- **Kein Pseudonym-Ranking** — die Top-Personen-Liste des internen Dashboards entfällt vollständig.
- **K-Anonymität** — Aggregate mit weniger als *k* Datensätzen (Schwelle aus den Einrichtungs-Settings, Standard 5) werden als **„unterdrückt"** ausgewiesen statt mit konkreter Zahl. Das betrifft „Unterschiedliche Personen", „Nach Dokumentationstyp" und „Nach Altersgruppe".
- **Datenschutzprofil-Kopf** — Einrichtung, Profil (`external`), Zeitraum, K-Anonymität-Schwelle und Erzeugungs-Zeitpunkt stehen oben im Bericht.

Über **„Als JSON exportieren"** (bzw. `?format=json` an der URL) erhalten Sie dieselben Daten maschinenlesbar. Jeder Aufruf wird als `EXPORT` im Audit-Log protokolliert. Der externe Bericht ist nur für **Leitung** und **Administration** zugänglich.

### Löschanträge prüfen (Leitung / Admin)

Wenn eine Fachkraft oder Assistenz einen Kontakt einer Person mit qualifizierter Dokumentation löschen möchte, entsteht ein **Löschantrag**, der von einer Leitung oder Administration genehmigt werden muss.

1. Offene Löschanträge sehen Sie unter `/deletion-requests/`.
2. Klicken Sie auf einen Antrag, um Details und das betroffene Ereignis einzusehen.
3. Klicken Sie auf **Genehmigen** oder **Ablehnen** und bestätigen Sie.

> **Wichtig:** Sie können Ihren eigenen Löschantrag nicht selbst genehmigen (4-Augen-Prinzip).

---

## 8. PWA installieren und Offline arbeiten

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

> **Hinweis:** Die App ist eine **Progressive Web App (PWA)**. Für den Streetwork-Einsatz steht jetzt ein **Offline-Modus** zur Verfügung (siehe unten) — Sie können Ereignisse auch ohne Internetverbindung erfassen und später synchronisieren.

> **Firefox (Android):** Firefox bietet zwar "Installieren" an, die App öffnet sich aber immer mit Adressleiste. Für den echten App-Modus ohne Adressleiste verwenden Sie **Chrome, Edge oder Samsung Internet** (Android) bzw. **Safari** (iOS).

### Offline-Erfassung (Streetwork)

Für Einsätze ohne verlässliche Internetverbindung — etwa bei aufsuchender Arbeit — können Sie Anlaufstelle im Offline-Modus nutzen.

**Vor dem Einsatz (online):**

1. Öffnen Sie die Personenliste.
2. Tippen Sie bei jeder gewünschten Person in der Spalte **„Offline"** auf **„Mitnehmen"** — oder auf der Detailseite der Person auf **„Offline mitnehmen"**. Pseudonym, Stammdaten und die letzten Ereignisse werden dann verschlüsselt für die Offline-Nutzung auf Ihr Gerät geladen; ein Badge **„Lokal verfügbar"** bestätigt es. Maximal 20 Personen gleichzeitig.
3. Der **Offline-Arbeitsplatz** unter **`/offline/`** listet jederzeit Ihre mitgenommenen Personen und dient auch als Einstieg, wenn Sie die App ohne Netz (bzw. als installierte App) öffnen.

**Während des Einsatzes (offline):**

- Öffnen Sie eine mitgenommene Person wie gewohnt über ihre Detailseite — die Ansicht bleibt an der **normalen Adresse** und wird aus dem verschlüsselten Offline-Speicher aufgebaut (kein separater „Offline"-Link mehr).
- Sie können **neue Ereignisse erfassen** (über **„Neuer Kontakt"**) und **bestehende bearbeiten**; die Einträge werden verschlüsselt lokal gespeichert (AES-GCM-256; der Schlüssel wird aus Ihrem Passwort abgeleitet) und bei Verbindung automatisch gesendet. **Datei-Anhänge** können offline nicht erfasst werden — reichen Sie sie nach, sobald Sie online sind.
- Die Offline-Erfassung (Kontakte **und Aufgaben**) findet **im Dossier der jeweiligen Person** statt — also bei einer Person, die Sie vorher „Offline mitgenommen" haben. Öffnen Sie die allgemeinen Formulare („Neuer Kontakt"/„Neue Aufgabe" aus der Navigation) ohne Netz, landen Sie auf dem Offline-Arbeitsplatz mit einem entsprechenden Wegweiser (#1483).
- **Kontakte ohne Personenbezug** (anonyme Kontakte) lassen sich offline **nicht neu beginnen**. Ausnahme: War das Kontakt-Formular beim Verbindungsabriss bereits geöffnet, wird der abgeschickte Eintrag verschlüsselt zwischengespeichert und automatisch nachgesendet. Ein eigener Offline-Einstieg dafür ist eine offene Produktentscheidung (#1485).
- In der Oberfläche sehen Sie einen Hinweis, dass Sie offline arbeiten und wie viele Einträge noch auf die Synchronisation warten.

**Zurück im Netz:**

- Sobald das Gerät wieder online ist, wird die Warteschlange **automatisch synchronisiert**. Die offline erfassten Ereignisse landen auf dem Server und sind dort für das Team sichtbar.

**Konflikte auflösen:**

Wurde ein Ereignis gleichzeitig online (durch jemand anderen) und offline (durch Sie) bearbeitet, zeigt Anlaufstelle beim Synchronisieren einen **Side-by-Side-Diff** mit drei Auswahlmöglichkeiten:

- **Meine Version übernehmen** — Ihre offline erfasste Änderung gewinnt.
- **Server-Version übernehmen** — die Online-Änderung gewinnt, Ihre offline-Version wird verworfen.
- **Manuell zusammenführen** — Sie entscheiden Feld für Feld, welche Inhalte übernommen werden.

**Konflikte und nicht übertragbare Einträge verwalten:**

Unter **`/offline/conflicts/`** sehen Sie alle Einträge, die eine Entscheidung erfordern — die Seite öffnet sich seit #1396 auch ohne Netzverbindung. Zwei Bereiche:

- **„Wartet auf Entscheidung"** — offene Konflikte; ein Klick führt zum Side-by-Side-Diff oben.
- **„Nicht übertragbar"** — Einträge, die dauerhaft nicht gesendet werden konnten, jeweils mit Grund (z. B. auf dem Server gelöscht, Zugriff entzogen, vom Server als ungültig abgelehnt, keine Berechtigung). Sie können diese Einträge **erneut versuchen** oder **verwerfen** — bei nicht übertragbaren Ereignissen zusätzlich vorher **exportieren**, um den Inhalt nicht zu verlieren.

> **Wichtig — Datenverlust vermeiden:** **Logout** und **Passwort-Änderung** löschen die lokal gespeicherten Offline-Daten — synchronisieren Sie daher **immer zuerst**, bevor Sie sich abmelden oder Ihr Passwort ändern. Bei längerer Inaktivität wird der Offline-Zugriff aus Sicherheitsgründen **gesperrt**; nach erneuter Anmeldung sind noch nicht synchronisierte Einträge wieder verfügbar. Das bloße **Schließen des Tabs** löscht dagegen nichts. Haben Sie beim Passwort-Ändern noch nicht synchronisierte Einträge, warnt Anlaufstelle Sie vor dem Absenden mit der Anzahl der betroffenen Einträge.

> **Keine Datei-Anhänge offline:** Ereignisse mit Datei-Anhängen können offline **nicht** gespeichert werden. Aus Sicherheitsgründen werden keine unverschlüsselten Datei-Blobs im Browser abgelegt. Erfassen Sie in diesem Fall zuerst das Ereignis ohne Datei und hängen Sie die Datei nach, sobald Sie wieder online sind.

---

## 9. Rollen und Berechtigungen

Anlaufstelle unterscheidet fünf Rollen. Ihre Rolle wird von der Anwendungsbetreuung Ihrer Einrichtung festgelegt.

> **Zielgruppe dieses Abschnitts:** die **praktische** Sicht — was Sie im Arbeitsalltag mit Ihrer Rolle tun dürfen. Die fachliche Begründung des Modells steht im [Fachkonzept (Role — Die Rolle)](fachkonzept-anlaufstelle.md#role--die-rolle), die technischen Details (DB-Werte, RLS-Scoping) in der [FAQ § C](faq.md#die-fünf-rollen), das Anlegen und Verwalten von Rollen im [Admin-Guide § 2.4](admin-guide.md#rollenbeschreibung).

### Rollenübersicht

| Rolle | Anzeigename | Kurzbeschreibung |
|---|---|---|
| `assistant` | Assistenz | Eingeschränkte Erfassung ohne Zugriff auf qualifizierte Personendaten |
| `staff` | Fachkraft | Standardrolle für Mitarbeitende in der Dokumentation |
| `lead` | Leitung | Alle Fachkraft-Funktionen plus Auswertungen und Leitungsaufgaben |
| `facility_admin` | Anwendungsbetreuung | Vollzugriff auf alle Bereiche und Einstellungen **Ihrer Einrichtung** (Audit-Log, DSGVO-Paket, Benutzerverwaltung) |
| `super_admin` | Systemadministration | Träger-/Hosting-Konzept: facility-übergreifend, Bootstrap-Tools, getrennter `/system/`-Bereich. **In der normalen Fach-UI nicht sichtbar** — Endnutzer interagieren nicht mit ihm. |

> **Hinweis:** Die Systemadministration (`super_admin`) ist eine Hosting-/Träger-Rolle. Sie hostet die Installation und legt die erste Einrichtung sowie die erste Anwendungsbetreuung an. Im fachlichen Tagesgeschäft begegnen Sie ihr in der Regel nicht — sie arbeitet ausschließlich im `/system/`-Bereich, der Ihnen als facility-gebundener User nicht zugänglich ist.

### Was darf wer?

Tabelle nur für die vier facility-gebundenen Rollen — `super_admin` ist hier ausgeklammert, weil er nicht im Fach-UI arbeitet:

| Funktion | Assistenz | Fachkraft | Leitung | Anwendungsbetreuung |
|---|---|---|---|---|
| Zeitstrom / Startseite einsehen | Ja | Ja | Ja | Ja |
| Anonyme Kontakte dokumentieren | Ja | Ja | Ja | Ja |
| Identifizierte Kontakte dokumentieren | Ja | Ja | Ja | Ja |
| Personen anlegen und suchen | Ja | Ja | Ja | Ja |
| Qualifizierte Personendaten einsehen | Nein | Ja (eigene Einrichtung) | Ja | Ja |
| Eigene Ereignisse bearbeiten | Ja | Ja | Ja | Ja |
| Fremde Ereignisse bearbeiten | Nein | Ja | Ja | Ja |
| WorkItems erstellen und bearbeiten | Ja | Ja | Ja | Ja |
| Suche verwenden | Ja | Ja | Ja | Ja |
| Statistik und Export | Nein | Nein | Ja | Ja |
| Personendaten exportieren (Art. 15 / 20 DSGVO) | Nein | Nein | Ja | Ja |
| DSGVO-Dokumentationspaket herunterladen | Nein | Nein | Nein | Ja |
| Löschanträge stellen | Ja | Ja | Ja | Ja |
| Löschanträge genehmigen | Nein | Nein | Ja | Ja |
| Pseudonyme verwalten / Kontaktstufe ändern | Nein | Nein | Ja | Ja |
| Audit-Log einsehen (eigene Einrichtung) | Nein | Nein | Nein | Ja |
| Fallmanagement (Fälle, Episoden, Ziele) | Nein | Ja | Ja | Ja |
| Fälle schließen / wiedereröffnen | Nein | Nein | Ja | Ja |
| Benutzer und Einstellungen verwalten | Nein | Nein | Nein | Ja |

> **Hinweis:** Der Zugriff ist immer auf die eigene Einrichtung beschränkt. Mitarbeitende einer Einrichtung können keine Daten anderer Einrichtungen derselben Organisation sehen — auch die Anwendungsbetreuung (`facility_admin`) sieht nur die eigene Einrichtung.

### Audit-Log (nur Anwendungsbetreuung)

Das Audit-Log (`/audit/`) protokolliert automatisch sicherheitsrelevante Aktionen: Anmeldungen, Zugriffe auf qualifizierte Daten, Exporte, Löschungen und Stufenwechsel. Es kann nicht verändert werden und dient der Nachvollziehbarkeit im Sinne der DSGVO. Zugriff hat die Anwendungsbetreuung (`facility_admin`) für die eigene Einrichtung.

---

## 10. Fallmanagement

Nicht jeder Kontakt mit einer Person steht für sich allein. Wenn die Zusammenarbeit mit einer Person über einen längeren Zeitraum läuft – z. B. ein Beratungsprozess, eine Krisenbegleitung oder eine Wohnungsvermittlung – können Sie diese Arbeit in einem **Fall** bündeln.

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
 - **Person:** Beginnen Sie mit der Eingabe des Pseudonyms – es erscheint eine Vorschlagsliste. Wählen Sie die passende Person aus. Ein Fall kann auch ohne Person erstellt werden.
 - **Beschreibung:** Ausführlichere Informationen zum Fall (optional).
 - **Fallverantwortlich:** Wählen Sie die zuständige Person aus dem Dropdown (optional). Nur Fachkräfte, Leitungen und Administratoren Ihrer Einrichtung stehen zur Auswahl.
3. Klicken Sie auf **Fall erstellen**.

Sie werden zur Detailseite des neuen Falls weitergeleitet.

> **Tipp:** Wenn Sie von der Detailseite der Person aus einen neuen Fall erstellen, ist die Person bereits vorausgefüllt.

### Fall-Detailseite

Die Detailseite eines Falls (`/cases/<id>/`) gliedert sich in drei Bereiche:

**Kopfbereich:**
- Titel und Status-Badge (Offen / Geschlossen)
- Link zur zugehörigen Person
- Fallverantwortlicher, Erstellt am, Erstellt von
- Beschreibung (falls vorhanden)
- Buttons: **Bearbeiten**, **Schließen** bzw. **Wiedereröffnen**

**Linke Spalte – Kontakte:**
- Alle dem Fall zugeordneten Kontakte (Events), chronologisch sortiert
- Möglichkeit, weitere Kontakte der Person zuzuordnen oder bestehende zu entfernen

**Rechte Spalte – Episoden und Wirkungsziele:**
- Liste der Episoden (Phasen innerhalb des Falls)
- Liste der Wirkungsziele mit Meilensteinen

### Fall bearbeiten

1. Öffnen Sie die Fall-Detailseite.
2. Klicken Sie auf **Bearbeiten**.
3. Ändern Sie Titel, Person, Beschreibung oder Fallverantwortlichen.
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

Auf der Fall-Detailseite sehen Sie unterhalb der zugeordneten Kontakte eine Liste der **nicht zugeordneten Kontakte** der Person. So ordnen Sie einen Kontakt zu:

1. Klicken Sie bei dem gewünschten Kontakt auf die Zuordnen-Schaltfläche.
2. Der Kontakt wird sofort in die Fallliste verschoben (ohne Seitenneuladen).

Um einen Kontakt wieder aus dem Fall zu entfernen:

1. Klicken Sie beim zugeordneten Kontakt auf **Entfernen**.
2. Der Kontakt wird aus dem Fall gelöst und erscheint wieder in der Liste der nicht zugeordneten Kontakte.

> **Hinweis:** Das Zuordnen und Entfernen ändert nur die Fall-Zugehörigkeit. Der Kontakt selbst bleibt unverändert erhalten.

### Episoden

Eine **Episode** ist eine abgrenzbare Phase innerhalb eines Falls. Wenn z. B. eine Person dreimal im Jahr in eine Krisensituation gerät, können diese als drei separate Episoden innerhalb desselben Falls dokumentiert werden.

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

| Funktion | Assistenz | Fachkraft | Leitung | Anwendungsbetreuung |
|---|---|---|---|---|
| Fallliste einsehen | Nein | Ja | Ja | Ja |
| Fall erstellen und bearbeiten | Nein | Ja | Ja | Ja |
| Fall schließen / wiedereröffnen | Nein | Nein | Ja | Ja |
| Kontakte zuordnen / entfernen | Nein | Ja | Ja | Ja |
| Episoden verwalten | Nein | Ja | Ja | Ja |
| Wirkungsziele und Meilensteine | Nein | Ja | Ja | Ja |

---

> **Weitere Fragen?** Die [FAQ](faq.md) beantwortet häufige Fragen zu Datenschutz, 2FA, Offline-Modus, Löschfristen und mehr.

---

*Anlaufstelle – Dokumentationssystem für niedrigschwellige soziale Hilfsangebote*

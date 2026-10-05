# Anlaufstelle – Benutzerhandbuch

Dieses Handbuch richtet sich an Fachkräfte, Leitungen und Assistenzen in offenen Sprechstunden und Beratungsstellen, Kontaktläden, Notschlafstellen, Awareness-Teams, Frauenhäusern, der Jugendarbeit und anderen niedrigschwelligen sozialen Einrichtungen, die mit Anlaufstelle arbeiten.

Dieses Handbuch und die [FAQ](faq.md) sind innerhalb der Anwendung unter „Hilfe" (`/hilfe/`) verlinkt (Refs #1904).

---

## Inhaltsverzeichnis

1. [Login und Passwort](#1-login-und-passwort)
2. [Startseite – Zeitstrom](#2-startseite--zeitstrom)
   - [Strichliste](#strichliste)
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

> **Konto gesperrt nach mehreren Fehlversuchen?** Nach **10 fehlgeschlagenen Anmeldungen** wird Ihr Konto automatisch gesperrt. Sie sehen dann eine entsprechende Hinweis-Seite und können sich nicht mehr anmelden. Auf der Login-Seite finden Sie drei Selbsthilfe-Wege (Refs #869):
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
5. Die angezeigten Backup-Codes sicher verwahren, die Pflicht-Checkbox **Ich habe die Codes sicher gespeichert.** aktivieren und bestätigen. Erst dieser Schritt schließt die Einrichtung ab. Wer die Seite vorher verlässt, beginnt beim nächsten Aufruf wieder unter `/mfa/setup/`.

> **Tipp:** Falls der QR-Code nicht scannbar ist, tippen Sie auf **Secret manuell eingeben** und übertragen die Zeichenkette in die App (Feld „Secret" / „Schlüssel" — Base32, ohne Leerzeichen). Wählen Sie in der App den Typ **TOTP / zeitbasiert**.

**Login mit 2FA:**

1. Benutzername und Passwort wie gewohnt eingeben.
2. Auf der folgenden Seite den aktuell in der App angezeigten 6-stelligen Code eintippen.
3. Der Code ist jeweils **30 Sekunden** gültig — falls er abläuft, einfach den nächsten nehmen.

Nach mehreren falschen Eingaben drosselt `django-otp` weitere Prüfungen zunehmend. Während dieser Wartezeit kann deshalb auch ein korrekter Code abgewiesen werden; warten Sie die Sperrfrist ab, statt weitere Codes direkt hintereinander einzugeben.

**2FA deaktivieren:**

Unter `/mfa/settings/` → **2FA deaktivieren**. Dies ist **nicht möglich**, wenn Ihre Einrichtung 2FA verpflichtend vorschreibt oder Ihr Konto individuell als 2FA-pflichtig markiert ist — in diesem Fall wenden Sie sich an Ihre Administration.

**Backup-Codes (für den Notfall):**

Bei der 2FA-Einrichtung erhalten Sie **10 einmalig nutzbare Backup-Codes**. Bewahren Sie diese sicher auf — z. B. ausgedruckt im Geldbeutel oder im Passwort-Manager. Falls Sie Ihr Telefon verlieren oder die Authenticator-App zurückgesetzt wurde, können Sie am 2FA-Login einen Backup-Code statt eines TOTP-Codes eingeben. Jeder Code funktioniert nur einmal.

> **Alle Codes verbraucht oder verloren?** Wenden Sie sich an Ihre Administration — sie kann Ihr TOTP-Gerät zurücksetzen, danach richten Sie 2FA und neue Backup-Codes ein.

**Passkey / Sicherheitsschlüssel (optional, zusätzlich zur Authenticator-App):**

Ergänzend zur Authenticator-App können Sie einen **Passkey** als zweiten Faktor hinterlegen — z. B. die Biometrie Ihres Geräts (Fingerabdruck/Gesicht), ein Smartphone oder einen USB-Sicherheitsschlüssel (FIDO2). Ein Passkey ist phishing-resistent, weil er an die Adresse der Anwendung gebunden ist.

1. Richten Sie **zuerst** die Authenticator-App (TOTP) ein — ein Passkey ist nur **zusätzlich** dazu möglich. So bleiben Ihre Backup-Codes der Wiederherstellungspfad, falls ein Passkey verloren geht.
2. Unter `/mfa/settings/` im Abschnitt **Passkeys** auf **Passkey hinzufügen** klicken und den Anweisungen des Browsers folgen.
3. Sie können mehrere Passkeys registrieren (z. B. Laptop und Smartphone) und einzelne wieder **entfernen** (verlangt eine erneute Bestätigung Ihres Passworts/2FA).

Beim Login bietet die 2FA-Seite dann **Mit Passkey anmelden** an — alternativ nutzen Sie weiterhin die Authenticator-App oder einen Backup-Code.

> **Hinweis:** Passkeys sind an die Web-Adresse gebunden. Ein auf `dev.anlaufstelle.app` registrierter Passkey funktioniert nicht auf einer anderen Adresse — registrieren Sie ihn dort, wo Sie die Anwendung tatsächlich nutzen.

### Abmelden

Klicken Sie oben rechts auf **Abmelden**. Aus Datenschutzgründen werden Sie nach einer konfigurierten Inaktivitätszeit automatisch abgemeldet (Standard: 30 Minuten).

### Schnell verlassen

Oben auf jeder Seite steht der Knopf **Schnell verlassen**. Er ist für Situationen gedacht, in denen jemand mitliest oder unerwartet neben Ihnen steht — am Empfangsplatz, im Frauenhaus, bei der Dokumentation an einem öffentlichen Ort.

Ein Klick bewirkt drei Dinge in dieser Reihenfolge:

1. Der Bildschirminhalt wird **sofort verdeckt**.
2. Sie werden **abgemeldet** — die Sitzung endet auch auf dem Server, nicht nur im Browser.
3. Der Browser wechselt auf eine **neutrale Seite** (welche, legt Ihre Einrichtung fest; Standard ist die Startseite von Wikipedia).

Dieselbe Aktion lösen zwei **Tastenkürzel** aus, auch aus einem Eingabefeld heraus:

| Kürzel | Hinweis |
|---|---|
| `Strg` + `Umschalt` + `X` | auf dem Mac `Cmd` + `Umschalt` + `X` |
| `Umschalt` + `Esc` | Alternative, wenn die erste Kombination belegt ist |

> **Ungespeicherte Eingaben gehen dabei verloren.** Das ist kein Sonderverhalten des Knopfs: Jede Abmeldung räumt die lokal zwischengespeicherten Entwürfe ab. Wenn Sie Zeit haben, speichern Sie vorher; wenn nicht, ist der Schutz wichtiger als der Entwurf.

Ein **Zurück** im Browser bringt die Anwendung nicht wieder: Die Seite verdeckt sich erneut und lädt neu — Sie landen auf der Anmeldemaske.

**Ohne Internetverbindung** funktioniert der Knopf ebenfalls: Der Inhalt wird verdeckt, der lokale Schlüssel für die Offline-Daten wird gelöscht und der Browser wechselt auf die neutrale Seite. Eine Abmeldung am Server ist offline nicht möglich — dafür sind die Offline-Daten ohne erneute Anmeldung nicht mehr lesbar, die Offline-Berechtigung läuft ohnehin nach 48 Stunden ab, und ein Neustart des Geräts räumt sie ab.

#### Was der Knopf nicht leisten kann

Sicher ist eines: **Ihr Bildschirm ist sofort leer.** Alles Weitere versucht die Anwendung so gut sie kann — vier Grenzen bleiben, und sie stehen hier, statt verschwiegen zu werden:

- **Der Browser-Verlauf.** Der Knopf ersetzt nur den *aktuellen* Verlaufseintrag. Alle Seiten, die Sie in dieser Sitzung vorher geöffnet haben, stehen weiter in der Chronik des Browsers (`Strg`+`H`) — mit Adresse und Seitentitel, und der Titel einer Personenseite enthält das Pseudonym. **Empfehlung:** Löschen Sie den Verlauf am Ende der Schicht, oder arbeiten Sie auf gemeinsam genutzten Geräten in einem privaten Fenster.
- **Ein zweites offenes Fenster.** Der Knopf wirkt auf das Fenster, in dem Sie ihn drücken. Ein zweiter offener Tab zeigt seinen bereits geladenen Inhalt weiter, bis dort etwas angeklickt oder neu geladen wird — dann landet auch er auf der Anmeldemaske. Sein Zugriff auf die lokalen Offline-Daten wird mitgesperrt, sobald die Löschung im ersten Fenster geklappt hat (siehe nächster Punkt). **Empfehlung:** Mit einem Fenster arbeiten und zweite Fenster vorher schließen.
- **Eine abgerissene Verbindung.** Die Abmeldung geht ohne Wartezeit an den Server. Ist genau in diesem Moment kein Netz da, kann sie verloren gehen; die Sitzung endet dann erst durch den Inaktivitäts-Timeout (Standard 30 Minuten) bzw. die maximale Sitzungsdauer.
- **Ein hängendes oder fehlgeschlagenes Löschen des lokalen Schlüssels.** Die Anwendung wartet vor dem Wechsel eine halbe Sekunde darauf, dass der Schlüssel für die Offline-Daten wirklich gelöscht ist — im Regelfall reicht das. Klappt es in dieser Zeit nicht, wird trotzdem gewechselt (Ihr Bildschirm ist wichtiger), und der Schlüssel bleibt bis zur nächsten regulären Abmeldung, bis zur automatischen Sperre nach längerer Untätigkeit oder bis zum Ablauf der 48-Stunden-Offline-Berechtigung bestehen. Wer das Gerät danach öffnet, kann die lokal gespeicherten Daten also unter Umständen noch sehen. **Empfehlung:** Lassen Sie das Gerät nach dem Verlassen nicht unbeaufsichtigt liegen.

### Sichtschutz bei Fenster- oder Tab-Wechsel

Ihre Einrichtung kann zusätzlich den **Sichtschutz** einschalten (Voreinstellung: aus). Ist er aktiv, legt sich eine neutrale Fläche über den Inhalt, sobald das Fenster den Fokus verliert oder der Tab in den Hintergrund geht — etwa beim Wechsel in ein anderes Programm.

Zum Weiterarbeiten klicken Sie auf **Fortfahren** oder drücken `Enter` bzw. die Leertaste. Der Cursor steht danach wieder dort, wo er vorher stand. Es läuft kein Countdown und nichts bewegt sich; die Fläche bleibt, bis Sie sie freigeben.

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

### Strichliste

Wenn die Verwaltung geeignete Dokumentationstypen freigegeben hat, steht im Zeitstrom die **Strichliste**. Sie zählt anonyme Kontakte je Typ im aktuellen Zeitfenster. Läuft gerade eine Schicht, gilt deren Zeitfenster; sonst der heutige Kalendertag.

- **Ohne Pflichtfelder:** `+1` legt sofort einen anonymen Kontakt an.
- **Mit Zahlen-Pflichtfeldern:** `+1` öffnet eine kompakte Werteingabe. Tragen Sie alle Pflichtwerte ein und wählen Sie **Kontakt zählen**. Das erzeugt genau einen anonymen Kontakt. Bei einem Spritzentausch gehören zum Beispiel Ausgabe und Rückgabe zu demselben Kontakt, statt mehrere Kontakte zu zählen.
- Ein Typ ist nur sichtbar, wenn er für die Strichliste freigegeben ist und Ihre Rolle ihn sowie alle erforderlichen Felder sehen darf. Typen mit anderen Pflichtfeldarten erscheinen nicht in der Strichliste.

### Datum wechseln

- **Pfeil-Buttons** über dem Feed: zum vorherigen oder nächsten Tag.
- Link **Heute**: springt zurück auf den aktuellen Tag.

### Schichtfilter

Unter der Datumsnavigation stehen **TimeFilter-Tabs** (z. B. „Frühdienst", „Spätdienst", „Nachtdienst" — konfigurierbar durch die Administration). Beim Öffnen des Zeitstroms wird die Schicht, die zur aktuellen Uhrzeit passt, automatisch vorausgewählt; Nachtschichten mit Mitternachts-Überlappung werden korrekt behandelt.

**Zeitumstellung.** An den beiden Umstellungswochenenden ist eine Uhrzeit nicht eindeutig, und der Schichtfilter legt das ausdrücklich fest. In der Nacht zur Winterzeit läuft die Stunde von 02:00 bis 03:00 zweimal; eine Schicht, die in dieser Stunde endet, endet beim **zweiten** Mal — eine Nachtschicht von 20:00 bis 02:45 dauert an diesem Tag also real 7 Stunden 45, und alles, was in der zusätzlichen Stunde dokumentiert wurde, bleibt in Zeitstrom, Übergabe und Strichliste sichtbar. In der Nacht zur Sommerzeit fällt die Stunde von 02:00 bis 03:00 aus; eine Schicht, die dort enden würde, endet stattdessen nach der vereinbarten Dauer ab Schichtbeginn und ist real eine Stunde kürzer. Ganztagsansichten sind davon nicht betroffen — der Umstellungstag im Herbst umfasst weiterhin 25 Stunden, der im Frühjahr 23.

**Die doppelte Stunde erfassen.** Ein Kontakt, der wirklich in dieser doppelten Stunde stattgefunden hat, lässt sich mit seiner echten Uhrzeit dokumentieren — Sie müssen nicht auf 01:59 oder 03:00 ausweichen. Tragen Sie den **Zeitpunkt** beim Anlegen wie gewohnt ein (etwa den 25.10.2026 um 02:30) und speichern Sie. Weil es diese Uhrzeit an diesem Tag zweimal gibt, fragt das Formular einmal nach: direkt unter dem Zeitpunkt erscheint das Auswahlfeld **„Welche der beiden Stunden?"** mit den beiden Möglichkeiten **Sommerzeit — erste Stunde (MESZ)** und **Winterzeit — zweite Stunde (MEZ)**. Wählen Sie die zutreffende Stunde und speichern Sie erneut. Das Auswahlfeld erscheint ausschließlich in diesem Grenzfall; an allen anderen Tagen und zu allen anderen Uhrzeiten sehen Sie es nicht.

**Offline-Erfassung.** Während der Offline-Erfassung steht keine Auswahl zur Verfügung; die Offline-Create-Shell lehnt eine Uhrzeit im Doppelfenster daher mit einem Hinweis ab. Wählen Sie in diesem Fall eine eindeutige Uhrzeit (außerhalb von 02:00–02:59 am Rückfalltag) oder erfassen Sie den Kontakt online.

Die beiden Stunden sind für die Anwendung wirklich verschiedene Zeitpunkte, die eine Stunde auseinanderliegen: der Kontakt landet in der Schicht, in der er stattfand, und die Reihenfolge im Zeitstrom stimmt. Angezeigt wird anschließend in beiden Fällen dieselbe Wanduhrzeit (02:30) — der Unterschied steckt im gespeicherten Zeitpunkt, nicht in der Anzeige.

**Beim Bearbeiten** eines Kontakts lässt sich der Zeitpunkt grundsätzlich nicht mehr ändern; er steht dort nur zur Ansicht. Die Auswahl treffen Sie also beim Anlegen. Ist ein Eintrag versehentlich in der falschen Stunde gelandet, legen Sie ihn neu an und lassen den falschen Eintrag löschen ([Kontakt löschen](#kontakt-löschen)).

In der Nacht zur Sommerzeit gibt es die Stunde von 02:00 bis 03:00 dagegen **gar nicht** — dort weist das Formular eine Uhrzeit aus diesem Fenster weiterhin zurück und nennt den Grund. Eine Auswahl gibt es hier nicht: eine Uhrzeit, die es nicht gibt, wird durch keine Angabe real.

Wenn ein Schichtfilter aktiv ist, erscheint oberhalb des Feeds ein aufklappbarer **Schichtübergabe-Block** mit Statistiken (Anzahl Kontakte, neue Aufgaben, neue Personen) und Highlights (Krisen-Ereignisse, neue Hausverbote, dringende Aufgaben).

#### Übergabenotiz

Ganz oben in der Übergabe — noch vor den Statistiken — steht die **Übergabenotiz**: ein Freitextfeld für das, was keine Kennzahl abbildet („B war heute sehr belastbar, bitte nachfassen"). Sie sehen dort zwei Blöcke: die Notiz der **zuletzt abgeschlossenen Schicht**, die Sie gerade übernehmen, und darunter das Feld für die **laufende Schicht**, in der Sie selbst arbeiten. Sind für Ihre Einrichtung **keine Schichten** eingerichtet, gilt der Kalendertag als Schicht: oben steht dann die Notiz von **gestern**, darunter das Feld für **heute**.

**Wer darf.** Lesen und schreiben dürfen Fachkraft, Leitung und Anwendungsbetreuung. Die Assistenz sieht die Notiz nicht — auch keinen Hinweis darauf, dass es eine gibt.

**Wie lange bearbeitbar.** Eine Notiz bleibt bis zum **Ende der Folgeschicht** änderbar: Wer im Frühdienst schreibt, kann bis zum Ende des Spätdienstes nachbessern; danach ist der Eintrag schreibgeschützt und trägt den Hinweis, bis wann eine Bearbeitung möglich war. Die Frist steht unter dem Textfeld. An den Umstellungswochenenden gilt dieselbe Schichtlogik wie oben beschrieben. **Die Frist steht mit dem Anlegen fest:** Ändert die Anwendungsbetreuung später die Schichtzeiten oder schaltet eine Schicht ab, öffnet das eine bereits geschlossene Notiz **nicht** wieder — was übergeben wurde, bleibt übergeben.

**Leeres Feld = keine Notiz.** Speichern Sie das Feld leer, wird der Eintrag entfernt — eine leere Notiz soll die Folgeschicht nicht als „nichts zu melden" fehlleiten.

**Wenn zwei gleichzeitig schreiben.** Legen zwei Personen im selben Moment die erste Notiz derselben Schicht an, gewinnt eine von beiden. Steht bei der anderen derselbe Text, passiert nichts weiter. Bei abweichendem Text zeigt die Seite **beide Fassungen**: oben die inzwischen gespeicherte (mit Autor und Zeit), darunter im Textfeld die eigene Eingabe — nichts davon geht verloren. Sie entscheiden dann: **Meine Eingabe übernehmen und ersetzen** schreibt den eigenen Text und ersetzt damit **genau die angezeigte** Fassung, **Verwerfen** behält die gespeicherte. Überschrieben wird nie stillschweigend — auch dann nicht, wenn sich der Bestand während Ihres Vergleichs noch einmal ändert: In diesem Fall wird nichts gespeichert, sondern die inzwischen aktuelle Fassung erneut zum Abgleich angezeigt, mit Ihrer Eingabe unverändert im Feld.

**Bitte keine Klarnamen.** Verwenden Sie dieselben Pseudonyme wie in der übrigen Dokumentation. Die Notiz ist verschlüsselt gespeichert und wird nach der von der Anwendungsbetreuung eingestellten Frist (Vorgabe: 90 Tage) automatisch gelöscht. Sie ist bewusst **nicht** Teil der Auskunft nach Art. 15 und **nicht** durchsuchbar.

Ist ihr Inhalt für ein Verfahren wichtig, dokumentieren Sie ihn innerhalb dieser einstellbaren Frist als Aktenvermerk am zugehörigen Fall; die Übergabenotiz selbst lässt sich nicht durch einen Legal Hold schützen und wird nach Fristablauf endgültig gelöscht.

Jede Änderung an einer Notiz erscheint im Audit-Log — protokolliert werden Schicht und Zeitpunkt, nie der Text selbst.

### Feed filtern

Direkt über dem Feed gibt es zwei Dropdowns:

- **Typ**: Alle · Events · Aktivitäten · Aufgaben · Hausverbote
- **Dokumentationstyp**: nur Einträge eines bestimmten Typs (z. B. „Kontakt", „Krisengespräch") — Sie sehen nur die Typen, die Ihre Rolle einsehen darf

Filteränderungen laden nur den Feed neu (HTMX), ohne die ganze Seite zu aktualisieren.

### Hausverbot-Banner

Wenn aktive Hausverbote in Ihrer Einrichtung bestehen, werden diese als rotes Banner unterhalb der Überschrift angezeigt.

---

## 2a. Arbeitszentrale

Die **Arbeitszentrale** (`/start/`) ist die Landingpage der **Leitung** — eine Übersichtsseite, die bestehende Daten zu kompakten Kacheln verdichtet. Der Sidebar-Link **„Arbeitszentrale"** (oberhalb des Zeitstroms) erscheint deshalb nur für die Leitung. Der Zeitstrom unter `/` bleibt unverändert.

Alle übrigen Rollen werden von `/start/` direkt an ihren Arbeitsplatz umgeleitet:

- **Fachkraft / Assistenz** — zum Zeitstrom (`/`); die bisherigen Kacheln „Heutige Kontakte", „Meine Aufgaben" und „Zuletzt bearbeitet" samt der Listen „Meine offenen Aufgaben" und „Zuletzt bearbeitete Personen" leben dort als Cockpit-Block oberhalb des Feeds weiter.
- **Anwendungsbetreuung** (`facility_admin`) — zur Verwaltungs-Übersicht (`/verwaltung/`).
- **Systemadministration** (`super_admin`) — zum Systembereich (`/system/`).

**Leitung** — Titel „Leitungs-Arbeitszentrale", DSGVO-Workflows, Retention und Statistik:

- **Löschanträge** — ausstehende Anträge (führt zur Löschantrags-Liste)
- **Löschvorschläge** — ausstehende Retention-Vorschläge (führt zum Retention-Dashboard)
- **Statistik** — Monat/Jahr des letzten Statistik-Snapshots

---

## 3. Kontakt dokumentieren (Event erstellen)

Ein **Event** ist ein einzelner dokumentierter Kontakt – z. B. ein Beratungsgespräch, eine Spritzentauschausgabe oder ein anonymer Besuch.

### Einen Kontakt dokumentieren

1. Klicken Sie in der Sidebar auf **Neu erstellen** und wählen Sie **Kontakt dokumentieren** (oder navigieren Sie zu `/events/new/`).
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

Wenn Ihre Administration für wiederkehrende Dokumentationsmuster **Schnell-Vorlagen** eingerichtet hat (z. B. „Beratungsgespräch 30 Min", „Standard-Check-in"), erscheinen diese oben auf der Seite „Kontakt dokumentieren" als Buttons.

- **Klick auf einen Button** füllt das Formular mit den hinterlegten Werten und dem passenden Dokumentationstyp vor.
- **Alle Felder bleiben bearbeitbar** — die Vorlage liefert nur Defaults, keine Sperre.
- **Bereits ausgefüllte Felder werden nicht überschrieben** — die Vorlage füllt nur leere Felder.
- **Rollen-Filter:** Sie sehen nur Vorlagen, deren Dokumentationstyp Sie laut Ihrer Rolle einsehen dürfen. Assistenzen sehen daher keine Vorlagen für erhöhte/hohe Sensitivität.
- **Selbstheilend:** Wurde nach Anlage einer Vorlage eine Auswahl-Option deaktiviert, wird der betroffene Wert beim Anwenden stillschweigend verworfen — Sie wählen dann einfach einen aktuell gültigen Wert.

> **Hinweis:** Vorlagen werden von Administratoren im Django-Admin-Bereich angelegt und gepflegt. Wenn Sie feststellen, dass Sie dasselbe Dokumentationsmuster immer wieder tippen, bitten Sie die Administration, eine Schnell-Vorlage dafür zu hinterlegen.

### Dateianhänge

An jedes Ereignis kann eine Datei angehängt werden – z. B. ein eingescanntes Formular, ein Foto eines Dokuments oder ein PDF.

> **Auch an Personen und Fällen:** Datei-Felder sind nicht mehr auf Ereignisse beschränkt. Hat Ihre Administration ein Datei-Feld an einem **Personen-** oder **Fall-Typ** hinterlegt, erscheint es im Formular der Person bzw. des Falls und funktioniert dort genauso wie hier beschrieben — Hochladen, Ersetzen mit Vorversionen, Herunterladen aus der Detailansicht. Zwei Unterschiede: **Fall-Dateien** sehen und laden nur Fachkräfte, Leitungen und Administration (Assistenzen haben keinen Zugriff auf Fälle), und Dateien an Person oder Fall werden **zusammen mit der Person gelöscht** — sie haben keine eigene Aufbewahrungsfrist.

- **Hochladen:** Im Kontakt-Formular gibt es ein Feld für den Datei-Upload. Wählen Sie eine Datei von Ihrem Gerät aus und speichern Sie das Ereignis wie gewohnt. An Person und Fall läuft es identisch: Datei im Formular wählen, Datensatz speichern.
- **Virenscan:** Vor dem Speichern wird jede Datei automatisch durch einen Virenscanner (ClamAV) geprüft. Infizierte Dateien werden abgelehnt — Sie erhalten eine Fehlermeldung und das Ereignis wird nicht gespeichert.
- **Verschlüsselung:** Alle Anhänge werden verschlüsselt gespeichert (Fernet, AES-128-CBC mit HMAC-SHA256). Das bedeutet: Die Datei ist auf dem Server nicht im Klartext lesbar, sondern nur über Anlaufstelle selbst abrufbar.
- **Bild-Metadaten werden entfernt:** Bei Bildern (JPG, PNG, WebP, TIFF — nicht bei GIF und BMP) werden beim Hochladen die eingebetteten Metadaten gelöscht — vor allem der **GPS-Aufnahmeort** (bei Handyfotos oft der Wohn- oder Aufenthaltsort der betroffenen Person), außerdem Gerätemodell, Aufnahmezeit und das eingebettete Vorschaubild, das den unbeschnittenen Originalausschnitt zeigen kann. Das Bild selbst bleibt unverändert sichtbar, hochkant aufgenommene Fotos bleiben hochkant. Bei PDF- und Office-Dateien passiert das **nicht** — dort kann der Name der bearbeitenden Person in den Dokumenteigenschaften stehen bleiben.
- **Maximale Größe:** Standardmäßig sind **bis zu 10 MiB pro Datei** erlaubt. Ihre Administration kann diesen Wert anpassen.
- **Unterstützte Formate:** PDF, Office-Dokumente und Bilder. Die genaue, für Ihre Einrichtung erlaubte Liste erfragen Sie bitte bei Ihrer Administration.
- **Herunterladen:** Öffnen Sie die Detailansicht des Ereignisses (bzw. der Person oder des Falls) und klicken Sie auf den Datei-Link. Die Datei wird beim Abruf automatisch entschlüsselt und im Browser ausgeliefert.
- **Ersetzen (mit Versionshistorie):** Beim Bearbeiten können Sie die bestehende Datei durch eine neue ersetzen. Die alte Datei wird **nicht** gelöscht — sie bleibt als **Vorversion** erhalten und ist in der Detailansicht über ein aufklappbares Akkordeon weiterhin herunterladbar. Vorversionen werden erst beim vollständigen Löschen des Datensatzes entfernt. Das gilt an Ereignis, Person und Fall gleichermaßen.
- **Entfernen:** Im Formular lässt sich eine hinterlegte Datei abwählen. Sie verschwindet aus der Detailansicht und ist nicht mehr herunterladbar; die Versionskette bleibt für die Nachvollziehbarkeit bestehen, bis der Datensatz selbst gelöscht wird. Die Datei liegt weiterhin verschlüsselt auf dem Server und wird in einer Auskunft nach Art. 15 DSGVO als „entfernt am …" ausgewiesen. Das ist **keine Sackgasse**: Eine entfernte Datei lässt sich später weiterhin endgültig löschen (siehe unten und [Abschnitt 3a](#endgültig-löschen)).
- **Endgültig löschen:** Für den Irrtumsfall — falsche Datei, falsche Person — gibt es zusätzlich das ersatzlose Löschen. Es vernichtet die verschlüsselte Datei samt aller Vorversionen physisch. Das ist **unwiderruflich** und deshalb Leitungen und der Administration vorbehalten. Der Einstieg steht an **zwei** Stellen: direkt an der Datei in der Detailansicht des Ereignisses, der Person oder des Falls (Link „endgültig löschen" neben dem Dateinamen) und in der [Dateien-Übersicht](#3a-dateien-übersicht).

> **Typwechsel verwirft Werte *und* Dateien:** Wechseln Sie an einer Person oder einem Fall den Dokumentationstyp — oder entfernen ihn („— Kein Typ —") —, gelten die Zusatzfelder des alten Typs nicht mehr. Eine Rückfrage vor dem Speichern nennt beides: wie viele **Feldwerte** verworfen werden **und** wie viele hinterlegte **Dateien** entfernt werden. „Abbrechen" lässt alles unverändert stehen; erst „OK" führt es aus.

> **Offline-Hinweis:** Ereignisse mit Datei-Anhängen können aktuell **nicht offline** gespeichert werden. Wenn Sie offline arbeiten, erscheint beim Anhängen einer Datei ein deutlicher Hinweis. Siehe [Abschnitt 8](#8-pwa-installieren-und-offline-arbeiten).

> **Zentrale Dateiübersicht:** Alle Dateianhänge Ihrer Einrichtung lassen sich über die **Dateien-Seite** in einer Liste durchsuchen und filtern — siehe [Abschnitt 3a: Dateien-Übersicht](#3a-dateien-übersicht).

### Kontakt bearbeiten

1. Öffnen Sie das Ereignis (über den Zeitstrom oder die Chronik der Person).
2. Klicken Sie auf **Bearbeiten**.
3. Ändern Sie die gewünschten Felder und klicken Sie auf **Speichern**.

> **Hinweis:** Bearbeitungen werden in der Änderungshistorie des Ereignisses protokolliert. Frühere Versionen bleiben im Verlauf sichtbar. Auch verschlüsselte Vorher-Werte werden dort im Klartext angezeigt, wenn Ihre Rolle das Feld sehen darf; höher eingestufte Felder bleiben als „[Eingeschränkt]“ geschützt.

> **Gleichzeitige Bearbeitung:** Wenn jemand anderes den gleichen Datensatz gleichzeitig bearbeitet hat, erscheint eine Fehlermeldung. Laden Sie die Seite neu und tragen Sie Ihre Änderung erneut ein — so wird sichergestellt, dass keine Änderungen still überschrieben werden.

### Kontakt löschen

1. Öffnen Sie das Ereignis.
2. Klicken Sie auf **Löschen**.
3. Geben Sie eine Begründung ein.
4. Bestätigen Sie die Löschung.

> **Wichtig:** Wenn der Kontakt einer **Person der Kontaktstufe „Mit Zusatzangaben"** zugeordnet ist, wird kein sofortiges Löschen durchgeführt. Stattdessen wird automatisch ein **Löschantrag** gestellt, der von einer Leitung oder Administration genehmigt werden muss (4-Augen-Prinzip). Sie erhalten eine entsprechende Rückmeldung.

---

## 3a. Dateien-Übersicht

Die **Dateien-Seite** (`/attachments/`, Sidebar-Eintrag **Dateien**) zeigt alle Anhänge Ihrer Einrichtung in einer durchsuchbaren Liste — ohne vorher den jeweiligen Datensatz öffnen zu müssen. Sie umfasst Dateien an **Ereignissen, Personen und Fällen**.

### Zugriff

- **Sidebar-Navigation → Dateien**
- Verfügbar für Assistenzen, Fachkräfte, Leitungen und Administratoren.

### Sichtbarkeit

Sie sehen nur Dateien, deren Datensatz (Ereignis, Person oder Fall) **und** dessen Feld Ihre Sensitivitätsrolle erreicht:

- Felder mit „hoher" Sensitivität bleiben für Fachkräfte einer niedrigeren Stufe verborgen — gleiches Gate wie in der Detailansicht.
- Anhänge gelöschter Ereignisse, Personen und Fälle sind nicht enthalten; ebenso wenig Dateien, die Sie im Formular wieder entfernt haben.
- **Assistenzen sehen keine Dateien an Fällen** — Fälle sind für sie generell nicht sichtbar, ihre Dateien folgerichtig auch nicht.
- Facility-Scoping: Sie sehen ausschließlich Dateien Ihrer eigenen Einrichtung.

### Spalte „Zugeordnet zu"

Jede Zeile weist aus, **woran** die Datei hängt: **Ereignis**, **Person** oder **Fall**. Der Eintrag ist verlinkt und führt direkt auf die zugehörige Detailseite (bei Fällen nur für Rollen, die Fälle sehen dürfen).

### Filter

Zwei Filter oberhalb der Tabelle:

- **Dokumentationstyp** — nur Anhänge zu Datensätzen eines bestimmten Typs (z. B. „Beratungsgespräch", „Aufnahmebogen"). Angeboten werden nur Typen, die Sie laut Ihrer Rolle einsehen dürfen.
- **Personen** — alle Anhänge zu einem bestimmten Pseudonym. Der Filter greift über **alle drei** Wege, auf denen eine Datei zu einer Person gehört: über ihr Ereignis, direkt an der Person und über einen ihrer Fälle.

Die Filter sind kombinierbar und aktualisieren die Liste via HTMX ohne kompletten Seitenreload.

### Herunterladen

Jede Zeile zeigt den Originaldateinamen. Ein Klick darauf liefert die entschlüsselte Datei — exakt wie in der Detailansicht des Ereignisses, der Person oder des Falls. Downloads werden im Audit-Log protokolliert.

### Verschlüsselung

Alle Dateien liegen auf dem Server verschlüsselt (Fernet, AES-128-CBC mit HMAC-SHA256). Entschlüsselt wird erst beim Download-Request im Django-Prozess; direkt auf die Festplatte zugreifende Admins sehen nur die verschlüsselten Binärdaten.

### Vorversionen

Ersetzte Dateien bleiben als Vorversionen erhalten (siehe [Abschnitt 3 – Dateianhänge](#dateianhänge)). Die Dateien-Übersicht zeigt aktuell nur die jeweils aktuelle Version eines Eintrags; Vorversionen sind über den Eintrag in der Detailseite des Ereignisses, der Person oder des Falls einsehbar.

### Endgültig löschen

Neben dem Download trägt jede Zeile für **Leitungen und Administration** ein Papierkorb-Symbol: **endgültig löschen**. Denselben Einstieg gibt es direkt an der Datei in der Detailansicht des Ereignisses, der Person oder des Falls. Gedacht ist er für den Irrtum — eine Datei, die nie hätte hochgeladen werden dürfen (falsche Person, falsche Datei, Klarname im Dokument).

- Der Klick führt zunächst auf eine **Bestätigungsseite**. Sie benennt den Datensatz, um den es geht: Dateiname, Größe, zugeordneter Datensatz **mit Person-ID**, Dokumentationstyp, Hochladezeitpunkt und die Zahl der betroffenen Versionen. Prüfen Sie diese Angaben — zwei gleichnamige Dateien an zwei Personen unterscheiden sich sonst durch nichts.
- Bestätigt wird die verschlüsselte Datei **samt allen Vorversionen vom Datenträger entfernt**. **In der Anwendung** gibt es danach keinen Papierkorb und keine Wiederherstellung — auch nicht durch die Administration.
- **Sicherungskopien sind davon nicht erfasst.** Führt Ihre Einrichtung ein Medien-Backup, bleibt die verschlüsselte Datei dort bis zum Ablauf der Backup-Aufbewahrung erhalten und ließe sich über eine Wiederherstellung des Gesamtstands zurückholen. Wenn eine betroffene Person auf vollständiger Löschung besteht, sagen Sie ihr das nicht anders zu und wenden Sie sich zusätzlich an Ihre Administration.
- **Aufbewahrungssperre (Legal Hold):** Steht der Datensatz oder die zugehörige Person unter einem aktiven Legal Hold, ist die Schaltfläche gesperrt und die Bestätigungsseite nennt den Grund. Beweissicherungspflichtige Dateien lassen sich nicht vernichten. Der abgewiesene Versuch wird protokolliert.
- Der Vorgang wird im Audit-Log protokolliert — **ohne** den Dateinamen im Klartext, aber mit Datensatzbezug und Zeitpunkt. In einer Auskunft nach Art. 15 DSGVO erscheint er dort als „Anhang gelöscht"; die Datei selbst wird danach nicht mehr als gespeichert ausgewiesen, weil sie es nicht mehr ist.

**Bereits entfernte Dateien.** Wer eine Datei zuerst nur über die Checkbox „Entfernen" aus der Dokumentation genommen hat, findet sie in der normalen Übersicht nicht mehr — sie liegt aber weiterhin verschlüsselt auf dem Server. Leitungen und Administration blenden diese Dateien über **„Entfernte Dateien anzeigen"** oberhalb der Tabelle ein und können sie von dort aus endgültig löschen.

> **Wann besser nur entfernen?** Wenn die Datei fachlich einmal richtig war und nur nicht mehr in der laufenden Dokumentation stehen soll, gehört sie über das Bearbeiten-Formular **entfernt** (siehe [Abschnitt 3 – Dateianhänge](#dateianhänge)). Sie verschwindet dann aus der Oberfläche, bleibt aber nachweisbar — und der Weg zum endgültigen Löschen bleibt über „Entfernte Dateien anzeigen" offen.

---

## 4. Personen verwalten

Personen werden in Anlaufstelle **ausschließlich mit Pseudonymen** erfasst – keine Klarnamen. Das Pseudonym ist der primäre Identifikator.

### Kontaktstufen

Jede Person hat eine **Kontaktstufe**:

| Stufe | Beschreibung |
|---|---|
| **Nur Pseudonym** | Es ist nur das Pseudonym hinterlegt, dazu die Altersgruppe |
| **Mit Zusatzangaben** | Zusätzliche persönliche Angaben sind hinterlegt (eingeschränkter Zugriff, das Öffnen der Detailseite wird protokolliert) |

> **Hinweis:** Protokolliert wird das Öffnen der **Detailseite** (`/clients/<id>/`) einer Person der Kontaktstufe „Mit Zusatzangaben" — und nur das. Die Personenliste, das Bearbeiten-Formular derselben Person, ein Ereignis, ein Fall und ein Suchtreffer erzeugen **keinen** Eintrag, auch wenn dort Zusatzangaben zu sehen sind.

### Person anlegen

1. Navigieren Sie zu **Personen** (`/clients/`) und klicken Sie auf **Person anlegen** (oder direkt zu `/clients/new/`).
2. Füllen Sie das Formular aus:
   - **Pseudonym:** Einzigartiger Name innerhalb Ihrer Einrichtung (z. B. ein selbstgewählter Spitzname). Das Pseudonym darf innerhalb einer Einrichtung nur einmal vorkommen.
   - **Kontaktstufe:** Wählen Sie „Nur Pseudonym" oder „Mit Zusatzangaben".
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

Von der Detailseite aus können Sie über **Kontakt dokumentieren** direkt einen Kontakt für diese Person erfassen oder über **Aufgabe erstellen** eine Aufgabe anlegen.

### Person bearbeiten

1. Öffnen Sie die Detailseite der Person.
2. Klicken Sie auf **Bearbeiten**.
3. Ändern Sie die gewünschten Felder (z. B. Kontaktstufe hochsetzen).
4. Klicken Sie auf **Speichern**.

> **Hinweis:** Eine Änderung der Kontaktstufe wird automatisch im Audit-Log protokolliert.

> **Gleichzeitige Bearbeitung:** Wenn jemand anderes den gleichen Datensatz gleichzeitig bearbeitet hat, erscheint eine Fehlermeldung. Laden Sie die Seite neu und tragen Sie Ihre Änderung erneut ein — so wird sichergestellt, dass keine Änderungen still überschrieben werden.

### Personen zusammenführen (Duplikate)

Wurde dieselbe Person versehentlich zweimal angelegt — etwa unter zwei Schreibweisen des Pseudonyms —, lassen sich die beiden Akten nachträglich zusammenführen. Schon beim **Anlegen** hilft ein Hinweis, das zu vermeiden: Sobald Sie ein Pseudonym eintippen, das einer bestehenden Person ähnelt, erscheint unter dem Feld „Ähnliche Person existiert bereits" mit einem Link auf die Akte. Der Hinweis blockiert nichts — Sie können trotzdem speichern.

Ist das Duplikat bereits entstanden:

1. Öffnen Sie die Akte der Person, die **aufgelöst** werden soll, und wählen Sie im Menü **Weitere Aktionen** (⋮) den Eintrag **Mit Duplikat zusammenführen**.
2. Suchen Sie die Ziel-Person — die Akte, die bestehen bleibt. Die Suche findet auch ähnliche Schreibweisen.
3. Die Bestätigungsseite zeigt **beide Akten im Vergleich** (Kontaktstufe, Altersgruppe, Anlagedatum, letzter Kontakt, Zahl der Ereignisse/Fälle/Anhänge) sowie eine Statistik-Warnung und den Frist-Hinweis. Welcher der beiden Wege gilt, sagt die Seite selbst:
   - **Direkt zusammenführen** — wenn beide Personen auf „Nur Pseudonym" stehen; das darf nur die **Leitung**.
   - **Antrag mit Bestätigung** — sobald eine der beiden Personen „Mit Zusatzangaben" trägt (oder eine Fachkraft den Weg geht). Geben Sie eine Begründung ein; bestätigt wird über **Datenschutz → Löschanträge**, wo der Antrag als eigene Art **Zusammenführung** mit Ziel erscheint. Bis dahin ist die Akte als „Zusammenführung beantragt" gekennzeichnet und bleibt nutzbar.

Was dabei passiert:

- **Ereignisse, Fälle, Aufgaben und die Merkliste wandern vollständig** zur Ziel-Akte. Bei den Zusatzangaben behält das Ziel seine Werte; Angaben der Quelle füllen nur leere Felder. Die Kontaktstufe steigt auf die höhere der beiden — nie umgekehrt.
- Die aufgelöste Akte verschwindet aus Liste, Suche und Auswahlfeldern und liegt mit dem Vermerk „zusammengeführt in …" im **Papierkorb**. Bis zum Ablauf der Papierkorb-Frist kann die Anwendungsbetreuung dort **Zusammenführung aufheben** — die gewanderten Einträge kehren zurück; seit der Zusammenführung neu erfasste bleiben beim Ziel. Danach wird die leere Hülle wie jede gelöschte Person endgültig anonymisiert; der Verweis auf das Ziel bleibt. Liegt auf einer der beiden Akten eine **Aufbewahrungssperre** (Legal Hold), ist das Aufheben so lange nicht möglich — die Anwendungsbetreuung erhält dann eine entsprechende Meldung.
- In einer **Datenauskunft nach Art. 15 DSGVO** weist die Ziel-Akte im Abschnitt „Zusammenführungen" aus, welche Akte wann in sie überführt wurde; die Auskunft über die aufgelöste Akte nennt umgekehrt „Zusammengeführt in". Das Zugriffsprotokoll der aufgelösten Akte bleibt an deren Datensatz — eine Auskunft über die volle Historie beider Vorgänger beantworten Sie mit **beiden** Auskünften.

> **Offline:** Auf Geräten zwischengespeicherte Daten der aufgelösten Akte werden beim nächsten Abgleich verworfen. Wurde zu ihr **offline bereits ein Ereignis erfasst**, aber noch nicht übertragen, wird es nicht automatisch auf die Ziel-Akte umgebucht — es landet wie bei einer Löschung in der **Konfliktliste** (Dead-Letter) zur Nachbearbeitung, damit nichts still im Namen der erfassenden Person umgeschrieben wird.

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

### Aufgabe oder Hinweis erstellen

1. Klicken Sie in der Inbox auf **Aufgabe erstellen** (oder navigieren Sie zu `/workitems/new/`).
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

Für umfangreichere Recherchen steht die Suchseite unter `/search/` zur Verfügung. Sie ist über den Link „Alle Ergebnisse anzeigen" in der Schnellsuche erreichbar; ebenso führt die Eingabetaste in beiden Suchfeldern der Navigation (Sidebar und mobiles Overlay) direkt dorthin.

- Die **Gruppenübersicht** zeigt je Gruppe bis zu **10 Treffer**, nennt in der Gruppenüberschrift aber immer die **Gesamtzahl**.
- Über die Filter-Chips **„Alle Typen · Nur Personen (N) · Nur Ereignisse (N)"** schränken Sie auf einen Typ ein (`?type=person` bzw. `?type=event`); in der gefilterten Liste blättern Sie seitenweise durch alle Treffer.
- Am Seitenende erklärt der Block **„Was die Suche nicht findet"**, welche Daten bewusst nicht durchsucht werden.

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

### CSV-Daten importieren

Der Import-Assistent für Personen und Ereignisse ist nur für die **Anwendungsbetreuung** verfügbar. Sie finden die vollständige Schritt-für-Schritt-Anleitung mit Dateigrenzen, Zuordnung, Probelauf, Übernahme und Abbruch im [Admin-Handbuch § 2.12 „Import-Assistent"](admin-guide.md#212-import-assistent).

### Statistik-Dashboard

1. Navigieren Sie zu **Statistik** (`/statistics/`).
2. Wählen Sie einen Zeitraum:
   - **Letzte 30 Tage** (Standard)
   - **Letzte 90 Tage**
   - **Letzte 180 Tage**
   - **Kalenderjahr** (mit Jahres-Navigation, siehe unten)
   - **Eigener Zeitraum:** Geben Sie Start- und Enddatum manuell ein.

   > Die drei „Letzte N Tage"-Fenster sind **rollierend** und enden heute; beide Enden zählen mit, „Letzte 30 Tage" umfasst also genau 30 Tage. Kalenderwörter stehen ausschließlich für Kalendereinheiten — deshalb heißt der kalendarische Zeitraum „Kalenderjahr" und die rollierenden Fenster nach ihrer Länge.
3. Das Dashboard aktualisiert sich automatisch und zeigt aggregierte Kennzahlen zu Kontaktzahlen, Dokumentationstypen und Personengruppen.

> **Hinweis:** Der Reiter „Übersicht" — dieses Dashboard — zählt die Ereignisse des Zeitraums **unabhängig von ihrer Berichtszuordnung**: auch solche, deren Dokumentationstyp keinem Bericht zugeordnet oder ausdrücklich von den Berichten ausgenommen ist. Die Berichtszuordnung (in der Konfiguration „Berichtsschlüssel") wirkt sich erst im Reiter „Datensatz-Berichte" auf die Zahlen aus — die beiden Reiter können im selben Zeitraum deshalb unterschiedliche Zahlen zeigen; weitere Gründe dafür nennt der Abschnitt „Datensatz-Berichte". Welche Grundgesamtheit eine einzelne Tabelle auswertet, steht jeweils unter ihrer Überschrift: „Kontakte nach Altersgruppe" zählt zum Beispiel nur Ereignisse mit Personenbezug.

### Jahresnavigation

Um die Daten eines bestimmten Jahres zu sehen:

1. Klicken Sie auf **Kalenderjahr**.
2. Mit den Pfeil-Buttons links und rechts neben der Jahreszahl navigieren Sie zum vorherigen oder nächsten Jahr.
3. Beim aktuellen Jahr wird der Zeitraum vom 01.01. bis heute angezeigt, bei vergangenen Jahren das vollständige Jahr (01.01. – 31.12.).

### Trend-Charts

Unterhalb der Kennzahlen zeigt das Dashboard drei interaktive Diagramme:

| Diagramm | Beschreibung |
|----------|-------------|
| **Kontakte im Zeitverlauf** | Liniendiagramm mit monatlicher Aufschlüsselung der Kontakte (Gesamt, Anonym, Nur Pseudonym, Mit Zusatzangaben) |
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

### PDF-Export

Der PDF-Export erstellt einen strukturierten Statistikbericht für den oben im Dashboard gewählten Zeitraum, zur internen Dokumentation.

1. Wählen Sie im Statistik-Dashboard den Zeitraum.
2. Klicken Sie auf **PDF-Export**.
3. Die PDF-Datei wird heruntergeladen (Dateiname: `bericht_YYYY-MM-DD_YYYY-MM-DD.pdf`).

### Beispiel-Sachbericht

Der Beispiel-Sachbericht erstellt einen strukturierten Sachbericht nach einem festen Beispiel-Layout. Er ist ein anpassbares Beispiel bzw. eine Vorlage — kein verbindlicher oder aktueller Behördennachweis und keine Garantie für landesspezifische Anforderungen.

1. Wählen Sie den Zeitraum. Er gilt für den gesamten Statistik-Bereich und bleibt beim Wechsel zwischen den Reitern erhalten.
2. Öffnen Sie den Reiter **Datensatz-Berichte** und wählen Sie die Karte **Beispiel-Sachbericht**.
3. Klicken Sie unter der Vorschau auf **PDF-Export**. Die Datei wird heruntergeladen (Dateiname: `sample-report-1_YYYY-MM-DD_YYYY-MM-DD.pdf`).

> **Tipp:** Für Halbjahresberichte wählen Sie „Eigener Zeitraum" und setzen Start- und Enddatum auf den 01.01. bzw. 30.06. (oder 01.07. – 31.12.). „Letzte 180 Tage" ist ein rollierendes Fenster und trifft nie exakt ein Kalenderhalbjahr.

**Datenquelle: Monats-Aggregate.** In der Fußzeile des PDFs steht, woher die Zahlen kommen. Die Sachbericht-PDFs — der Beispiel-Sachbericht wie auch der Halbjahresbericht-Export — lesen aus den gespeicherten **Monats-Auswertungen**. Zwei Folgen: Zeiträume, deren Ereignisse nach Ablauf der Löschfristen entfernt wurden, zeigt das PDF weiterhin mit Zahlen — und über mehrere Monate hinweg werden die Monatswerte addiert, wer in zwei Monaten erschienen ist, zählt bei „Einzigartige Personen" zweimal. Die Berichte des Reiters „Datensatz-Berichte" (CSV/JSON/XML, siehe unten) rechnen dagegen live und können deshalb im selben Zeitraum andere Zahlen zeigen.

### Datensatz-Berichte (CSV / JSON / XML / PDF)

Der Reiter **Datensatz-Berichte** listet alle Berichtsstandards, die für Ihre Einrichtung eingerichtet sind — der Beispiel-Sachbericht ebenso wie weitere, die Ihre Administration ergänzt hat.

1. Wählen Sie die **Karte** des gewünschten Berichts. Jede Karte nennt in einem Satz, was der Bericht zeigt, welche Grundgesamtheit er auswertet und ob er live rechnet oder aus Monats-Auswertungen liest.
2. Darunter erscheint eine **Vorschau** mit den Zahlen des gewählten Zeitraums. Die Adresse merkt sich die Auswahl (`?ansicht=datensaetze&bericht=…`), Lesezeichen und Zurück-Taste funktionieren.
3. Über die Schaltflächen **CSV-Export**, **JSON-Export** und **XML-Export** laden Sie denselben Datenstand als Datei herunter. CSV eignet sich für Tabellenkalkulationen, JSON und XML für die Weiterverarbeitung in anderen Systemen. Beim Beispiel-Sachbericht kommt **PDF-Export** hinzu.

Ein Zeitraum-Klick verwirft die Berichtsauswahl nicht — Karte und Vorschau bleiben stehen, die Zahlen aktualisieren sich.

**Vollständigkeitshinweise:** Über der Vorschau steht, was der Bericht *nicht* zeigt — zum Beispiel Ereignisse, deren Dokumentationstyp keiner Berichtskategorie zugeordnet ist, oder Ereignisse ohne Personenbezug. Eine gelbe **Hinweis**-Markierung bedeutet: Es fehlen Daten, die Zahlen sind also zu niedrig. Eine graue **Info**-Markierung bedeutet: Die Daten sind enthalten, fehlen aber in einzelnen Teilauswertungen. Steht dort „Keine Vollständigkeitsprobleme erkannt", ist der Bericht lückenlos.

**Kleinstfallzahlen werden unterdrückt:** Vorschau *und* Download zeigen denselben datenschutzfreundlichen Datenstand — Werte unterhalb der K-Anonymitäts-Schwelle Ihrer Einrichtung (Standard 5) werden entfernt, damit sich aus einem Bericht keine einzelnen Personen ableiten lassen. **Dargestellt** wird ein entfernter Wert je nach Format unterschiedlich: in der Vorschau als Gedankenstrich („—"), im CSV als leere Zelle (die Metadatenzeile am Dateianfang weist auf die Unterdrückung hin), im JSON als `null`, im XML als leeres Element. Gelegentlich fällt zusätzlich ein zweiter, größerer Wert weg — das ist beabsichtigt, sonst ließe sich der unterdrückte Wert zurückrechnen. Aus demselben Grund enthalten Vollständigkeitshinweise bei sehr kleinen Zahlen gar keine Ziffer mehr.

**Datenquelle: Live-Abfrage.** Unter der Vorschau steht, woher die Zahlen kommen. Vorschau und Download rechnen die Zahlen bei **jedem Aufruf neu** aus den Ereignisdaten. Zeiträume, deren Ereignisse nach Ablauf der Löschfristen entfernt wurden, erscheinen deshalb leer — anders als im Beispiel-Sachbericht-PDF, das aus den Monats-Auswertungen liest. In den Dateiformaten steht dieselbe Angabe maschinenlesbar: im CSV in der Metadatenzeile am Dateianfang, in JSON und XML als Feld `data_source` (Wert `live`).

> **Hinweis:** Jeder Download wird im Audit-Log protokolliert; die Vorschau nicht. Ein leerer Zeitraum sieht in der Vorschau aus wie eine Unterdrückung — prüfen Sie im Zweifel den gewählten Zeitraum.

### Datenschutzfreundliche externe Berichte (Leitung / Admin)

Für die Weitergabe an externe Stellen (z. B. Fördermittelgeber oder Kommune) gibt es unter `/statistics/external/` einen **externen Bericht mit Datenschutzprofil**. Er nutzt dieselben Zeitraum-Filter wie das Statistik-Dashboard, ist aber bewusst datensparsam:

- **Kein Pseudonym-Ranking** — die Top-Personen-Liste des internen Dashboards entfällt vollständig.
- **K-Anonymität** — Aggregate mit weniger als *k* Datensätzen (Schwelle aus den Einrichtungs-Settings, Standard 5) werden als **„unterdrückt"** ausgewiesen statt mit konkreter Zahl. Das betrifft „Unterschiedliche Personen", „Nach Dokumentationstyp" und „Nach Altersgruppe".
- **Datenschutzprofil-Kopf** — Einrichtung, Profil (`external`), Zeitraum, K-Anonymität-Schwelle, Erzeugungs-Zeitpunkt und **Datenquelle** stehen oben im Bericht. Auch dieser Bericht rechnet live; in der JSON-Fassung steht die Angabe als `metadata.data_source` (Wert `live`).

Über **„Als JSON exportieren"** (bzw. `?format=json` an der URL) erhalten Sie dieselben Daten maschinenlesbar. Jeder Aufruf wird als `EXPORT` im Audit-Log protokolliert. Der externe Bericht ist nur für **Leitung** und **Administration** zugänglich.

### Löschanträge prüfen (Leitung / Admin)

Wenn eine Fachkraft oder Assistenz einen Kontakt einer Person der Kontaktstufe „Mit Zusatzangaben" löschen möchte, entsteht ein **Löschantrag**, der von einer Leitung oder Administration genehmigt werden muss.

1. Offene Löschanträge sehen Sie unter `/deletion-requests/`.
2. Klicken Sie auf einen Antrag, um Details und das betroffene Ereignis einzusehen.
3. Klicken Sie auf **Genehmigen** oder **Ablehnen** und bestätigen Sie.

> **Wichtig:** Sie können Ihren eigenen Löschantrag nicht selbst genehmigen (4-Augen-Prinzip).

Unabhängig von Ihrer Rolle: Als antragstellende Person können Sie Ihren eigenen Löschantrag selbst zurückziehen, solange er noch **ausstehend** ist. Unter `/deletion-requests/` erscheint an Ihrem eigenen ausstehenden Antrag neben dem Vier-Augen-Hinweis der Link **Zurückziehen**. Der Bestätigungsdialog sagt ausdrücklich: **Es wird nichts gelöscht — die Daten bleiben unverändert.** Danach ist jederzeit ein neuer Löschantrag für dasselbe Ziel möglich. Der Antrag bleibt für den Genehmiger-Pool in der Sektion **Zurückgezogen** sichtbar und der Rückzug wird mit dem Endstatus **Zurückgezogen** protokolliert.

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
3. Eine Übersicht über Ihre mitgenommenen Personen finden Sie jederzeit unter **„Personen"** (`/clients/`) — diese Ansicht funktioniert seit auch offline (siehe unten).

**Während des Einsatzes (offline):**

- **Personen, Aufgaben und Zeitstrom bleiben an ihrer gewohnten Adresse verfügbar (seit/3, #1499):** Die **Personenliste** (`/clients/`), die **Aufgabenliste** (`/workitems/`) und der **Zeitstrom** als Startseite (`/`) rendern offline denselben lokalen Ausschnitt wie online — aus Ihren mitgenommenen Personen zusammengestellt, in gewohnter Optik. Die Aufgabenliste und der Zeitstrom zeigen dabei auch **anonyme bzw. personlose Einträge** (Standalone-Aufgaben, Kontakte ohne Personenbezug), klar als solche markiert; der Zeitstrom ist bewusst als „lokale Chronik" gekennzeichnet — er zeigt nur die offline verfügbaren Vorgänge, nicht die vollständige Historie der Einrichtung. Eine separate Offline-Startseite brauchen Sie dafür nicht mehr.
- Öffnen Sie eine mitgenommene Person wie gewohnt über ihre Detailseite — die Ansicht bleibt an der **normalen Adresse** und wird aus dem verschlüsselten Offline-Speicher aufgebaut (kein separater „Offline"-Link mehr).
- Sie können **neue Ereignisse erfassen** (über **„Kontakt dokumentieren"**) und **bestehende bearbeiten**; die Einträge werden verschlüsselt lokal gespeichert (AES-GCM-256; der Schlüssel wird aus Ihrem Passwort abgeleitet) und bei Verbindung automatisch gesendet. **Datei-Anhänge** können offline nicht erfasst werden — reichen Sie sie nach, sobald Sie online sind.
- **Erfassung überall offline (seit, #1499):** Sie müssen dafür **kein Dossier mehr geöffnet** haben. Tippen Sie offline im **„+"-Menü** auf **„Kontakt dokumentieren"** bzw. **„Aufgabe erstellen"** (oder öffnen Sie die Formulare `/events/new/` / `/workitems/new/` direkt) — es öffnet sich **an der gewohnten Adresse** eine echte Erfassungsmaske: Dokumentationstyp wählen, optional eine „offline mitgenommene" Person zuordnen, speichern. Der Eintrag wird verschlüsselt lokal abgelegt und beim nächsten Netzkontakt automatisch gesendet.
- **Kontakte ohne Personenbezug** (anonyme Kontakte) und **Aufgaben ohne Person** (Standalone) lassen sich damit jetzt **auch offline neu beginnen** — wählen Sie im Personen-Auswahlfeld **„— ohne Person —"**. Erfordert der gewählte Dokumentationstyp eine Kontaktstufe, ist „ohne Person" deaktiviert; ordnen Sie dann eine mitgenommene Person zu (der Server prüft die Kontaktstufe beim Synchronisieren erneut). Die frühere Sackgasse mit „geht nur im Dossier"-Wegweiser (#1483/#1485) entfällt.
- **Aufgaben-Erfassung ist Fachkräften vorbehalten.** Öffnen Sie als Assistenz offline die Aufgaben-Maske, sehen Sie statt der Felder einen Hinweis — so entsteht kein Eintrag, der beim Senden ohnehin abgelehnt würde.
- Ist für die Offline-Erfassung **noch nichts vorbereitet** (die Einrichtung wurde nie online geöffnet, der lokale Cache ist leer, oder die installierte App ist noch nicht aktualisiert), zeigt die Maske einen kurzen Hinweis **„einmal online öffnen"** statt der Formularfelder.
- **Datei-Anhänge** können offline weiterhin nicht erfasst werden — reichen Sie sie nach, sobald Sie online sind.
- **`/offline/` ist seit nur noch ein technischer Fallback**, kein Einstiegspunkt mehr: Die Seite „Keine lokale Ansicht verfügbar" erscheint ausschließlich, wenn für die aufgerufene Adresse **keine** lokale Ansicht vorliegt (z. B. eine unbekannte URL, oder die App wurde noch nicht auf den aktuellen Stand aktualisiert) — mit Links zur Personenliste, den Aufgaben und zum Zeitstrom sowie „Erneut versuchen".
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

> **Anmeldung während der Übertragung erforderlich:** Verlangt der Server beim Synchronisieren eine erneute Anmeldung, die Bestätigung des zweiten Faktors oder einen Passwortwechsel, gilt das **nicht** als gespeichert — die Übertragung pausiert, alle noch nicht übertragenen Einträge bleiben unverändert auf Ihrem Gerät, und die Offline-Ansicht weist mit der Anzahl darauf hin. Melden Sie sich erneut an; danach läuft die Übertragung ohne doppelte Einträge weiter. **Ausnahme Passwortwechsel:** Verlangt der Server einen Passwortwechsel, lassen sich die Einträge vorher nicht übertragen, und der Wechsel löscht die lokal gespeicherten Offline-Daten (siehe oben) — sichern Sie deren Inhalte, bevor Sie das neue Passwort bestätigen. Der Hinweis in der App sagt das in diesem Fall ausdrücklich.

> **Keine Datei-Anhänge offline:** Ereignisse mit Datei-Anhängen können offline **nicht** gespeichert werden. Aus Sicherheitsgründen werden keine unverschlüsselten Datei-Blobs im Browser abgelegt. Erfassen Sie in diesem Fall zuerst das Ereignis ohne Datei und hängen Sie die Datei nach, sobald Sie wieder online sind.

---

## 9. Rollen und Berechtigungen

Anlaufstelle unterscheidet fünf Rollen. Ihre Rolle wird von der Anwendungsbetreuung Ihrer Einrichtung festgelegt.

> **Zielgruppe dieses Abschnitts:** die **praktische** Sicht — was Sie im Arbeitsalltag mit Ihrer Rolle tun dürfen. Die fachliche Begründung des Modells steht im [Fachkonzept (Role — Die Rolle)](fachkonzept-anlaufstelle.md#role--die-rolle), die technischen Details (DB-Werte, RLS-Scoping) in der [FAQ § C](faq.md#die-fünf-rollen), das Anlegen und Verwalten von Rollen im [Admin-Guide § 2.4](admin-guide.md#rollenbeschreibung).

### Rollenübersicht

| Rolle | Anzeigename | Kurzbeschreibung |
|---|---|---|
| `assistant` | Assistenz | Eingeschränkte Erfassung ohne Zugriff auf Personendaten der Stufe „Mit Zusatzangaben" |
| `staff` | Fachkraft | Standardrolle für Mitarbeitende in der Dokumentation |
| `lead` | Leitung | Alle Fachkraft-Funktionen plus Auswertungen und Leitungsaufgaben |
| `facility_admin` | Anwendungsbetreuung | Vollzugriff auf alle Bereiche und Einstellungen **Ihrer Einrichtung** (Audit-Log, DSGVO-Paket, Benutzerverwaltung) |
| `super_admin` | Systemadministration | Träger-/Hosting-Konzept: facility-übergreifend, Bootstrap-Tools, getrennter `/system/`-Bereich. **In der normalen Fach-UI nicht sichtbar** — Endnutzer interagieren nicht mit ihm. |

> **Hinweis:** Die Systemadministration (`super_admin`) ist eine Hosting-/Träger-Rolle. Sie hostet die Installation und legt die erste Einrichtung sowie die erste Anwendungsbetreuung an — beides in der Django-Adminoberfläche (`/admin-mgmt/`); der `/system/`-Bereich zeigt Organisation und Einrichtungen nur an. Im fachlichen Tagesgeschäft begegnen Sie ihr in der Regel nicht — beide Bereiche sind Ihnen als facility-gebundener User nicht zugänglich.

### Was darf wer?

Tabelle nur für die vier facility-gebundenen Rollen — `super_admin` ist hier ausgeklammert, weil er nicht im Fach-UI arbeitet:

| Funktion | Assistenz | Fachkraft | Leitung | Anwendungsbetreuung |
|---|---|---|---|---|
| Zeitstrom / Startseite einsehen | Ja | Ja | Ja | Ja |
| Anonyme Kontakte dokumentieren | Ja | Ja | Ja | Ja |
| Kontakte zu Personen der Stufe „Nur Pseudonym" dokumentieren | Ja | Ja | Ja | Ja |
| Personen anlegen und suchen | Ja | Ja | Ja | Ja |
| Personendaten der Stufe „Mit Zusatzangaben" einsehen | Nein | Ja (eigene Einrichtung) | Ja | Ja |
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

Das Audit-Log (`/audit/`) protokolliert automatisch sicherheitsrelevante Aktionen: Anmeldungen, Zugriffe auf Daten der Stufe „Mit Zusatzangaben", Exporte, Löschungen und Stufenwechsel. Es kann nicht verändert werden und dient der Nachvollziehbarkeit im Sinne der DSGVO. Zugriff hat die Anwendungsbetreuung (`facility_admin`) für die eigene Einrichtung.

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

### Fall anlegen

1. Klicken Sie auf der Fallliste auf **Fall anlegen** (oder navigieren Sie zu `/cases/new/`).
2. Füllen Sie das Formular aus:
   - **Titel** (Pflichtfeld): Eine kurze Bezeichnung für den Fall (z. B. „Wohnungssuche", „Suchtberatung").
   - **Person:** Beginnen Sie mit der Eingabe des Pseudonyms – es erscheint eine Vorschlagsliste. Wählen Sie die passende Person aus. Ein Fall kann auch ohne Person erstellt werden.
   - **Beschreibung:** Ausführlichere Informationen zum Fall (optional).
   - **Fallverantwortlich:** Wählen Sie die zuständige Person aus dem Dropdown (optional). Nur Fachkräfte, Leitungen und Administratoren Ihrer Einrichtung stehen zur Auswahl.
3. Klicken Sie auf **Fall anlegen**.

Sie werden zur Detailseite des neuen Falls weitergeleitet.

> **Tipp:** Wenn Sie von der Detailseite der Person aus über **Fall anlegen** starten, ist die Person bereits vorausgefüllt.

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

### Fall löschen

Ein versehentlich angelegter Fall lässt sich löschen. „Schließen" ist dafür **nicht** das richtige Mittel: Es ist eine fachliche Aussage über den Hilfeverlauf, und ein Fall, den es nie gab, hat keinen Verlauf.

1. Öffnen Sie die Fall-Detailseite.
2. Klicken Sie im Menü **Weitere Aktionen** (⋮) auf **Löschen**.
3. Die folgende Seite sagt Ihnen, welcher der beiden Wege gilt:
   - **Direkt in den Papierkorb** — bei Personen der Kontaktstufe „Anonym" oder „Nur Pseudonym".
   - **Löschantrag mit Bestätigung** — bei einer Person der Stufe „Mit Zusatzangaben". Geben Sie eine Begründung ein; die Seite nennt gleich, wer den Antrag bestätigen kann. Bestätigt wird er über **Datenschutz → Löschanträge**, wie bei Personen und Kontakten. Solange der Antrag läuft, bleibt der Fall unverändert nutzbar und ist als „Löschung beantragt" gekennzeichnet.
4. Bestätigen Sie.

Was dabei passiert:

- Der Fall verschwindet aus Fallliste, Statistik und Zeitstrom und liegt im **Papierkorb**. Episoden, Wirkungsziele und Dateianhänge gehen mit — sie kommen beim Wiederherstellen vollständig zurück.
- **Zugeordnete Kontakte bleiben bei der Person.** Sie werden nicht mitgelöscht und blockieren die Löschung auch nicht; sie verlieren ihre Fallzuordnung erst, wenn der Fall endgültig getilgt wird.
- Nach Ablauf der Papierkorb-Frist (Standard 30 Tage, dieselbe wie für gelöschte Personen) wird der Fall automatisch **anonymisiert**: Titel, Beschreibung, Zusatzfelder, Episoden, Wirkungsziele und Dateien sind dann unwiderruflich weg. Danach ist keine Wiederherstellung mehr möglich.
- In einer Datenauskunft nach Art. 15 DSGVO erscheint ein Fall im Papierkorb bis zu diesem Zeitpunkt in einem **eigenen Abschnitt** „Fälle im Papierkorb" — mit dem Hinweis, seit wann er gelöscht und ob er bereits endgültig anonymisiert ist.

> **Wiederherstellen** kann nur die **Anwendungsbetreuung** — über **Papierkorb** in der Kopfzeile der Fallliste. Dort steht auch **Jetzt endgültig anonymisieren**, wenn nicht auf die Frist gewartet werden soll.

> **Offline:** Ein gelöschter Fall verschwindet beim nächsten Abgleich auch aus den offline gespeicherten Daten auf Ihrem Gerät. Fälle lassen sich offline ohnehin nicht anlegen, bearbeiten oder löschen.

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
| Fall anlegen und bearbeiten | Nein | Ja | Ja | Ja |
| Fall schließen / wiedereröffnen | Nein | Nein | Ja | Ja |
| Fall löschen bzw. Löschung beantragen | Nein | Ja | Ja | Ja |
| Fall-Papierkorb: wiederherstellen, endgültig anonymisieren | Nein | Nein | Nein | Ja |
| Kontakte zuordnen / entfernen | Nein | Ja | Ja | Ja |
| Episoden verwalten | Nein | Ja | Ja | Ja |
| Wirkungsziele und Meilensteine | Nein | Ja | Ja | Ja |

---

> **Weitere Fragen?** Die [FAQ](faq.md) beantwortet häufige Fragen zu Datenschutz, 2FA, Offline-Modus, Löschfristen und mehr.

---

*Anlaufstelle – Dokumentationssystem für niedrigschwellige soziale Hilfsangebote*

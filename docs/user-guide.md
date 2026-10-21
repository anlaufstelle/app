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

> **Konto gesperrt nach mehreren Fehlversuchen?** Nach **10 fehlgeschlagenen Anmeldungen innerhalb von 15 Minuten** von derselben IP-Adresse wird Ihr Konto für Anmeldungen von dieser Adresse gesperrt. Die Systemadministration (`super_admin`) ist von dieser Kontosperre ausgenommen; Ratelimits und 2FA bleiben davon unberührt. Auf der Login-Seite erscheint dann der Hinweis, dass Ihr Konto vorübergehend gesperrt ist — auch wenn Sie das richtige Passwort eingeben. Die Sperre endet von selbst, sobald weniger als 10 Fehlversuche in den letzten 15 Minuten liegen — ohne neue Fehlversuche spätestens 15 Minuten nach dem letzten. Ein weiterer Versuch mit falschem oder richtigem Passwort zählt mit und hält die Sperre aufrecht. Unabhängig davon nimmt die Anmeldung höchstens 5 Versuche pro Minute von derselben IP-Adresse und 10 pro Stunde für denselben Benutzernamen an; darüber zeigt der Browser statt der Login-Seite nur eine leere Fehlerseite (HTTP 429), und es hilft nur Abwarten, bis das Zeitfenster endet. Nach zehn Fehlversuchen ist das häufig zuerst zu sehen; diese abgewiesenen Versuche zählen für die Sperre nicht. Die Sperre selbst heben Sie schneller über drei Selbsthilfe-Wege auf der Login-Seite auf (Refs #869):
>
> - **Passwort vergessen?** — Klassischer Reset per E-Mail. Wenn Sie ein neues Passwort setzen, wird Ihr Konto automatisch entsperrt.
> - **Konto entsperren per E-Mail** — Schickt Ihnen einen Entsperr-Link an Ihre hinterlegte E-Mail-Adresse, der 30 Minuten gültig ist. Kein neues Passwort nötig.
> - **Backup-Code einlösen** — Wenn Sie 2FA aktiviert haben, geben Sie Username + einen Ihrer Backup-Codes ein. Jeder Code ist einmalig.
>
> Wenn keiner dieser Wege funktioniert, bitten Sie die Anwendungsbetreuung Ihrer Einrichtung um Entsperrung (Verwaltung → Zugänge). Alle Vorgänge werden im Audit-Log protokolliert.

### Passwort ändern

1. Klicken Sie auf Ihren Namen am Fuß der Sidebar (mobil: **Mehr → Profil**), um **Mein Profil** zu öffnen.
2. Wählen Sie **Passwort ändern** (URL: `/password-change/`).
3. Geben Sie Ihr aktuelles Passwort ein.
4. Geben Sie das neue Passwort zweimal ein.
5. Klicken Sie auf **Passwort ändern**.

> **Tipp:** Wählen Sie ein sicheres Passwort (mindestens 12 Zeichen, Groß-/Kleinbuchstaben, Ziffern). Das System prüft die Mindestanforderungen.

> **Einladung neuer Nutzer:** Die erste Einladung an neue Nutzer kommt jetzt per Token-Link (kein Klartext-Initialpasswort mehr). Beim ersten Klick auf den Link legen Sie selbst ein Passwort fest.

### Zwei-Faktor-Authentifizierung (2FA)

Die Anlaufstelle unterstützt zeitbasierte Einmalcodes (TOTP) als zweiten Faktor beim Login. In Produktion ist 2FA für alle Rollen verpflichtend: für Systemadministration und Anwendungsbetreuung sofort, für Leitung, Fachkraft und Assistenz nach einer Übergangsfrist von 30 Tagen ab der ersten Anmeldung. Während der Frist erinnert ab 14 verbleibenden Tagen ein Hinweis; danach führt die Anmeldung direkt zur Einrichtung. Der Betrieb kann diese Rollenvorgabe ändern, und die Anwendungsbetreuung kann 2FA zusätzlich für einzelne Zugänge oder die ganze Einrichtung sofort verpflichtend machen. Wo keine Pflicht besteht, können Sie 2FA freiwillig aktivieren.

**Einrichtung (einmalig):**

1. Name am Fuß der Sidebar (mobil: **Mehr → Profil**) → **Mein Profil → 2FA verwalten** (URL: `/mfa/settings/`).
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

Unter `/mfa/settings/` → **2FA deaktivieren**. Dies ist **nicht möglich**, wenn 2FA für Sie verpflichtend ist — über Ihre Rolle, Ihre Einrichtung oder Ihren Zugang. In diesem Fall wenden Sie sich an Ihre Administration.

**Backup-Codes (für den Notfall):**

Bei der 2FA-Einrichtung erhalten Sie **10 einmalig nutzbare Backup-Codes**. Bewahren Sie diese sicher auf — z. B. ausgedruckt im Geldbeutel oder im Passwort-Manager. Falls Sie Ihr Telefon verlieren oder die Authenticator-App zurückgesetzt wurde, können Sie am 2FA-Login einen Backup-Code statt eines TOTP-Codes eingeben. Jeder Code funktioniert nur einmal.

> **Alle Codes verbraucht oder verloren?** Wenden Sie sich an die Anwendungsbetreuung Ihrer Einrichtung. Sie kann Ihren zweiten Faktor zurücksetzen: Dabei werden alle Authenticator-Apps, Passkeys und Backup-Codes Ihres Zugangs entfernt, und Sie werden überall abgemeldet. Ihr Passwort bleibt gültig. Ist 2FA für Sie verpflichtend, richten Sie bei der nächsten Anmeldung 2FA und neue Backup-Codes ein.

**Neues Authenticator-Gerät einrichten:** Nach der Anmeldung mit TOTP oder Backup-Code unter `/mfa/settings/` **Neues Gerät einrichten** wählen. Wenn Sudo verlangt wird, Passwort und einen aktuellen TOTP- oder weiteren unbenutzten Backup-Code eingeben. Den neuen QR-Code scannen, den Code der neuen App bestätigen, die neuen Backup-Codes speichern und quittieren. Erst die Quittung ersetzt die bisherigen bestätigten Authenticator-Geräte; Ihre Passkeys bleiben erhalten. Das funktioniert auch bei verpflichtender 2FA. Ein Backup-Code-Login allein ersetzt das Gerät nicht. Gerätewechsel und freiwillige Deaktivierung beenden andere angemeldete Sitzungen des Kontos nicht; bei Verlust oder Diebstahl eines Geräts mit möglicherweise offener Sitzung den Admin-Reset verwenden (Dienstanweisung der Einrichtung).

**Bisherige Backup-Codes ersetzen:** Vor dieser Härtung erzeugte Codes sind ungültig. Erzeugen Sie mit Ihrer funktionierenden Authenticator-App neue Codes; ohne nutzbaren Faktor wenden Sie sich an die Anwendungsbetreuung. Auch nach einer Änderung des Serverschlüssels (`SECRET_KEY`) brauchen Sie neue Codes.

**Passkey / Sicherheitsschlüssel (optional, zusätzlich zur Authenticator-App):**

Ergänzend zur Authenticator-App können Sie einen **Passkey** als zweiten Faktor hinterlegen — z. B. die Biometrie Ihres Geräts (Fingerabdruck/Gesicht), ein Smartphone oder einen USB-Sicherheitsschlüssel (FIDO2). Ein Passkey ist phishing-resistent, weil er an die Adresse der Anwendung gebunden ist.

1. Richten Sie **zuerst** die Authenticator-App (TOTP) ein — ein Passkey ist nur **zusätzlich** dazu möglich. So bleiben Ihre Backup-Codes der Wiederherstellungspfad, falls ein Passkey verloren geht.
2. Unter `/mfa/settings/` im Abschnitt **Passkeys** auf **Passkey hinzufügen** klicken und den Anweisungen des Browsers folgen.
3. Sie können mehrere Passkeys registrieren (z. B. Laptop und Smartphone) und einzelne wieder **entfernen** (verlangt eine erneute Bestätigung Ihres Passworts/2FA).

Beim Login bietet die 2FA-Seite dann **Mit Passkey anmelden** an — alternativ nutzen Sie weiterhin die Authenticator-App oder einen Backup-Code.

> **Hinweis:** Passkeys sind an die Web-Adresse gebunden. Ein auf `dev.anlaufstelle.app` registrierter Passkey funktioniert nicht auf einer anderen Adresse — registrieren Sie ihn dort, wo Sie die Anwendung tatsächlich nutzen.

### Abmelden

Klicken Sie am Fuß der Sidebar (mobil unter **Mehr**) auf **Abmelden**. Aus Datenschutzgründen werden Sie nach einer konfigurierten Inaktivitätszeit automatisch abgemeldet (Standard: 30 Minuten).

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

**Ohne Internetverbindung** funktioniert der Knopf ebenfalls: Der Inhalt wird verdeckt, der lokale Schlüssel für die Offline-Daten wird gelöscht und der Browser wechselt auf die neutrale Seite. Eine Abmeldung am Server ist offline nicht möglich — dafür sind die Offline-Daten ohne erneute Anmeldung nicht mehr lesbar, die verschlüsselten Daten bleiben zunächst auf dem Gerät. Ein Neustart löscht sie nicht. Abgelaufene Offline-Bestände (nach 48 Stunden) räumt die App erst mit geladenem Schlüssel auf, etwa beim Öffnen nach erneuter Anmeldung; noch nicht übertragene Einträge werden durch diesen Fristablauf nicht gelöscht.

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

### Datum und Uhrzeit eingeben

Bei geladenem JavaScript folgen Datumsfelder der gewählten App-Sprache: Deutsch `TT.MM.JJJJ`, Englisch `YYYY-MM-DD`. Uhrzeiten werden im 24-Stunden-Format `HH:MM` eingegeben; Felder mit Sekundengenauigkeit erlauben zusätzlich `:SS`. Das gilt auch für vorbereitete Offline-Formulare. Ohne JavaScript bleiben native Browserfelder erhalten; deren Anzeige richtet sich nach dem Browser.

Ungültige Kalendertage (zum Beispiel `30.02.2026`) und Werte außerhalb der erlaubten Grenzen verhindern das Absenden. Korrigieren Sie den Wert im angezeigten Format. Die Auswahl zur doppelten Stunde bei der Zeitumstellung bleibt erhalten.

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

In der rechten Seitenspalte steht die Team-Fokusbox **„Team-Aufgaben mit Handlungsbedarf“** mit bis zu **8 Einträgen**, unabhängig von der Zuweisung. Sie gruppiert nach **Überfällig**, **Heute fällig**, **Dringend · Wichtig** und **In Bearbeitung**; jede Aufgabe erscheint nur in der höchsten zutreffenden Druckstufe. Innerhalb der Gruppen wird nach Fälligkeit, Priorität und Erstellzeit sortiert. Offene normale Aufgaben ohne Frist bleiben über **Zur Aufgabenübersicht** erreichbar.

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

Oberhalb des Feeds steht die dauerhaft sichtbare Box **Aktueller Dienst** mit Kontakten, neuen Aufgaben, neuen Personen und wichtigen Vorkommnissen der laufenden Schicht. Sie ist unabhängig vom gewählten Zeitstrom-Filter und erscheint nur, wenn gerade eine eingerichtete Schicht läuft.

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

Direkt über dem Feed gibt es Filter-Chips und eine Typauswahl:

- **Filter-Chips**: Alle · Kontakte · Aktivitäten · Aufgaben · Hausverbote
- **Alle Dokumentationstypen**: nur Einträge eines bestimmten Typs (z. B. „Kontakt", „Krisengespräch") — Sie sehen nur die Typen, die Ihre Rolle einsehen darf

Filteränderungen laden nur den Feed neu (HTMX), ohne die ganze Seite zu aktualisieren.

### Hausverbot-Banner

Wenn aktive Hausverbote in Ihrer Einrichtung bestehen, werden diese als rotes Banner unterhalb der Überschrift angezeigt.

---

### Kontakte-Liste

Die **Kontakte-Liste** (`/events/`) ist die Arbeitsliste für Kontakte über mehrere Tage, zum Beispiel „alle Krisengespräche im letzten Monat“ oder „meine Erfassungen diese Woche“. Der Zeitstrom bleibt die Tagesansicht mit Aktivitäten, Aufgaben und Hausverboten.

**Öffnen:** Wählen Sie **Kontakte** direkt unter **Zeitstrom** in der Seitenleiste, mobil **Mehr → Kontakte**. Nur auf `/events/` ist dieser Eintrag aktiv; Kontakt-Detailseiten bleiben dem Zeitstrom zugeordnet. Auch auf der Zeitstrom-Seite führt ein lokaler Link **Kontakte** zur Liste: Er übernimmt den ausgewählten Tag, Dokumentationstyp und die Schicht.

**Filter kombinieren:** Wählen Sie die gewünschten Angaben und anschließend **Filtern**. Unter **Weitere Filter** finden Sie Fall, Erfasst von, Schicht und Anonym. Ist einer davon gesetzt, bleibt der Bereich offen und nennt die aktiven Filter. Das Formular funktioniert auch ohne JavaScript.

| Filter | Verwendung |
|---|---|
| **Von / Bis** | Zeitraum des Kontaktzeitpunkts; ohne Grenzen alle noch vorhandenen Kontakte |
| **Schnellwahl** | **Diese Woche** oder **Letzter Monat** setzt den Zeitraum; für eigene Datumsgrenzen wieder **Alle** wählen |
| **Typ** | Dokumentationstyp, soweit Ihre Rolle ihn sehen darf |
| **Person** | Auswahl einer aktiven Person nach Pseudonym; keine Autocomplete-Suche |
| **Fall** | Auswahl eines Falls; Filter und Fallanzeige erst ab Fachkraft |
| **Erfasst von / Nur meine** | Kontakte anderer Mitarbeitender oder die eigenen; beide Filter wirken gemeinsam |
| **Schicht** | Das eingerichtete Zeitfenster an jedem Tag des Zeitraums; eine Nachtschicht zählt zum Tag ihres Beginns |
| **Anonym** | Alle, nur anonyme oder nur nicht anonyme Kontakte |
| **Suchbegriff** | Ab zwei Zeichen in Pseudonym und Ereignis-Suchtext suchen |

Der Suchraum entspricht der globalen Suche (#1862, [ADR-043](adr/043-suchraum-volltextsuche.md)): Der Ereignis-Suchtext enthält nur unverschlüsselte Felder mit wirksamer Sensibilität **Normal**. Verschlüsselte, erhöht oder hoch eingestufte Feldwerte, Falltitel und Dateiinhalte werden nicht durchsucht. Es gibt keine Suchvorschläge. Kontakte von Personen im Papierkorb sind bei einer Suchbegriffsabfrage ausgeschlossen. **Filter zurücksetzen** öffnet wieder die ungefilterte Liste.

**Sichtbarkeit:** Assistenz, Fachkraft, Leitung und Anwendungsbetreuung sehen Kontakte der eigenen Einrichtung, auch Erfassungen anderer Mitarbeitender, mit derselben Sensibilitätsgrenze wie im Zeitstrom: Assistenz nur **Normal**, Fachkraft bis **Erhöht**, Leitung und Anwendungsbetreuung auch **Hoch**. Gelöschte Kontakte und Kontakte im Papierkorb fehlen. Die Systemadministration hat keinen Zugang zu dieser Arbeitsliste.

**Ergebnisse:** Neueste Kontakte stehen zuerst, mit 25 Einträgen je Seite. Die Karten zeigen Zeitpunkt, Typ, Person beziehungsweise „Anonym“, ab Fachkraft den Fall sowie „Erfasst von“. Klicken Sie auf die Kopfzeile oder drücken Sie dort Enter beziehungsweise die Leertaste, um die Angaben auf- und zuzuklappen. Sie sehen dieselben rollenberechtigten Feldwerte wie im Zeitstrom. **Zum Eintrag** öffnet weiterhin die Detailansicht. Die Filter stehen in der Adresse und bleiben beim Teilen, Seitenwechsel und mit der Zurück-/Vorwärts-Taste erhalten, einschließlich der Formularwerte.

Die Liste bietet **keinen Export und keine Massenaktionen**. Exportmöglichkeiten bleiben im Bereich [Statistik und Export](#7-statistik-und-export). Listen- und Filteraufrufe erzeugen **keinen Audit-Eintrag**; beim Öffnen einer Detailansicht gelten deren Zugriffsregeln.

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

Daneben steht **Speicher** mit der aktuellen Belegung, dem Kontingent und einem Link zur Seite **Dateien**.

---

## 3. Kontakt dokumentieren (Event erstellen)

Ein **Event** ist ein einzelner dokumentierter Kontakt – z. B. ein Beratungsgespräch, eine Spritzentauschausgabe oder ein anonymer Besuch.

### Einen Kontakt dokumentieren

1. Klicken Sie in der Sidebar auf **Neu erstellen** und wählen Sie **Kontakt dokumentieren** (oder navigieren Sie zu `/events/new/`).
2. **Dokumentationstyp wählen:** Wählen Sie aus der Liste den passenden Typ (z. B. „Kontakt", „Krisengespräch", „Spritzentausch"). Die verfügbaren Typen sind von Ihrer Einrichtung konfiguriert.
3. **Felder ausfüllen:** Nach der Typauswahl werden die zugehörigen Eingabefelder geladen. Füllen Sie alle relevanten Felder aus.
4. **Zeitpunkt:** Das Feld „Zeitpunkt" ist automatisch auf die aktuelle Uhrzeit gesetzt. Sie können es anpassen, wenn Sie einen Kontakt nachträglich erfassen.
5. **Person zuordnen (optional):**
   - Für einen **anonymen Kontakt** (ohne Pseudonym): Lassen Sie das Personen-Feld leer. Ohne zugeordnete Person wird der Kontakt anonym gespeichert.
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
- **Verschlüsselung:** Alle Anhänge werden verschlüsselt gespeichert (Fernet, AES-128-CBC mit HMAC-SHA256). Die Schlüssel liegen im installationsweiten Schlüsselring `ENCRYPTION_KEYS` (alternativ `ENCRYPTION_KEY`), gemeinsam für alle Einrichtungen; jede Einrichtung hat also keinen eigenen Schlüsselring. Das bedeutet: Die Datei ist auf dem Server nicht im Klartext lesbar, sondern nur über Anlaufstelle selbst abrufbar.
- **Bild-Metadaten werden entfernt:** Bei Bildern (JPG, PNG, WebP, TIFF — nicht bei GIF und BMP) werden beim Hochladen die eingebetteten Metadaten gelöscht — vor allem der **GPS-Aufnahmeort** (bei Handyfotos oft der Wohn- oder Aufenthaltsort der betroffenen Person), außerdem Gerätemodell, Aufnahmezeit und das eingebettete Vorschaubild, das den unbeschnittenen Originalausschnitt zeigen kann. Das Bild selbst bleibt unverändert sichtbar, hochkant aufgenommene Fotos bleiben hochkant. Bei PDF- und Office-Dateien passiert das **nicht** — dort kann der Name der bearbeitenden Person in den Dokumenteigenschaften stehen bleiben.
- **Bildgrenzen:** Rasterbilder dürfen standardmäßig insgesamt höchstens 40 Millionen Pixel über alle Frames/Seiten und maximal 100 Frames/Seiten haben; zusätzlich muss eine konservative Bildpufferschätzung innerhalb von 256 MiB bleiben. Größere Bilder werden mit einer Meldung abgewiesen, kleine Mehrseitenbilder bleiben erlaubt. GIF-Metadaten bleiben weiterhin erhalten. WebP, PNG/APNG, GIF und ICO werden schon vor dem Bildöffnen anhand ihrer Header geprüft. Zugelassene SVG-Dateien werden unverändert gespeichert und nicht rasterisiert. Die Prüfungen garantieren keine Obergrenze für den gesamten Encoder-RAM.
- **Maximale Größe:** Standardmäßig sind **bis zu 10 MiB pro Datei** erlaubt. Ihre Anwendungsbetreuung kann diesen Wert bis zur installationsweiten Betreibergrenze `FILE_VAULT_MAX_UPLOAD_BYTES` anpassen (Vorgabe: 50 MiB). Es gilt das strengere der beiden Limits.
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

> **Gleichzeitige Bearbeitung:** Bei einem Versionskonflikt wird nichts gespeichert. Solange Sie den Datensatz und die Felder weiterhin bearbeiten dürfen, stehen Ihre eigenen Eingaben im Formular; der Abschnitt „Aktueller Stand“ zeigt die zulässigen Serverwerte und markiert Abweichungen. Prüfen oder kopieren Sie die Eingaben und speichern Sie erst dann bewusst erneut: Ihre Formularwerte ersetzen dabei die entsprechenden Serverwerte. Auch nach Speicher- oder Uploadfehlern bleiben weiterhin zulässige Texte im Formular. Dateien müssen Sie erneut auswählen (#2539).

### Kontakt löschen

1. Öffnen Sie das Ereignis.
2. Klicken Sie auf **Löschen**.
3. Geben Sie eine Begründung ein.
4. Bestätigen Sie die Löschung.

> **Wichtig:** Wenn der Kontakt einer **Person der Kontaktstufe „Mit Zusatzangaben"** zugeordnet ist, wird kein sofortiges Löschen durchgeführt. Stattdessen wird automatisch ein **Löschantrag** gestellt, der von einer zweiten Person mit dem Recht „Löschbestätigung“ genehmigt werden muss (4-Augen-Prinzip). Sie erhalten eine entsprechende Rückmeldung.

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

> **Hinweis:** Pseudonyme ändern und die Kontaktstufe hochsetzen dürfen Sie ab Fachkraft. Eine Person der Stufe „Mit Zusatzangaben“ herabsetzen dürfen nur Leitung oder Anwendungsbetreuung. Eine Änderung der Kontaktstufe wird automatisch im Audit-Log protokolliert.

> **Gleichzeitige Bearbeitung:** Bei einem Versionskonflikt wird nichts gespeichert. Solange Sie den Datensatz und die Felder weiterhin bearbeiten dürfen, stehen Ihre eigenen Eingaben im Formular; der Abschnitt „Aktueller Stand“ zeigt die zulässigen Serverwerte und markiert Abweichungen. Prüfen oder kopieren Sie die Eingaben und speichern Sie erst dann bewusst erneut: Ihre Formularwerte ersetzen dabei die entsprechenden Serverwerte. Auch nach Speicher- oder Uploadfehlern bleiben weiterhin zulässige Texte im Formular. Dateien müssen Sie erneut auswählen (#2539).

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

Aufgaben ohne Person bleiben sichtbar. Aufgaben zu Personen im Papierkorb oder zu inaktiven Personen fehlen in Posteingang, Zeitstrom, Fokus, Profil, Navigationszählern und Übergabe; ihre Detaillinks und für Bearbeitungsberechtigte auch ihre Bearbeitungslinks liefern 404. Auch Kontakte, Hausverbote und Aktivitäten mit Personen-, Ereignis-, Aufgaben- oder Fallziel im Zeitstrom schließen diese Personen aus; Einträge ohne Personenbezug bleiben sichtbar. Nach Wiederherstellung der weiterhin aktiven Person ist der zulässige Aufgabenbestand wieder sichtbar. Auch Favoriten und zuletzt besuchte Personen im Profil schließen gelöschte oder inaktive Personen aus. Ereignislisten und Kontaktzähler im Profil, der heutige Kontaktzähler im Cockpit sowie Ereignisse in der Übergabe berücksichtigen nur für die Rolle sichtbare, nicht gelöschte Dokumentation aktiver Personen außerhalb des Papierkorbs oder ohne Personenbezug.

Die gewöhnliche Personenakte und ihre Lösch-, Zusammenführungs-, Herabstufungs- und Aufbewahrungssperrformulare liefern für gelöschte oder inaktive Personen 404. Auch die Ereignisliste, Ereignis-Detail- und Anhanglinks, die Ereignissuche und der gewöhnliche Ereignis-CSV-Export schließen Ereignisse zu gelöschten oder inaktiven Personen aus; zulässige Ereignisse ohne Person bleiben sichtbar. Statusantworten und deren Wiederholungen liefern für solche Personenaufgaben 404 vor der Ausgabe von Aufgabeninhalten. Der beim Nachladen angezeigte Erledigt-Zähler zählt nur lesbare Aufgaben. Ein offener Löschantrag allein sperrt diese Leser nicht; Wiederherstellung einer weiterhin aktiven Person macht den zulässigen Bestand wieder sichtbar. Die personenbezogene Statistikrangliste zeigt nur aktive Personen außerhalb des Papierkorbs; die nächste sichtbare Person rückt nach. Aggregierte Statistikzahlen bleiben davon unberührt.

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
- **Erledigt in den letzten 7 Tagen:** Erledigte oder verworfene Einträge

### Status ändern

Direkt in der Inbox-Karte eines WorkItems können Sie den Status per Klick ändern:

Bei einer **offenen** Aufgabe:

- **„Übernehmen"** – übernimmt die Aufgabe automatisch auf Ihren Namen
- **„Als erledigt markieren"** – schließt die Aufgabe ab
- **„Als nicht relevant schließen"** – verwirft die Aufgabe, ohne sie zu löschen

Bei einer Aufgabe **in Bearbeitung**: **„Erledigt"** schließt sie ab; **„Zurücksetzen"** setzt sie wieder auf offen.

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

> **Gleichzeitige Bearbeitung:** Bei einem Versionskonflikt wird nichts gespeichert. Die Bearbeitungsseite wird erneut geöffnet und zeigt den gespeicherten Stand. Prüfen Sie diesen Stand und geben Sie Ihre eigenen, noch nicht gespeicherten Änderungen erneut ein.

### Filter: Mir zugewiesen

In der Inbox-Ansicht können Sie den Filter **„Mir zugewiesen"** aktivieren. Dann sehen Sie ausschließlich Aufgaben und Hinweise, die Ihnen persönlich zugewiesen sind — allgemeine (nicht zugewiesene) Einträge und Einträge für andere Personen werden ausgeblendet.

Der Filter ist hilfreich, wenn Sie sich einen klaren Überblick über Ihre eigenen offenen Punkte verschaffen möchten, ohne von der gesamten Teamliste abgelenkt zu werden.

### Bulk-Edit

Wenn Sie mehrere Aufgaben gleichzeitig bearbeiten möchten, nutzen Sie den Bulk-Edit-Modus:

1. Wählen Sie in der Inbox die gewünschten Aufgaben über die **Checkbox** links neben jeder Karte aus.
2. Oben erscheint ein **Bulk-Dropdown** mit den verfügbaren Aktionen.
3. Ändern Sie zentral **Status**, **Priorität** oder **Zuweisung** für alle ausgewählten Einträge auf einmal.
4. Bestätigen Sie die Änderung — alle markierten Einträge werden gemeinsam aktualisiert.

Die Auswahl umfasst nur sichtbare Aufgaben. Nach einem Filterwechsel oder dem Zuklappen erledigter Aufgaben werden verborgene Einträge aus der Auswahl entfernt. Für neue Zuweisungen bieten die Formulare nur aktive, für Aufgaben berechtigte Zugänge derselben Einrichtung an. Im Filter „Zugewiesen an“ bleiben deaktivierte Zugänge verfügbar, damit historische Aufgaben auffindbar bleiben.

### Erinnerung vs. Frist

Aufgaben kennen zwei unterschiedliche Zeit-Felder:

| Feld | Bedeutung |
|---|---|
| **Zu erledigen bis** | Fälligkeitstermin — wann die Aufgabe spätestens erledigt sein muss |
| **Wiedervorlage am** | Datum, ab dessen Vortag die Aufgabe beim Öffnen der Inbox/Liste hervorgehoben wird — üblicherweise vor der Frist |

**Beispiel:** **Zu erledigen bis** ist der 15.04., **Wiedervorlage am** setzen Sie auf den 10.04. Am 09.04. erscheint das gelbe Badge „Morgen fällig“, ab 10.04. das orange Badge „Heute fällig“ bis zum Abschluss. Es gibt keine E-Mail, Push-Nachricht oder Hintergrundbenachrichtigung.

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

### Die drei Reiter im Überblick

Der Bereich **Statistik** hat drei Reiter. Sie unterscheiden sich nach Aufgabe, Schutz der Zahlen und Datenquelle. Alle beziehen sich auf die eigene Einrichtung; Zugriff haben Leitung und Anwendungsbetreuung.

| Reiter | Wofür / für wen | Kleinstfallzahlen und Pseudonyme | Datenquelle |
|---|---|---|---|
| **Übersicht** | Interner Blick auf Kennzahlen, Diagramme und Tabellen | am Bildschirm keine Kleinstfall-Unterdrückung; Top-Personen mit Pseudonym | Monats-Aggregate mit Live-Fallback; Personenkennzahlen live über den Gesamtzeitraum; mit Dokumentationstyp-Filter vollständig live |
| **Datensatz-Berichte** | Vorschau und Dateien für eine bestimmte Frage, etwa **Allgemeine Kennzahlen**, Beispiel-Sachbericht, Beratungsstunden, Spritzentausch oder **Summe von Zahlenfeldern** | Kleinstfallzahlen unterdrückt, keine Personen-Pseudonyme | live; Ausnahme: PDF des Beispiel-Sachberichts hybrid aus Monats-Aggregaten mit Live-Fallback und live gezählten unterschiedlichen Personen |
| **Freigaben** | Interne Kontrolle herausgegebener Berichtszeiträume | keine Statistik | markierte Herausgaben im Audit-Log |

**Gemeinsamer Zeitraum:** Übersicht und Datensatz-Berichte nutzen dieselbe Zeitraum-Leiste; ein Reiterwechsel behält den Zeitraum. Freigaben zeigt die Herausgaben unabhängig davon und reicht den Zeitraum an die anderen Reiter weiter. Der Dokumentationstyp-Filter gilt nur für die Übersicht und deren Ereignisliste, nicht für die Datensatz-Berichte.

**Warum Zahlen abweichen können:**

1. **Unterdrückung:** Datensatz-Berichte unterdrücken kleine Werte nach der K-Anonymitäts-Schwelle der Einrichtung (Standard 5); zusätzliche Werte können zum Schutz vor Rückrechnung entfallen. Die Übersicht zeigt intern die ununterdrückten Zahlen. Unterdrückung allein ist keine Zusicherung, dass eine Herausgabe anonym ist.
2. **Grundgesamtheit:** Die Übersicht zählt Ereignisse unabhängig von ihrer Berichtszuordnung. Berichte zählen je nach Definition beispielsweise zugeordnete Ereignisse, Ereignisse mit Personenbezug, Personen oder Beiträge zu einer Zahlenfeld-Summe. Berichtskarten und Tabellen benennen die jeweilige Menge; ausdrücklich ausgeschlossene Typen können deshalb in der Übersicht, aber nicht in einer Berichtsauswertung enthalten sein.
3. **Datenquelle:** Live-Abfragen sehen den aktuellen Bestand und verlieren gelöschte Ereignisse. Gespeicherte Monats-Aggregate können vergangene Ereigniszahlen erhalten; fehlende Snapshots und Teilmonate werden live abgefragt. Unterschiedliche Personen werden live über den gesamten Zeitraum gezählt, **nicht** als Summe monatlicher Personenzahlen. Beachten Sie den Quellenausweis je Format.

| Sie möchten … | Ort |
|---|---|
| intern den Verlauf der Arbeit ansehen | **Übersicht** |
| Einzelereignisse mit Pseudonymen intern weiterverarbeiten | **Übersicht → Ereignisliste** |
| Eckzahlen oder eine fachliche Auswertung als Datei herausgeben | **Datensatz-Berichte**, etwa **Allgemeine Kennzahlen** |
| frühere Herausgaben und überschneidende Zeiträume prüfen | **Freigaben** |

> **Tipp:** Nutzen Sie für Zahlen nach außen die Datensatz-Berichte. Geben Sie in der Exportleiste Empfänger und Zweck an und prüfen Sie [Freigaben](#freigaben-leitung--admin). Die Vorschau ist keine Herausgabe; der Datei-Download wird protokolliert. Eine Weitergabe braucht auch mit Unterdrückung eine fachliche und datenschutzrechtliche Prüfung.

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

> **Hinweis:** Die Charts werden beim Drucken nicht angezeigt. Nutzen Sie für Berichtsdateien den Reiter „Datensatz-Berichte“.

### Ereignisliste (CSV, intern)

Die Übersicht bietet **„Ereignisliste (CSV, intern, mit Pseudonymen)“**. Die Datei enthält Einzelereignisse des gewählten Zeitraums, bei gewähltem Dokumentationstyp nur diesen Typ. Statische Spalten sind Datum, Uhrzeit, Dokumentationstyp, Person, Kontaktstufe und Altersgruppe; hinzu kommen die für Ihre Rolle sichtbaren dynamischen Felder, ausgenommen Dateifelder (Anhänge bleiben im geschützten Dateidownload). Die Spalte **„Person“** kann ein Pseudonym enthalten. Pseudonyme bleiben personenbezogen: Dies ist **keine anonymisierte Statistik**. Eine Weitergabe setzt eine passende Rechtsgrundlage voraus; für Zahlen nach außen nutzen Sie die Datensatz-Berichte.

1. Wählen Sie im Statistik-Dashboard Zeitraum und gegebenenfalls Dokumentationstyp.
2. Tragen Sie optional **Empfänger** und **Zweck** in der Exportleiste ein.
3. Klicken Sie auf **Ereignisliste (CSV, intern, mit Pseudonymen)**. Die Datei heißt `export_YYYY-MM-DD_YYYY-MM-DD.csv`.

Der erfolgreiche Download wird im Audit-Log als Herausgabe geführt und erscheint unter **Freigaben**.

### PDF-Export

PDF-Dateien erhalten Sie im Reiter **Datensatz-Berichte**: Jede Berichtskarte bietet CSV, JSON, XML und PDF in der gemeinsamen Exportleiste. Wählen Sie den Bericht und prüfen Sie Vorschau, Grundgesamtheit, Unterdrückung und Datenquelle; geben Sie optional Empfänger und Zweck an und klicken Sie auf **PDF-Export**. Der Download enthält die für dieses Format vorbereiteten, unterdrückten Berichtsdaten ohne Personen-Pseudonyme. Der Beispiel-Sachbericht hat dabei eine andere Datenquelle als seine Live-Vorschau (siehe unten). Die Übersicht selbst bietet keinen PDF-Export; auch `internal=1` ist kein interner PDF-Modus.

### Beispiel-Sachbericht

Der Beispiel-Sachbericht erstellt einen strukturierten Sachbericht nach einem festen Beispiel-Layout. Er ist ein anpassbares Beispiel bzw. eine Vorlage — kein verbindlicher oder aktueller Behördennachweis und keine Garantie für landesspezifische Anforderungen.

1. Wählen Sie den Zeitraum. Er gilt für den gesamten Statistik-Bereich und bleibt beim Wechsel zwischen den Reitern erhalten.
2. Öffnen Sie den Reiter **Datensatz-Berichte** und wählen Sie die Karte **Beispiel-Sachbericht**.
3. Klicken Sie unter der Vorschau auf **PDF-Export**. Die Datei wird heruntergeladen (Dateiname: `sample-report-1_YYYY-MM-DD_YYYY-MM-DD.pdf`).

> **Tipp:** Für Halbjahresberichte wählen Sie „Eigener Zeitraum" und setzen Start- und Enddatum auf den 01.01. bzw. 30.06. (oder 01.07. – 31.12.). „Letzte 180 Tage" ist ein rollierendes Fenster und trifft nie exakt ein Kalenderhalbjahr.

**Datenquelle des Beispiel-Sachbericht-PDFs: Monats-Aggregate mit Live-Fallback.** Vollständige vergangene Monate werden aus vorhandenen Monats-Auswertungen gelesen; fehlende Snapshots und Teilmonate werden live berechnet. So können gespeicherte Ereigniszahlen nach Ablauf der Löschfristen erhalten bleiben. „Unterschiedliche Personen“ wird dagegen live über den Gesamtzeitraum gezählt, nicht monatlich addiert. Die Fußzeile nennt die Quelle und gegebenenfalls den Stand der verwendeten Monats-Aggregate. Vorschau und CSV/JSON/XML dieses Berichts rechnen live; sie können deshalb vom PDF desselben Zeitraums abweichen.

### Datensatz-Berichte (CSV / JSON / XML / PDF)

Der Bericht **„Summe von Zahlenfeldern“** erscheint im Reiter **Datensatz-Berichte**, sobald mindestens ein aktiver, nicht ausgeschlossener Ereignis-Typ ein aktives Zahlenfeld zum Berichtsschlüssel `quantity_units` trägt. Typen ohne gebundenes Zahlenfeld oder mit Typ-Berichtsschlüssel `excluded` („Gehört ausdrücklich in keinen Bericht“) werden weder gezählt noch für diesen Bericht unter „Erscheint in“ genannt. Er zeigt je Typ Ereignisse, beitragende Ereignisse und die Stückzahl-Summe des gewählten Zeitraums. Vorschau und CSV/PDF geben dieselben unterdrückten Werte aus; wenn weniger als *k* Ereignisse zur Summe beitragen, bleibt die Summe verborgen.

Der Reiter **Datensatz-Berichte** listet alle Berichtsstandards, die für Ihre Einrichtung eingerichtet sind — der Beispiel-Sachbericht ebenso wie weitere, die Ihre Administration ergänzt hat.

1. Wählen Sie die **Karte** des gewünschten Berichts. Jede Karte nennt in einem Satz, was der Bericht zeigt, welche Grundgesamtheit er auswertet und ob er live rechnet oder aus Monats-Auswertungen liest.
2. Darunter erscheint eine **Vorschau** mit den Zahlen des gewählten Zeitraums. Die Adresse merkt sich die Auswahl (`?ansicht=datensaetze&bericht=…`), Lesezeichen und Zurück-Taste funktionieren.
3. Über die gemeinsame Exportleiste mit optionalem **Empfänger** und **Zweck** laden Sie **CSV**, **JSON**, **XML** oder **PDF** herunter. CSV eignet sich für Tabellenkalkulationen, JSON und XML für andere Systeme. Jede Datei wird neu erzeugt; beim Beispiel-Sachbericht liest nur das PDF hybrid aus Monats-Aggregaten, die anderen Formate live.

Ein Zeitraum-Klick verwirft die Berichtsauswahl nicht — Karte und Vorschau bleiben stehen, die Zahlen aktualisieren sich.

**Vollständigkeitshinweise:** Über der Vorschau steht, was der Bericht *nicht* zeigt — zum Beispiel Ereignisse, deren Dokumentationstyp keiner Berichtskategorie zugeordnet ist, oder Ereignisse ohne Personenbezug. Eine gelbe **Hinweis**-Markierung bedeutet: Es fehlen Daten, die Zahlen sind also zu niedrig. Eine graue **Info**-Markierung bedeutet: Die Daten sind enthalten, fehlen aber in einzelnen Teilauswertungen. Steht dort „Keine Vollständigkeitsprobleme erkannt", wurden bei den eingebauten Prüfungen keine Probleme erkannt; das garantiert keine vollständige Erfassung.

**Kleinstfallzahlen werden unterdrückt:** Vorschau und Downloads wenden den Schutz vor Kleinstfallzahlen an — Werte unterhalb der K-Anonymitäts-Schwelle Ihrer Einrichtung (Standard 5) werden entfernt, um Rückschlüsse auf einzelne Personen zu erschweren. Unterdrückung allein garantiert keine Anonymität. **Dargestellt** wird ein entfernter Wert je nach Format unterschiedlich: in der Vorschau als Gedankenstrich („—"), im CSV als leere Zelle (die Metadatenzeile am Dateianfang weist auf die Unterdrückung hin), im JSON als `null`, im XML als leeres Element. Gelegentlich fällt zusätzlich ein zweiter, größerer Wert weg — das ist beabsichtigt, sonst ließe sich der unterdrückte Wert zurückrechnen. Aus demselben Grund enthalten Vollständigkeitshinweise bei sehr kleinen Zahlen gar keine Ziffer mehr.

**Datenquelle: Live-Abfrage.** Unter der Vorschau steht, woher die Zahlen kommen. Vorschau und CSV/JSON/XML rechnen die Zahlen bei **jedem Aufruf neu** aus dem aktuellen Datenbestand; das gilt auch für die PDFs der anderen Berichte. Je nach Bericht sind das Ereignisse, Fälle oder Personen. Werte, die auf Ereignissen beruhen, erscheinen für Zeiträume, deren Ereignisse nach Ablauf der Löschfristen entfernt wurden, deshalb leer — anders als im Beispiel-Sachbericht-PDF, das aus den Monats-Auswertungen liest. Zahlen, die Fälle oder Personen selbst zählen (etwa „Beispiel: Fall-Kennzahlen“ oder die Neuaufnahmen in „Beispiel: Personen-Kennzahlen“), ändern sich durch das Löschen von Ereignissen nicht. In den Dateiformaten steht dieselbe Angabe maschinenlesbar: im CSV in der Metadatenzeile am Dateianfang, in JSON und XML als Feld `data_source` (Wert `live`).

> **Hinweis:** Jeder Download wird im Audit-Log protokolliert; die Vorschau nicht. Ein leerer Zeitraum sieht in der Vorschau aus wie eine Unterdrückung — prüfen Sie im Zweifel den gewählten Zeitraum.

### Freigaben (Leitung / Admin)

Der Reiter **Freigaben** zeigt keine Statistik, sondern die markierten Herausgaben Ihrer Einrichtung: Zeitpunkt, Artefakt, Format, Berichtszeitraum, Empfänger und Zweck. Die Anzeige umfasst **50 Einträge je Seite**, unabhängig vom gewählten Statistik-Zeitraum. Beim Reiterwechsel bleibt dieser Zeitraum erhalten.

**Als Herausgabe zählt** der erfolgreiche Download der internen Ereignisliste sowie eines Datensatz-Berichts als CSV, JSON, XML oder PDF mit Berichtszeitraum und Herausgabe-Markierung im Audit-Log. **Nicht erfasst** sind Berichtsvorschauen oder Weitergaben außerhalb der Anwendung, etwa weitergeleitete Dateien und Bildschirmfotos. Die Adresse `/statistics/external/` leitet auch mit `?format=json` zur Karte **Allgemeine Kennzahlen** weiter; dieser Aufruf ist keine Herausgabe und erzeugt keinen Export-Audit-Eintrag.

**Überschneidungswarnung:** Die Seite markiert nicht identische, überlappende Berichtszeiträume desselben Artefakts. Die registrierten Zähldimensionen bestimmen verwandte Berichte: Zur Ereignisfamilie gehören **Allgemeine Kennzahlen**, **Summe von Zahlenfeldern**, **Beispiel: Ereignisse je Dokumentationstyp**, **Beispiel-Sachbericht**, Monatsverlauf, Beratungsstunden, Spritzentausch-Bilanz, Hausverbots-Übersicht und Leistungsverteilung, einschließlich ihrer Kategorien und Teilpopulationen. **Personen-Kennzahlen** teilen zusätzlich die Personendimension mit den allgemeinen Kennzahlen; Fall-Kennzahlen gehören nicht zur Ereignisfamilie. Zwischen verwandten Berichten lösen auch identische Zeiträume eine Warnung aus. Die Beziehung bedeutet keine Gleichheit der Grundgesamtheiten oder Maßeinheiten und keinen vollständigen Schutz vor Rückrechnung über alle Berichte. Dateiformat und Empfänger ändern diesen Vergleich nicht; wiederholte Downloads desselben Berichts mit identischem Zeitraum bleiben ausgenommen. Ältere Einträge ohne Backend-Schlüssel werden nach Format, ersatzweise Ziel gruppiert. Geprüft wird gegen **alle noch protokollierten passenden Herausgaben** der Einrichtung, auch außerhalb der angezeigten Seite und jenseits von 500 Einträgen. Der Hinweis oben zählt nur betroffene Herausgaben auf der aktuellen Seite. `RELEASE_LOG_LIMIT = 500` ist der Standard eines Service-Aufrufs, weder die Seitengröße noch eine Grenze dieser Zeitraumprüfung.

Die Warnung verhindert keine Herausgabe. Überlappende Dateien können durch Differenzbildung unterdrückte Informationen offenlegen, auch ohne sichtbare Unterdrückung. Identische Zeiträume desselben Berichts sind vom Warnalgorithmus ausgenommen; bei verändertem Bestand oder Quellenstand können sie trotzdem unterschiedliche Inhalte liefern. Prüfen Sie Empfänger und mögliche Zusammenführung: Verschiedene Empfänger senken das Risiko nur unter der **organisatorischen Annahme**, dass sie die Dateien nicht zusammenführen; die Anwendung garantiert das nicht.

Tragen Sie als **Empfänger die Organisation**, nicht eine Ansprechperson ein. Empfänger und Zweck sind optional; je Feld sind höchstens 200 Zeichen erlaubt. Erkannte E-Mail-/Telefonmuster und typische Namenspaare werden vor dem Download abgewiesen. Die Prüfung ist heuristisch und erkennt nicht jede personenbezogene Angabe.

Die Liste enthält nur Einträge mit Herausgabe-Markierung; unmarkierte Exporte fehlen und werden nicht nachgetragen. Markierte Berichtsdateien sind von der normalen Audit-Löschfrist ausgenommen, Bildschirmabrufe nicht. Daraus folgt keine Zusage einer dauerhaft vollständigen Historie: Unmarkierte Einträge und Weitergaben außerhalb der Anwendung bleiben unsichtbar.

Weitere organisatorische Regeln stehen im [Admin-Handbuch § 8 „Herausgabe von Berichten“](admin-guide.md#herausgabe-von-berichten-was-sie-wissen-müssen), insbesondere zu kurzen Zeiträumen und dem Zusammenführen von Dateien aus unterschiedlichen Quellen.

### Löschanträge prüfen (Recht „Löschbestätigung“)

Wenn eine Fachkraft, Leitung oder Anwendungsbetreuung einen Kontakt einer Person der Kontaktstufe „Mit Zusatzangaben" löschen möchte, entsteht ein **Löschantrag**, der von einer zweiten Person mit dem Recht „Löschbestätigung“ genehmigt werden muss.

1. Offene Löschanträge sehen Sie unter `/deletion-requests/`.
2. Klicken Sie auf einen Antrag, um Begründung und verfügbare Angaben zum betroffenen Datensatz einzusehen. **Datensatz ansehen (neuer Tab)** öffnet das Ereignis, die Personenakte oder den Fall in einem neuen Tab; die Prüfseite bleibt im bisherigen Tab offen. Bei einer Zusammenführung gibt es je einen Link für die aufzulösende und die verbleibende Akte.
3. Klicken Sie auf **Genehmigen** oder **Ablehnen** und bestätigen Sie.

Die Prüfseite erweitert Ihre Leserechte nicht: Ereignisangaben und Links folgen der Sensibilität und Ihrer Rolle; Fallangaben und Falllinks sind erst ab Fachkraft sichtbar und setzen einen verfügbaren Fall einer aktiven, nicht gelöschten Person voraus. Nicht einsehbare, gelöschte oder nicht auffindbare Ziele erhalten einen neutralen Hinweis ohne Link; bei unsichtbaren Ereignissen fehlen auch Dokumentationstyp, Zeitpunkt und Pseudonym. Sie können den Antrag weiterhin anhand von Antragsteller:in und Begründung prüfen. Bei einem zurückgezogenen Antrag bleiben Links auf weiterhin einsehbare Ziele erhalten; der Rückzugshinweis bleibt sichtbar und eine Entscheidung ist nicht mehr möglich.

> **Wichtig:** Das Recht „Löschbestätigung“ wird unabhängig von der Rolle vergeben. Beim Anlegen eines Zugangs ist es für Leitung und Anwendungsbetreuung vorbelegt und abwählbar, für andere Rollen nicht; ohne das Recht kann auch eine Leitung nicht genehmigen. Eigene Anträge können Sie weder genehmigen noch ablehnen (4-Augen-Prinzip). Zur Vergabe siehe [Admin-Handbuch § 7.5](admin-guide.md#75-löschanträge-4-augen-prinzip).

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

„Letzter Kontakt“ in Personenliste und Offline-Bundle sowie die Reihenfolge der Personenauswahl berücksichtigen nur nicht gelöschte Kontakte, deren Dokumentationstyp für die eigene Rolle sichtbar ist. Ohne sichtbaren Kontakt bleibt der Wert leer. Auch Kontakte vor dem 90-Tage-Fenster der Bundle-Ereignisliste zählen für diesen Wert.

Personen im Papierkorb sowie inaktive oder bereits anonymisierte Personen können nicht erneut als Offline-Bundle abgerufen werden, auch nicht mit einer bedingten Anfrage. Ein offener Löschantrag allein sperrt den Abruf nicht; nach Wiederherstellung einer weiterhin aktiven Person ist er wieder möglich.

**Demo-Einstieg** (#2511): Nach „Demo starten“ wird nur im Demo-Modus (`DEMO_MODE`) der Offline-Schlüssel aus dem öffentlich bekannten Demo-Passwort und dem kontospezifischen Server-Salt abgeleitet; die absolute Höchstdauer gilt auch hier. Beim Start sowie beim Bereichs- und Rollenwechsel fordert der Server per `Clear-Site-Data` die Löschung des alten Browserspeichers an; die Zielseite leitet danach den neuen Schlüssel ab. Bei einem abweichenden Kontopasswort wird der Start vor dieser Löschung mit einem sichtbaren Fehler abgewiesen; Fehler beim Salt-Abruf oder bei der Ableitung erscheinen ebenfalls sichtbar. Wird die Vorbereitung durch einen Seitenwechsel unterbrochen, versucht die nächste angemeldete Seite sie erneut; ein Demo-Neustart ist dafür nicht nötig. Ein bereits vorhandener gültiger Schlüssel wird bestätigt, ohne ihn neu abzuleiten oder seine Laufzeit zu verlängern. Das Kontopasswort wird dabei nicht zurückgesetzt. Die Demo enthält synthetische Daten; das bekannte Passwort bietet keine Vertraulichkeit gegenüber anderen Demo-Besuchern.

**Offline-Schlüssel und Widerrufsprüfung** (#2528). Der aus Passwort und Server-Salt abgeleitete non-extractable `CryptoKey` bleibt in der Browser-IndexedDB gespeichert und übersteht Tab-, Browser- und Geräteneustart. „Non-extractable“ verhindert den Export über WebCrypto; der Schutz des gespeicherten Schlüssels gegen Zugriff auf das Browserprofil hängt an **Geräteverschlüsselung und Bildschirmsperre**, die für Offline-Geräte verpflichtend sind. Beim Ableiten wird eine absolute Frist eingefroren: `session_absolute_hours` der Einrichtung (Vorgabe 12 Stunden). Aktivität und Navigation verlängern sie nicht. Die Anwendung prüft beim Laden, vor Entschlüsselung/Speicherung/Replay, bei Tab-Rückkehr und im 60-Sekunden-Intervall. Am Ablauf entzieht sie den Schlüssel und zeigt „Offline-Schlüssel abgelaufen — bitte online anmelden“; ungesendete Arbeit bleibt verschlüsselt erhalten und wird nach erneutem Online-Login mit unverändertem Passwort und Salt wieder lesbar. Das Leerlauffenster gilt zusätzlich. Fehlende oder ungültige Ablaufdaten sperren den Zugriff; Bundles ohne gültiges `expiresAt` gelten als abgelaufen. Ein beobachteter Rücksprung der Geräteuhr sperrt den Schlüssel; eine manipulierbare Geräteuhr bleibt dennoch ein Restrisiko. Bei ausgeschalteter Anwendung erfolgt kein Schlüsselentzug per Fernlöschung.

Meldet die Widerrufsliste einen HTTP-/Netz-/Verarbeitungsfehler (auch 500/429) oder liefert sie eine ungültige Antwortstruktur, bleiben Queue- und Edit-Replay ausgesetzt: „Synchronisation wartet auf Widerrufsprüfung“. Der Sperrzustand wird in der lokalen IndexedDB gespeichert. Zusätzlich braucht jeder Replay-Lauf eine eigene frische, strukturell gültige, erfolgreich angewandte und persistierte Widerrufsprüfung; eine bereits erteilte Freigabe erlaubt keinen weiteren Lauf und keinen anderen Tab, auch wenn Metadatenschreiben und Ersatzlöschung gleichzeitig scheitern. Fehlende oder unlesbare Metadaten sperren den Replay; auch nach einem Lesefehler ist eine neue erfolgreiche Prüfung nötig. Der nächste Sync-Lauf prüft erneut, ebenso ein direktes Speichern aus der Offline-Akte. Bei fehlgeschlagener Prüfung bleibt ungesendete Arbeit verschlüsselt erhalten. Die bestehenden Idempotenz-Schlüssel bleiben erhalten, und die Server-Gates (422 für widerrufene Personen, 409 für Statuskonflikte) bleiben verbindlich. Geräteverschlüsselung ersetzt diese Server-Prüfungen nicht.

Für Einsätze ohne verlässliche Internetverbindung — etwa bei aufsuchender Arbeit — können Sie Anlaufstelle im Offline-Modus nutzen.

**Vor dem Einsatz (online):**

1. Öffnen Sie die Personenliste.
2. Tippen Sie bei jeder gewünschten Person in der Spalte **„Offline"** auf **„Mitnehmen"** — oder auf der Detailseite der Person auf **„Offline mitnehmen"**. Pseudonym, Stammdaten und die letzten Ereignisse werden dann verschlüsselt für die Offline-Nutzung auf Ihr Gerät geladen; ein Badge **„Lokal verfügbar"** bestätigt es. Maximal 20 Personen gleichzeitig.
3. Eine Übersicht über Ihre mitgenommenen Personen finden Sie jederzeit unter **„Personen"** (`/clients/`) — diese Ansicht funktioniert auch offline (siehe unten).

**Während des Einsatzes (offline):**

- **Personen, Aufgaben und Zeitstrom bleiben an ihrer gewohnten Adresse verfügbar:** Die **Personenliste** (`/clients/`), die **Aufgabenliste** (`/workitems/`) und der **Zeitstrom** als Startseite (`/`) rendern offline denselben lokalen Ausschnitt wie online — aus Ihren mitgenommenen Personen zusammengestellt, in gewohnter Optik. Die Aufgabenliste und der Zeitstrom zeigen dabei auch **anonyme bzw. personlose Einträge** (Standalone-Aufgaben, Kontakte ohne Personenbezug), klar als solche markiert; der Zeitstrom ist bewusst als „lokale Chronik" gekennzeichnet — er zeigt nur die offline verfügbaren Vorgänge, nicht die vollständige Historie der Einrichtung. Eine separate Offline-Startseite brauchen Sie dafür nicht mehr.
- Öffnen Sie eine mitgenommene Person wie gewohnt über ihre Detailseite — die Ansicht bleibt an der **normalen Adresse** und wird aus dem verschlüsselten Offline-Speicher aufgebaut (kein separater „Offline"-Link mehr).
- Sie können **neue Ereignisse erfassen** (über **„Kontakt dokumentieren"**) und **bestehende bearbeiten**; die Einträge werden verschlüsselt lokal gespeichert (AES-GCM-256; der Schlüssel wird aus Ihrem Passwort abgeleitet) und bei Verbindung automatisch gesendet. **Datei-Anhänge** können offline nicht erfasst werden — reichen Sie sie nach, sobald Sie online sind.
- **Erfassung überall offline:** Sie müssen dafür **kein Dossier mehr geöffnet** haben. Tippen Sie offline im **„+"-Menü** auf **„Kontakt dokumentieren"** bzw. **„Aufgabe erstellen"** (oder öffnen Sie die Formulare `/events/new/` / `/workitems/new/` direkt) — es öffnet sich **an der gewohnten Adresse** eine echte Erfassungsmaske: Dokumentationstyp wählen, optional eine „offline mitgenommene" Person zuordnen, speichern. Der Eintrag wird verschlüsselt lokal abgelegt und beim nächsten Netzkontakt automatisch gesendet.
- **Kontakte ohne Personenbezug** (anonyme Kontakte) und **Aufgaben ohne Person** (Standalone) lassen sich damit jetzt **auch offline neu beginnen** — wählen Sie im Personen-Auswahlfeld **„— ohne Person —"**. Erfordert der gewählte Dokumentationstyp eine Kontaktstufe, ist „ohne Person" deaktiviert; ordnen Sie dann eine mitgenommene Person zu (der Server prüft die Kontaktstufe beim Synchronisieren erneut).
- **Aufgaben werden vor dem lokalen Speichern geprüft:** Der Titel darf nach Entfernen äußerer Leerzeichen nicht leer sein und höchstens 200 Zeichen enthalten. Die Wiedervorlage darf nicht nach der Fälligkeit liegen. Feldfehler lassen die Eingaben im Formular; korrigieren Sie sie vor dem Speichern. Lokal gespeicherte Aufgaben warten weiterhin auf die Synchronisation und die Prüfung durch den Server.
- **Aufgaben-Erfassung ist Fachkräften vorbehalten.** Öffnen Sie als Assistenz offline die Aufgaben-Maske, sehen Sie statt der Felder einen Hinweis — so entsteht kein Eintrag, der beim Senden ohnehin abgelehnt würde.
- Ist für die Offline-Erfassung **noch nichts vorbereitet** (die Einrichtung wurde nie online geöffnet, der lokale Cache ist leer, oder die installierte App ist noch nicht aktualisiert), zeigt die Maske einen kurzen Hinweis **„einmal online öffnen"** statt der Formularfelder.
- **`/offline/` ist ein technischer Fallback**, kein Einstiegspunkt mehr: Die Seite „Keine lokale Ansicht verfügbar" erscheint ausschließlich, wenn für die aufgerufene Adresse **keine** lokale Ansicht vorliegt (z. B. eine unbekannte URL, oder die App wurde noch nicht auf den aktuellen Stand aktualisiert) — mit Links zur Personenliste, den Aufgaben und zum Zeitstrom sowie „Erneut versuchen".
- In der Oberfläche sehen Sie einen Hinweis, dass Sie offline arbeiten und wie viele Einträge noch auf die Synchronisation warten.

**Zurück im Netz:**

- Sobald das Gerät wieder online ist, wird die Warteschlange **automatisch synchronisiert**. Die offline erfassten Ereignisse landen auf dem Server und sind dort für das Team sichtbar.

**Konflikte auflösen:**

Wurde ein Ereignis gleichzeitig online (durch jemand anderen) und offline (durch Sie) bearbeitet, zeigt Anlaufstelle beim Synchronisieren einen **Side-by-Side-Diff** mit drei Auswahlmöglichkeiten:

- **Meine Version behalten** — Ihre offline erfasste Änderung gewinnt.
- **Server-Version übernehmen** — die Online-Änderung gewinnt, Ihre offline-Version wird verworfen.
- **Manuell mergen** — Sie entscheiden Feld für Feld, welche Inhalte übernommen werden.

„Meine Version behalten“ und „Manuell mergen“ prüfen vor der Übertragung die Widerrufsliste erneut im selben exklusiven Lauf. Scheitert diese Prüfung oder ihre lokale Speicherung, bleibt das Formular bedienbar und zeigt „Synchronisation wartet auf Widerrufsprüfung“; die Auswahl bleibt verschlüsselt gespeichert. Ein erneuter Klick prüft frisch und überträgt erst nach gültiger, erfolgreich gespeicherter Freigabe. Hat die neue Liste den lokalen Datensatz entfernt, wird er nicht übertragen und der Dialog bittet darum, die Konfliktübersicht neu zu laden.

**Konflikte und nicht übertragbare Einträge verwalten:**

Unter **`/offline/conflicts/`** sehen Sie alle Einträge, die eine Entscheidung erfordern — die Seite öffnet sich auch ohne Netzverbindung (#1396). Zwei Bereiche:

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
| `assistant` | Assistenz | Eingeschränkte Erfassung; Personen beider Kontaktstufen einsehen, Zusatzfelder nur bei wirksamer Sensibilität „Normal“; keine internen Personennotizen |
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
| Kontakte-Liste filtern und aufklappen (auch fremde Erfassungen) | Ja (Normal) | Ja (bis Erhöht) | Ja (auch Hoch) | Ja (auch Hoch) |
| Fallfilter und Fallanzeige in der Kontakte-Liste | Nein | Ja | Ja | Ja |
| Anonyme Kontakte dokumentieren | Ja | Ja | Ja | Ja |
| Kontakte zu Personen der Stufe „Nur Pseudonym" dokumentieren | Ja | Ja | Ja | Ja |
| Personen einsehen und suchen | Ja (beide Kontaktstufen) | Ja | Ja | Ja |
| Personen anlegen und bearbeiten | Nein | Ja | Ja | Ja |
| Zusatzfelder an Personen einsehen | Nur wirksam „Normal“ | Bis wirksam „Erhöht“ | Alle Stufen | Alle Stufen |
| Interne Personennotizen einsehen | Nein | Ja | Ja | Ja |
| Eigene Ereignisse bearbeiten | Ja | Ja | Ja | Ja |
| Fremde Ereignisse bearbeiten | Nein | Ja | Ja | Ja |
| WorkItems erstellen und bearbeiten | Nein | Ja | Ja | Ja |
| Suche verwenden | Ja | Ja | Ja | Ja |
| Statistik und Export | Nein | Nein | Ja | Ja |
| Personendaten exportieren (Art. 15 / 20 DSGVO) | Nein | Nein | Ja | Ja |
| DSGVO-Dokumentationspaket herunterladen | Nein | Nein | Nein | Ja |
| Löschanträge stellen | Nein | Ja | Ja | Ja |
| Löschanträge genehmigen | Nur mit Löschbestätigung | Nur mit Löschbestätigung | Nur mit Löschbestätigung | Nur mit Löschbestätigung |
| Pseudonyme ändern / Kontaktstufe hochsetzen | Nein | Ja | Ja | Ja |
| Kontaktstufe „Mit Zusatzangaben“ herabsetzen | Nein | Nein | Ja | Ja |
| Audit-Log einsehen (eigene Einrichtung) | Nein | Nein | Nein | Ja |
| Fallmanagement (Fälle, Episoden, Ziele) | Nein | Ja | Ja | Ja |
| Fälle schließen / wiedereröffnen | Nein | Nein | Ja | Ja |
| Benutzer und Einstellungen verwalten | Nein | Nein | Nein | Ja |

Die wirksame Sensibilität ist die höhere Stufe aus Dokumentationstyp und Feld. Die Feldverschlüsselung ist davon unabhängig.

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

### Konflikt beim Bearbeiten einer Episode

Hat sich eine Episode seit dem Öffnen ihres Bearbeitungsformulars geändert, wird das Speichern abgewiesen. Auch ein fehlender oder ungültiger Formularstand verhindert das Speichern. Ihre eingegebenen Texte und Datumswerte bleiben erhalten; der aktuelle Episodenstand steht darüber. Vergleichen Sie beide Stände und passen Sie Ihre Eingaben an, bevor Sie bewusst erneut **Speichern** wählen. Besonders bei einem inzwischen gesetzten Enddatum: Ein leeres Ende öffnet die Episode bei diesem erneuten Absenden wieder. Eine weitere zwischenzeitliche Änderung wird erneut abgewiesen (#2586).

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
   - **Direkt in den Papierkorb** — bei Personen der Kontaktstufe „Nur Pseudonym".
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

**Episode abschließen:** Klicken Sie auf **Abschließen**. Das Enddatum wird automatisch auf das heutige Datum gesetzt. Liegt der Beginn in der Zukunft, wird der Abschluss mit einer Meldung abgewiesen; bearbeiten Sie die Episode, um den Beginn zu korrigieren oder ein passendes Ende einzutragen.

Das Ende darf nicht vor dem Beginn liegen; derselbe Tag ist erlaubt. Eine entsprechende Fehlermeldung am Enddatum erhält Ihre Eingaben beim Erstellen und Bearbeiten.

Jede Episode zeigt ihren Status:
- **aktiv** (grün) – noch kein Enddatum
- **abgeschlossen** (grau) – Enddatum gesetzt

### Wirkungsziele und Meilensteine

**Wirkungsziele** dokumentieren, was die Arbeit am Fall erreichen soll – z. B. „Stabile Wohnsituation" oder „Anbindung an Suchtberatung". Jedes Ziel kann in konkrete **Meilensteine** untergliedert werden.

Titel von Wirkungszielen und Meilensteinen dürfen höchstens **200 Zeichen** enthalten. Das gilt auch beim Bearbeiten eines Zieltitels.

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

## Aufbewahrung und Sperren

Leitung und Anwendungsbetreuung öffnen in Personen-, Fall- oder Ereignisakte
**Legal Hold verwalten**, tragen einen Grund und bei Bedarf ein Ablaufdatum
ein und setzen die Sperre. Dort lässt sie sich auch aufheben; beide Aktionen
werden protokolliert. Ein Personen-/Fall-Hold schützt auch zugehörige
Ereignisse und die Audit-Zeilen zu geschützten Zielen. Die Systemadministration
kann Holds in `/system/legal-holds/` aufheben.

Der Nachtlauf schlägt abgelaufene Ereignisse „Mit Zusatzangaben“ vor; erst zwei verschiedene
berechtigte Personen mit **Löschbestätigung** geben sie manuell frei. Zurückstellen schützt bis
zum Datum; Ablehnen löscht nichts, frühestens nach der Wiedervorlagefrist
(Default 365 Tage) entsteht eine neue Vorlage. Auto-Freigabe bleibt Opt-in.

Den Vorschlag legt der Lauf an. Die manuelle Freigabe braucht zwei verschiedene aktive Personen der eigenen Einrichtung, jeweils mit Rolle Leitung oder Anwendungsbetreuung und dem Recht „Löschbestätigung“. Die erste Freigabe lässt den Vorschlag ausstehend (PENDING, „1 von 2 Freigaben“); erst die zweite andere Person gibt ihn endgültig frei (APPROVED). Mit nur einem berechtigten Konto bleibt es bei „1 von 2“; die erste Freigabe allein erlaubt keine Löschung. Zurückstellen, Legal Hold (auch aus der Personen-, Fall- oder Ereignisakte) und Ablehnung verwerfen die erste Freigabe mit Audit; bei einem erneuten manuellen Freigabeweg sind wieder zwei Freigaben nötig. Die Einrichtung benennt mindestens zwei berechtigte Konten. Die Compliance-Karte „Genehmiger für den Aufbewahrungslauf“ prüft Rolle und Anzahl (0 = kritisch, 1 = Warnung, mindestens 2 = ok). Die Karte „Genehmiger für Löschanträge“ zählt weiterhin aktive Löschantragsberechtigte unabhängig von ihrer Rolle. Ausnahme: Die Einrichtung kann die Auto-Freigabe nach mehrfacher Zurückstellung ausdrücklich einschalten (Opt-in, standardmäßig aus); nach der konfigurierten maximalen Zahl von Zurückstellungen und Ablauf der Wiedervorlage genehmigt der Lauf ohne zweite Person. Hold und Art. 18 schützen auch diesen Weg.
Ohne Fall beginnt die Stufenfrist beim Ereignisdatum, im offenen Fall bei
letzter fachlicher Fallaktivität (Fallback Anlage), geschlossen beim Abschluss.
Eine Typ-**Höchstfrist (verkürzt nur)** ersetzt den Freigabeschritt nicht.

Auch die Herabstufung „Mit Zusatzangaben“ → „Nur Pseudonym“ benötigt bei
vorhandenen Ereignissen einen Antrag und eine andere Person mit diesem Recht.
Ohne Ereignis gilt die Inaktivitätsfrist `retention_identified_days` nur für „Nur Pseudonym“ ohne offene Fälle. Der Anker ist der jüngste Zeitpunkt aus Personenänderung (`updated_at`), fachlicher Fallaktivität (`last_activity_at`), Aufgabenänderung und Anhangsänderung an Person oder Fall (Anlage, Ersetzung oder Entfernung). Personen „Mit Zusatzangaben“ ohne Ereignis sind von der automatischen Anonymisierung ausgenommen; der Vorschlagsweg unterstützt bisher nur Ereignisse, für diese Personen fehlt eine automatische Fristvollstreckung. Personen „Nur Pseudonym“ ohne Ereignis mit offenem Fall sind vom Inaktivitätszweig ausgenommen. Die Einrichtung prüft ereignislose Personen „Mit Zusatzangaben“ regelmäßig und stellt bei Bedarf einen Löschantrag; das Prüfintervall hält sie im Löschkonzept fest. Maßgeblich ist [`anonymization.py`](../src/core/retention/anonymization.py). Hold und Art. 18 stellen Löschung, Anonymisierung und
Historien-Redaktion zurück. Nach Anonymisierung zeigt der
Papierkorb noch offene Historienfassungen an; regulär redigiert sie der Nachtlauf.
Abgeschlossene Lösch-, Zusammenführungs- und Herabstufungsanträge bleiben
1095 Tage (konfigurierbar) erhalten. Die Monatszählwerte bleiben auch nach
manueller Ereignislöschung unbefristet erhalten.

*Anlaufstelle – Dokumentationssystem für niedrigschwellige soziale Hilfsangebote*

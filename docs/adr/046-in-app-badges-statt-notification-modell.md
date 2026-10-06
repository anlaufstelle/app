# ADR-046: In-App-Badges statt Notification-Modell

- **Status:** Accepted
- **Date:** 2026-08-24
- **Deciders:** Tobias Nix
- **Refs:** #1870 (RFC), #1941 E1 (M4-Zuschnitt), #1984 (Umsetzung in `release-0.85`)

## Context

Vier Arbeitsabläufe setzen derzeit voraus, dass die richtige Person die richtige Liste öffnet:

1. Vier-Augen-Löschanträge,
2. Berichtigungsanträge nach Art. 16 DSGVO,
3. zugewiesene Aufgaben,
4. fällige Aufgaben und Wiedervorlagen (`due_date`/`remind_at`).

Es gibt kein Notification-Modell, keinen In-App-Posteingang und keinen E-Mail- oder Push-Versand für diese Vorgänge. Zwei kleinere Pull-Signale bestehen bereits:

- Die Leitungs-Arbeitszentrale zeigt die Zahl offener Löschanträge ([`dashboard/main.py`](../../src/core/services/dashboard/main.py)).
- Der Navigationspunkt **Aufgaben** zeigt offene und überfällige eigene Aufgaben ([`nav.py`](../../src/core/nav.py), [`_nav_sidebar_items.html`](../../src/templates/partials/_nav_sidebar_items.html)).

Ein echtes Notification-System löst eine andere Problemklasse als ein Zähler. Es braucht persistente Empfänger- und Zustellzustände, Gelesen/Ungelesen, Deduplizierung, Präferenzen, eventuell Digest/Retry und eine Aufbewahrungsregel. Bei E-Mail oder Web Push kommen Datenabfluss, Zustellbarkeit, Opt-in und Background-Worker hinzu. Das wäre für den aktuellen Bedarf unverhältnismäßig: Gefordert ist zuerst, bereits vorhandene offene Arbeitsmengen an ihren bestehenden Einstiegen sichtbar zu machen.

## Decision

### 1. Der nächste Schritt sind serverseitige In-App-Zähler

Anlaufstelle ergänzt die vorhandene Navigation um **Badges mit aktuellen Anzahlen**. Die Werte werden beim normalen authentifizierten Request aus den fachlichen Tabellen berechnet; es entsteht weder ein neues Modell noch ein persistenter Notification-Datensatz.

Der Umsetzungsumfang liegt in #1984 (`release-0.85`). `release-0.84` liefert nur diese Entscheidung. Dadurch gibt es in 0.84 keinen dritten Service-Worker-Bump und keine Kollision mit dem Umbau des Verwaltungs-Hubs.

### 2. Ein Badge zeigt offenen Bestand, nicht „ungelesen"

Ohne persistierten Nutzerzustand ist „neu" oder „gelesen" nicht belastbar feststellbar. Die Zähler bedeuten deshalb ausschließlich:

| Einstieg | Zählmenge |
|---|---|
| Löschanträge | PENDING-Anträge, die die angemeldete Person in der Zielansicht sehen darf: für Leitung/Genehmiger alle der Einrichtung, für reine Antragsteller nur eigene offene Anträge |
| Berichtigungen | PENDING-Berichtigungsanträge der Einrichtung für Leitung/Anwendungsbetreuung |
| Aufgaben | bestehende Semantik: eigene offene Aufgaben; Überfälligkeit bleibt hervorgehoben |

`remind_at`/`due_date` werden damit **nicht aktiv zugestellt**. Sie beeinflussen weiterhin Inbox-Sortierung/-Filter und die bestehende Überfälligkeitsanzeige; sichtbar wird eine Fälligkeit erst beim nächsten Online-Request. Eine Aufgabe, die seit gestern offen ist, behält denselben Badge-Anteil — sie wird nicht durch Anschauen „verbraucht".

### 3. Berechtigungs- und Datenschutzgrenze = Zielansicht

Ein Badge verwendet dieselbe Sichtmenge und dieselben Gates wie die verlinkte Liste. Es darf insbesondere nicht die Existenz fremder Anträge verraten. Facility-Scoping und RLS bleiben zusätzliche Grenzen; eine installationsweite oder einrichtungsübergreifende Zählung ist unzulässig.

Der sichtbare Inhalt ist nur eine Anzahl. Pseudonyme, Aufgaben-/Feldtitel, Gründe und Freitexte gehören weder in Markup-Attribute noch in clientseitige Payloads. Der Zähler selbst wird nicht auditiert: Er ist eine abgeleitete Listenmetrik, kein Zugriff auf einen einzelnen Fachinhalt.

### 4. Kein Zustellkanal und kein Background-Lauf

In diesem Modell gibt es bewusst:

- keine E-Mail,
- keinen Web-Push und keine Service-Worker-Nachricht,
- keinen Tages-Digest,
- keinen Cron-/Queue-Job,
- keinen Zustellnachweis, Retry oder Eskalationslauf,
- keine persönlichen Notification-Präferenzen,
- kein Gelesen/Ungelesen, Dismiss oder Snooze.

Damit ist ein Badge ein verbesserter Pull-Hinweis, **keine Zustellgarantie**. Wer die Anwendung nicht öffnet, wird nicht erreicht.

### 5. Darstellung und Abfragevertrag

- Badges sind ergänzend zum beschrifteten Navigationsziel und niemals der einzige Hinweis.
- Ein zugänglicher Name beschreibt Bedeutung und Anzahl; Farbe allein trägt keine Information.
- Desktop und Mobil verwenden dieselbe Semantik. Die bestehenden 44-Pixel-Touchziele dürfen durch den Zähler nicht schrumpfen.
- `0` rendert keinen Badge; große Werte dürfen als `99+` verdichtet werden, ohne den vollständigen zugänglichen Text zu verlieren.
- Desktop- und Mobilnavigation teilen die pro Request berechneten Werte. Ein doppeltes Rendern darf keine doppelten Queries erzeugen; Query-Budgets und Rollen-/IDOR-Proben gehören zur Abnahme.

## Wiedereröffnungs-Bedingungen

Die Entscheidung „kein Notification-Modell" wird neu bewertet, sobald mindestens eine der folgenden Anforderungen fachlich verbindlich wird:

1. Benachrichtigung ohne offenen Anlaufstelle-Tab (E-Mail, Push oder Offline/PWA),
2. zeitgesteuerte Zustellung oder Digest unabhängig von einem Web-Request,
3. persönlicher Gelesen/Ungelesen-, Dismiss-, Snooze- oder Präferenzzustand,
4. nachvollziehbare Zustellung, Retry, Eskalation oder Vertretungsregeln,
5. historische Benachrichtigungsliste,
6. zu hohe Request-Kosten der Live-Zählung trotz Query-Konsolidierung.

Dann wird ein eigenes, minimiertes Notification-/Delivery-Modell entworfen. Zustellzustand darf nicht schrittweise als versteckte Zusatzspalte an Löschantrag, Berichtigung und Aufgabe verteilt werden — das ergäbe mehrere widersprüchliche Wahrheiten.

## Consequences

### Positiv

- Bestehende, autorisierte Querysets bleiben Single Source of Truth; kein Synchronisationsproblem zwischen Fachvorgang und Notification-Zeile.
- Keine neue PII-Kopie, Aufbewahrungsfrist, Queue oder Zustell-Infrastruktur.
- Lösch- und Berichtigungsworkflows werden an ihren vorhandenen Einstiegen sichtbar, ohne Fachdetails in Navigation oder E-Mail zu tragen.
- Kleine, unabhängig testbare Umsetzung in 0.85; 0.84 bleibt frei von einem zusätzlichen Service-Worker-Cachewechsel.

### Negativ / bewusst akzeptiert

- Es bleibt ein Pull-System: abgemeldete oder längere Zeit inaktive Nutzer erhalten keinen Hinweis.
- „Neu zugewiesen" ist nicht von „seit Tagen offen" unterscheidbar; ein Zähler kann nicht belegen, dass jemand einen Vorgang gesehen hat.
- Fällige Wiedervorlagen lösen keinen zeitgenauen Alarm aus.
- Live-Zähler erhöhen das Query-Budget jeder Seite mit Navigation und müssen gecacht bzw. zusammengeführt werden.
- Ein voller Zähler kann Aufmerksamkeit erzeugen, ersetzt aber keine organisatorische Vertretungs- oder Reaktionsregel.

## Alternatives considered

- **Notification-Modell sofort:** sauber für Gelesen-/Zustellzustand, aber ohne bestätigten Bedarf an diesen Eigenschaften unnötige Persistenz-, Retention- und Synchronisationsfläche — verworfen.
- **E-Mail als einfachster Kanal:** erreicht abgemeldete Nutzer, schafft aber einen externen Zustell- und Datenschutzkanal, benötigt Präferenzen/Deduplizierung und darf keine Fachdetails enthalten — verworfen.
- **Web Push/PWA:** braucht Permission-/Subscription-Lifecycle, Background-Zustellung und einen Service-Worker-Umbau; für reine Bestandszähler unverhältnismäßig — verworfen.
- **Nur Dashboard-Karten:** bereits teilweise vorhanden, aber rollenabhängige Einstiegsseiten werden nicht von allen täglich besucht; Navigation ist die stabilere, app-weite Fläche — verworfen.
- **Keine Änderung:** lässt die bekannte Auffindbarkeitslücke bestehen, obwohl die offenen Mengen bereits günstig und autorisiert abfragbar sind — verworfen.

## References

- RFC und Scoping: #1870, #1941 E1
- Umsetzung: #1984
- Bestehende Quellen: [`core/nav.py`](../../src/core/nav.py), [`services/dashboard/main.py`](../../src/core/services/dashboard/main.py), [`services/hubs.py`](../../src/core/services/hubs.py), [`models/user.py`](../../src/core/models/user.py)

## Nachtrag 2026-10-06: Umgesetzte Navigationszähler (Refs #2450)

Die In-App-Zähler sind umgesetzt. `src/core/context_processors.py`, `review_counts`, ermittelt die offenen Lösch- und Berichtigungsanträge und `datenschutz_review_count` als Summe am Datenschutz-Hub. Berichtigungen zu widerrufenen oder inaktiven Personen gehören nicht zur prüfbaren Menge und werden nicht gezählt. Das Badge bleibt eine aktuelle Anzahl, kein persistenter Gelesen-/Zustellzustand. Beleg: `src/templates/partials/_nav_sidebar_items.html`, Badge-Ausgabe, und `src/core/context_processors.py`, `review_counts`.

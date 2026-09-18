# Sprachleitlinie

> **Zweck:** Verbindliche Sprachregel für UI-Texte, Handbuch und Fachkonzept.
> **Herkunft:** [Issue #604](https://github.com/anlaufstelle/app/issues/604) (RFC adoptiert).
> **Geltung:** UI-Texte, Handbuch, Fachkonzept; **nicht** Modell-/API-Code (`Client` bleibt intern).

## Kurzfazit

**„Klientel" wird als Leitbegriff in UI und Handbuch abgelöst** — Standard ist **„Person"** (bei Bedarf _„Person mit Pseudonym"_). „Klientel" ist fachlich umstritten und sprachlich schief (der Duden definiert es als _Gesamtheit_ der Klient*innen — gemeint ist meist die einzelne Person). Der Code-/Modellbegriff `Client` bleibt; er ist nur intern sichtbar.

## Leitprinzipien

1. **Personen vor Kategorien** — lieber _Person_ / _Menschen_ / _Person mit Pseudonym_ als Sammel- oder Etikettbegriffe.
2. **Lebensweltlich statt verwaltungssprachlich** — lieber konkret beschreiben, was gemeint ist.
3. **Datenstufe ≠ Personenmerkmal** — _qualifiziert_ / _identifiziert_ beschreiben Dokumentations- bzw. Schutzstufen, nicht die Person.
4. **Fachsprache nur dort, wo sie hilft** — Admin, Fachkonzept und Datenschutz dürfen präziser / technischer sein als die Alltags-UI.
5. **Ebenenkonsistenz** — UI, Handbuch, Fachkonzept und Datenmodell dürfen unterschiedlich präzise sein, jede Ebene muss aber _intern_ konsistent bleiben.

## Terminologie-Matrix

### Behalten

| Begriff | Scope / Bedingung |
|---|---|
| Pseudonym | Primärer Identifikator, nicht ersetzen |
| Fachkraft, Leitung, Assistenz | Rollenbegriffe, unverändert |
| Anwendungsbetreuung | UI-Label für Rolle `facility_admin` (Refs #867). Vollzugriff in der eigenen Einrichtung. Ablöser des bisherigen UI-Labels „Administrator". |
| Systemadministration | UI-Label für Rolle `super_admin` (Refs #867). Facility-übergreifende Hosting-/Bootstrap-Rolle, sichtbar im `/system/`-Bereich. Begriff bewusst gewählt, um die Hosting-Ebene von der inhaltlichen Anwendungsbetreuung zu trennen. |
| Einrichtung | Facility-Begriff |
| Kontakt | Für einzelne Begegnung / Event-Kontext |
| Ereignis | Wenn technisch bei _Event_ geblieben wird |
| Löschantrag | 4-Augen-Prinzip, fachlich etabliert |
| niedrigschwellig | Nur in Fachkonzept / Handbuch-Einleitung / Träger-Doku |
| Streetwork | Nur wenn _tatsächlich_ Straßensozialarbeit gemeint |
| wohnungslos | Als Oberbegriff |
| obdachlos | Nur bei Leben ohne Unterkunft (Straßenobdachlosigkeit) |

### Ersetzen (UI + Handbuch)

| Bisher | Besser |
|---|---|
| Klientel | Person / Personen / Pseudonym / Person mit Pseudonym |
| Neues Klientel | Neue Person _(alternativ: Neues Pseudonym)_ |
| Klientel bearbeiten | Person bearbeiten |
| Klientel wurde erstellt | Person wurde angelegt |
| Klientel wurde aktualisiert | Person wurde aktualisiert |
| Kein Klientel gefunden | Keine Personen gefunden |
| Letzte Klientel | Zuletzt besuchte Personen |
| Noch keine besuchten Klientel | Noch keine zuletzt besuchten Personen |
| qualifiziertes Klientel | Person mit qualifizierter Dokumentation _(bzw. Person in qualifizierter Kontaktstufe)_ |
| identifiziertes Klientel | Person mit Pseudonym _(bzw. Person in identifizierter Kontaktstufe)_ |
| qualifizierte Klienteldaten | qualifizierte Daten |
| Klientelprofil | Personenprofil _(oder nur: Profil)_ |
| Klientel-Chronik | Chronik der Person _(oder: Verlauf)_ |

### Nur intern verwenden

| Begriff | Wo erlaubt |
|---|---|
| `Client` | Modell-/API-/Codebegriff — **kein Zwang**, das Domain-Modell sofort umzubenennen |
| Adressat\*innen | Fachlich korrekt, aber nicht als Produktwort in der UI |
| Nutzer\*innen | Eher für Forschung / Fachdebatte, nicht für begleitete Personen |
| Fall | **Entschieden (Refs #1805): _Fall_ bleibt UI-Begriff.** Intern, im Fachkontext und in der Oberfläche einheitlich — die Vokabelregel des Hauptkonzepts sagt _Fall anlegen_ |
| identifiziert / qualifiziert | **Nur noch Code-/API-Werte** (`ContactStage.IDENTIFIED` / `.QUALIFIED`, CSV, Export-JSON). Sichtbares UI-Label ab Refs #1805: _Nur Pseudonym_ / _Mit Zusatzangaben_ |

## Standards pro Ebene

| Ebene | Standardbegriff | Hinweise |
|---|---|---|
| **UI** | Person / Personen | Buttons: _Neue Person_, _Person bearbeiten_. Bei Pseudonym-Fokus: _Person mit Pseudonym_. |
| **Benutzerhandbuch** | Person | Modell erklären als „Personen werden mit Pseudonymen geführt". Kontaktstufen **beschreiben**, nicht als Identität der Person formulieren. |
| **Fachkonzept** | Person _(Klient\*in / Klientel punktuell zulässig)_ | Empfehlung: mittelfristig auch hier auf _Personen_ / _Menschen_ / _Personen mit Pseudonym_ umstellen. |
| **Code / Datenmodell** | `Client` | Kein sofortiger Rename nötig; nicht nach außen sichtbar. |

## Konkrete Sofortkorrekturen

> **Stand 2026-07-13:** Der _Klientel → Person_-Sweep in UI-Templates, View-Texten und Handbuch ist umgesetzt — `grep -r "Klientel"` über die unten gelisteten Templates, [`src/core/views/clients.py`](../src/core/views/clients.py) und [`docs/user-guide.md`](user-guide.md) liefert 0 Treffer; auch _obdachlos_ ist in [`seed.py`](../src/core/management/commands/seed.py) nicht mehr enthalten. Rest: In [`django.po`](../src/locale/de/LC_MESSAGES/django.po) stehen noch vereinzelte `msgstr "Klientel …"`-Strings zu inzwischen obsoleten bzw. nur noch in alten Migrationen eingefrorenen `msgid`s; diese werden beim nächsten `makemessages`-Lauf neu erzeugt.

Die folgenden Stellen wurden für den Refactor-Sweep per `grep` identifiziert:

**UI-Templates:**

- [`src/templates/core/clients/form.html`](../src/templates/core/clients/form.html) — _Klientel bearbeiten_ / _Neues Klientel_ → _Person bearbeiten_ / _Neue Person_
- [`src/templates/core/clients/list.html`](../src/templates/core/clients/list.html) — _Klientel_ → _Personen_
- [`src/templates/core/clients/partials/table.html`](../src/templates/core/clients/partials/table.html) — _Keine Klientel gefunden_ → _Keine Personen gefunden_
- [`src/templates/core/clients/detail.html`](../src/templates/core/clients/detail.html) — Offline-Snackbar
- [`src/templates/core/clients/offline_detail.html`](../src/templates/core/clients/offline_detail.html) — _Dieses Klientel wurde nicht …_ → _Diese Person wurde nicht …_
- [`src/templates/core/events/create.html`](../src/templates/core/events/create.html) — _Klientel_-Autocomplete-Label, aria-label, Anonym-Hinweis → _Person_ bzw. _Person suchen_
- [`src/templates/core/events/edit.html`](../src/templates/core/events/edit.html), [`detail.html`](../src/templates/core/events/detail.html), [`deletion_review.html`](../src/templates/core/events/deletion_review.html), [`delete_confirm.html`](../src/templates/core/events/delete_confirm.html) — Detail-`<dt>`-Label _Klientel_ → _Person_; _qualifizierten Klientel_ → _Person mit qualifizierter Dokumentation_

**View-Texte / Messages:**

- [`src/core/views/clients.py`](../src/core/views/clients.py) — Erfolgsmeldungen _Klientel wurde erstellt/aktualisiert_ → _Person wurde angelegt/aktualisiert_

**Handbuch:**

- [`docs/user-guide.md`](user-guide.md) Kapitel „Klientel verwalten" → „Personen verwalten"; Fließtexte konsistent auf _Person_ umstellen.

**Seed / Übersetzung:**

- [`src/core/management/commands/seed.py`](../src/core/management/commands/seed.py) — _obdachlos_ nur dort verwenden, wo wirklich Straßenobdachlosigkeit gemeint ist; sonst _wohnungslos_ / _von Wohnungslosigkeit betroffen_.
- [`src/locale/de/LC_MESSAGES/django.po`](../src/locale/de/LC_MESSAGES/django.po) — bestehende Inkonsistenz (`msgid "Klientel"` → `msgstr "Klienten"`) entschärfen. Nach UI-Umbenennung werden die msgids ohnehin neu erzeugt; auf Kohärenz von _msgid_ und _msgstr_ auf _Person_ / _Personen_ achten.

## Zeitwörter: kalendarisch vs. rollierend

> **Quelle:** [Issue #1804](https://github.com/anlaufstelle/app/issues/1804) §3.6 (Maintainer-geprüft 2026-08-11). **Geltung:** app-weit — UI-Etiketten, Query-Werte, Handbuch, Übersetzung.

Der Anlass war ein arithmetischer, kein kosmetischer Widerspruch: Der Fälligkeitsfilter „Diese Woche" zeigte ein rollierendes Acht-Tage-Fenster, während im selben Screen die Fälligkeits-Badges darunter ISO-kalendarisch rechneten — eine Aufgabe mit dem Badge „Nächste Woche (Mo)" lag im Filter „Diese Woche". In der Statistik hieß ein 31-Tage-Fenster „Monat".

**Z1 — Kalenderwörter sind reserviert.** „Woche", „Monat", „Quartal", „Halbjahr", „Jahr", „Diese/Nächste Woche", „Dieser/Nächster Monat" bezeichnen **ausschließlich** Kalendereinheiten. Woche = ISO-Woche (Mo–So), passend zu [`src/core/utils/dates.py`](../src/core/utils/dates.py).

**Z2 — Rollierende Fenster heißen nach ihrer Länge.** Muster: _Letzte N Tage_ (rückwärts), _Nächste N Tage_ (vorwärts). „Heute" ist der zulässige Sonderfall N = 1. Nie ein Kalenderwort für ein rollierendes Fenster.

**Z3 — Endpunkte werden benannt, nicht geraten.** Wo eine Zahl von einem Fenster abhängt, steht das aufgelöste Intervall sichtbar daneben.

**Z4 — Beide Enden inklusiv, Länge = Name.** _Letzte 30 Tage_ = `heute−29 … heute` (30 Tage), _Nächste 7 Tage_ = `heute … heute+6`. Ein Fenster, dessen Name eine andere Länge behauptet als seine Grenzen, ist ein Fehler.

**Z5 — Die Konvention gilt auch für EN.** _Last 30 days_, _Next 7 days_, _Calendar year_ — keine Umdeutung in der Übersetzung.

| Statt | Richtig | Wo |
|---|---|---|
| „Monat" / „Quartal" / „Halbjahr" (rollierend) | „Letzte 30 / 90 / 180 Tage" | Statistik-Zeitraumleiste (`?zeitraum=letzte30\|letzte90\|letzte180`) |
| „Jahr" | „Kalenderjahr" | Statistik (`?zeitraum=jahr`, mit Stepper) |
|| „Eigener Zeitraum" | Statistik (`?zeitraum=frei`) |
| „Diese Woche" (rollierend) | „Nächste 7 Tage" | Fälligkeitsfilter der Aufgaben (`?due=next7`) |
|| „Diese Woche (Do)" bleibt | Fälligkeits-Badges — sie sind ISO-kalendarisch und damit **korrekt** |

Umbenannte Query-Werte bleiben dauerhaft als stille Aliasse akzeptiert (Bookmarks, `sessionStorage` der Filter-Persistenz); angeboten wird nur die kanonische Schreibweise.

## Diskutable Begriffe

- **niedrigschwellig** — behalten (Caritas, BAG W verwenden den Begriff weiterhin). In der UI nicht alleine stehen lassen, lieber konkretisieren: _ohne Termin_, _anonym möglich_, _einfacher Zugang_, _aufsuchend_.
- **Streetwork** — behalten, wenn tatsächlich Straßensozialarbeit gemeint ist. Andernfalls: _aufsuchende Arbeit_ / _aufsuchende Sozialarbeit_.
- **Fall** — **entschieden (2026-08, Refs #1805): _Fall_ bleibt UI-Begriff.** Im Case-Management fachlich korrekt; die Vokabelregel des Hauptkonzepts (_Fall anlegen_) entscheidet damit auch für die Oberfläche. _Begleitung_ / _Unterstützungsprozess_ / _Verlauf_ bleiben als Fließtext-Varianten zulässig, nicht als Produktwort.
- **obdachlos vs. wohnungslos** — _wohnungslos_ ist der Oberbegriff; _obdachlos_ nur bei tatsächlichem Leben ohne Unterkunft.

## Priorisierung (Umsetzungsreihenfolge)

1. **obdachlos / wohnungslos** fachlich schärfen (Seed, Beispieltexte, Handbuch).

## Quellen

- Duden „Klientel": <https://www.duden.de/rechtschreibung/Klientel>
- DGSA Fachgruppe „Adressat*innen, Nutzer*innen und (Nicht-)Nutzung Sozialer Arbeit": <https://www.dgsa.de/fachgruppen/adressatinnen-nutzerinnen-und-nichtnutzung-sozialer-arbeit>
- socialnet Lexikon „Klientin, Klient": <https://www.socialnet.de/lexikon/Klientin-Klient>
- BAG W Übersicht: <https://www.bagw.de/de/bag-w/uebersicht>
- BAG W Wohnungsnotfalldefinition 2025: <https://www.bagw.de/de/themen/zahl-der-wohnungslosen/wohnungsnotfalldefinition>
- BAG W Gesundheit / diskriminierungssensible Versorgung: <https://www.bagw.de/de/publikationen/pos-pap/pos-gesundheit>
- Caritas „Harm Reduction — niedrigschwellige Hilfen": <https://www.caritas.de/glossare/harm-reduction--niedrigschwellige-hilfen>
- bpb zu „barrierearm": <https://www.bpb.de/pift2025/563370/sprache-raeume-gerechtigkeit-zugaenge-und-barrieren/>

## Folge-Refactor

Konkrete UI-/Handbuch-Renames sind kein Bestandteil dieser Leitlinie, sondern eines separaten Refactor-Sweeps. Das Domain-Modell `Client` (Python/API) bleibt **außerhalb** dieser Leitlinie und wird nicht umbenannt.

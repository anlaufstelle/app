# Sprachleitlinie — Klientel → Person

> **Quelle:** Issue #604 (RFC adoptiert).
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
| Fall | Intern und im Fachkontext okay; in UI prüfen, ob _Begleitung_ / _Unterstützungsprozess_ passender ist |
| identifiziert / qualifiziert | Als technische Stufen okay; sichtbare UI-Texte möglichst erläuternd formulieren |

## Standards pro Ebene

| Ebene | Standardbegriff | Hinweise |
|---|---|---|
| **UI** | Person / Personen | Buttons: _Neue Person_, _Person bearbeiten_. Bei Pseudonym-Fokus: _Person mit Pseudonym_. |
| **Benutzerhandbuch** | Person | Modell erklären als „Personen werden mit Pseudonymen geführt". Kontaktstufen **beschreiben**, nicht als Identität der Person formulieren. |
| **Fachkonzept** | Person _(Klient\*in / Klientel punktuell zulässig)_ | Empfehlung: mittelfristig auch hier auf _Personen_ / _Menschen_ / _Personen mit Pseudonym_ umstellen. |
| **Code / Datenmodell** | `Client` | Kein sofortiger Rename nötig; nicht nach außen sichtbar. |

## Konkrete Sofortkorrekturen

Die folgenden Stellen sind per `grep` verifiziert und kommen in einem eigenen Refactor-Sweep dran:

**UI-Templates:**

- [`src/templates/core/clients/form.html`](./src/templates/core/clients/form.html) — _Klientel bearbeiten_ / _Neues Klientel_ → _Person bearbeiten_ / _Neue Person_
- [`src/templates/core/clients/list.html`](./src/templates/core/clients/list.html) — _Klientel_ → _Personen_
- [`src/templates/core/clients/partials/table.html`](./src/templates/core/clients/partials/table.html) — _Keine Klientel gefunden_ → _Keine Personen gefunden_
- [`src/templates/core/clients/detail.html`](./src/templates/core/clients/detail.html) — Offline-Snackbar
- [`src/templates/core/clients/offline_detail.html`](./src/templates/core/clients/offline_detail.html) — _Dieses Klientel wurde nicht …_ → _Diese Person wurde nicht …_
- [`src/templates/core/events/create.html`](./src/templates/core/events/create.html) — _Klientel_-Autocomplete-Label, aria-label, Anonym-Hinweis → _Person_ bzw. _Person suchen_
- [`src/templates/core/events/edit.html`](./src/templates/core/events/edit.html), [`detail.html`](./src/templates/core/events/detail.html), [`deletion_review.html`](./src/templates/core/events/deletion_review.html), [`delete_confirm.html`](./src/templates/core/events/delete_confirm.html) — Detail-`<dt>`-Label _Klientel_ → _Person_; _qualifizierten Klientel_ → _Person mit qualifizierter Dokumentation_

**View-Texte / Messages:**

- [`src/core/views/clients.py`](./src/core/views/clients.py) — Erfolgsmeldungen _Klientel wurde erstellt/aktualisiert_ → _Person wurde angelegt/aktualisiert_

**Handbuch:**

- [`docs/user-guide.md`](user-guide.md) Kapitel „Klientel verwalten" → „Personen verwalten"; Fließtexte konsistent auf _Person_ umstellen.

**Seed / Übersetzung:**

- [`src/core/management/commands/seed.py`](./src/core/management/commands/seed.py) — _obdachlos_ nur dort verwenden, wo wirklich Straßenobdachlosigkeit gemeint ist; sonst _wohnungslos_ / _von Wohnungslosigkeit betroffen_.
- [`src/locale/de/LC_MESSAGES/django.po`](./src/locale/de/LC_MESSAGES/django.po) — bestehende Inkonsistenz (`msgid "Klientel"` → `msgstr "Klienten"`) entschärfen. Nach UI-Umbenennung werden die msgids ohnehin neu erzeugt; auf Kohärenz von _msgid_ und _msgstr_ auf _Person_ / _Personen_ achten.

## Diskutable Begriffe

- **niedrigschwellig** — behalten (Caritas, BAG W verwenden den Begriff weiterhin). In der UI nicht alleine stehen lassen, lieber konkretisieren: _ohne Termin_, _anonym möglich_, _einfacher Zugang_, _aufsuchend_.
- **Streetwork** — behalten, wenn tatsächlich Straßensozialarbeit gemeint ist. Andernfalls: _aufsuchende Arbeit_ / _aufsuchende Sozialarbeit_.
- **Fall** — im Case-Management fachlich nicht falsch, in niedrigschwelliger UI aber diskussionswürdig. Für interne Fachlogik bleibt _Fall_; in sichtbarer UI sind _Begleitung_ / _Unterstützungsprozess_ / _Verlauf_ oft weicher.
- **obdachlos vs. wohnungslos** — _wohnungslos_ ist der Oberbegriff; _obdachlos_ nur bei tatsächlichem Leben ohne Unterkunft.

## Priorisierung (Umsetzungsreihenfolge)

1. **Klientel → Person** in UI und Handbuch ersetzen (siehe Sofortkorrekturen-Liste oben).
2. **qualifiziert / identifiziert** als Personenetiketten entpersonalisieren (Kontaktstufen statt Personenmerkmale).
3. **obdachlos / wohnungslos** fachlich schärfen (Seed, Beispieltexte, Handbuch).
4. **Fall** strategisch prüfen — welche UI-Stellen bewusst Case-Management-Sprache brauchen, wo _Begleitung_ passt.

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

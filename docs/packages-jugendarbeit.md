# Paket „Jugendarbeit“

Das Paket richtet eine neue Einrichtung für mobile und offene Jugendarbeit ein, etwa für Streetwork, Fanprojekte oder Jugendzentren. Es unterstützt personenlose Strichlisten für Laufkundschaft und Gruppenangebote ebenso wie identifizierte Krisenarbeit und qualifizierte Beratung. Der U18-Schwerpunkt prägt Felder und empfohlene Aufbewahrungszeiten; kleine Fallzahlen bleiben in Berichten durch k-Anonymität unterdrückt. Das Streetwork-Profil ist offline nutzbar und erfasst Orte bewusst nur als Stadtteil oder Spot, nicht als genaue Adresse.

## Einspielen

Eine neue Einrichtung erhält das Paket beim interaktiven Einrichten:

```console
python manage.py setup_facility --package jugendarbeit
```

Das Jugendarbeit-Paket ist **Ersatz, nicht Zusatz**. Es wird in eine neue, katalogfreie Einrichtung eingespielt und nicht auf den Standardkatalog aufgesetzt. Ein bewusstes erneutes Laden erfolgt mit `python manage.py load_package --package jugendarbeit --facility <UUID>`; `--dry-run` zeigt die Änderungen vorab.

Das ist besonders beim wiederverwendeten Feld `leistungen` wichtig: Die Jugend-Optionen würden die Optionen des Standardkatalogs vollständig ersetzen, nicht ergänzen.

## Dokumentationstypen

`tally` kennzeichnet Typen, die zusätzlich für eine personenlose Strichliste vorgesehen sind. `identified` und `qualified` sind Mindest-Datenstufen eines Kontakts, keine Eigenschaften der Person.

| Slug | Name | Sensitivität | tally | min_contact_stage | system_type | report_key |
|---|---|---|:---:|---|---|---|
| `kontakt` | Kontakt | normal | ja ||| `contact` |
| `veranstaltungsteilnahme` | Veranstaltung / Angebot | normal | ja ||| `group_activity` |
| `bezugsarbeit` | Mobile Bezugsarbeit | normal | ja ||| `contact` |
| `krisengespraech-jugend` | Krisengespräch | elevated | nein | `identified` | `crisis` | `crisis` |
| `beratung-jugend` | Beratung | elevated | nein | `qualified` || `counseling` |
| `vermittlung` | Vermittlung (Jugendamt/Schule/Hilfe) | normal | ja ||| `referral` |
| `hausverbot-jugend` | Hausverbot | elevated | nein | `identified` | `ban` | `excluded` |
| `notiz` | Notiz | normal | ja ||| `excluded` |

## Felder

| Typ | Felder |
|---|---|
| `kontakt` | `leistungen` (Essen, Getränk, WLAN, Spiele, Aufenthaltsraum, Ladestation, Sonstiges), `alterscluster`, `strassenkontakt`, `dauer` (Minuten), `notiz` |
| `veranstaltungsteilnahme` | `angebotsname`, `teilnehmende` (Kopfzahl, mindestens 1), `zielgruppe` (Grundschule, Sekundarstufe I/II, geflüchtete Jugendliche, queere Jugendliche, offen) |
| `bezugsarbeit` | `ort` (Stadtteil oder Spot, keine Adresse), `anlass` (Streetwork, Schulhof, Fest, unterwegs angesprochen), `leistungen` |
| `krisengespraech-jugend` | `art-der-krise` (acht jugendspezifische Kategorien, `report_key: art_der_krise`), `notiz-krise` (high, verschlüsselt), `weitervermittlung-jugend` (Jugendamt, Schulsozialarbeit, Therapeut:in, Notdienst, keine) |
| `beratung-jugend` | `thema` und `vereinbarungen` (beide high und verschlüsselt), `sorgeberechtigte-informiert` (normal, unverschlüsselt) |
| `vermittlung` | keine dynamischen Felder |
| `hausverbot-jugend` | `grund` (Pflichtfeld), `bis`, `aktiv`; die Slugs entsprechen exakt der Hausverbots-Projektion |
| `notiz` | `notiz` |

Das Paket enthält bewusst **kein Datei-Feld**. `teilnehmende` ist ausschließlich eine Kopfzahl; Gruppenangebote führen keine Liste von Pseudonymen.

### Zwei verschiedene Alterscluster

Das Ereignisfeld `alterscluster` ist eine situative Schätzung für anonyme Kontakte. Es gehört zum Paket und kann im Ereignis erfasst werden. `Client.age_cluster` ist dagegen die systemweite Altersgruppe einer gespeicherten Person; sie steuert Statistik und Retention-Äquivalenzklassen. Beide kennen U18, bleiben aber technisch und fachlich unabhängig. Siehe [ADR-047](adr/047-altersgruppen-systemweit-fix.md) und [Admin-Handbuch § 2.5](admin-guide.md#25-dokumentationstypen-konfigurieren).

## Sensitivität und Rollen

Die Sichtbarkeit folgt der Rollenobergrenze der Anwendung. Ein nicht sichtbares Ereignis wird mit HTTP 404 verborgen, damit seine Existenz nicht verraten wird. High-Felder innerhalb eines elevated Typs sind für `staff` ausgeblendet und erst für `lead` oder `facility_admin` sichtbar.

| Dokumentationstyp | assistant | staff | lead | facility_admin |
|---|:---:|:---:|:---:|:---:|
| Kontakt | sichtbar | sichtbar | sichtbar | sichtbar |
| Veranstaltung / Angebot | sichtbar | sichtbar | sichtbar | sichtbar |
| Mobile Bezugsarbeit | sichtbar | sichtbar | sichtbar | sichtbar |
| Krisengespräch | **nicht sichtbar** | sichtbar, ohne High-Felder | sichtbar | sichtbar |
| Beratung | **nicht sichtbar** | sichtbar, ohne High-Felder | sichtbar | sichtbar |
| Vermittlung | sichtbar | sichtbar | sichtbar | sichtbar |
| Hausverbot | **nicht sichtbar** | sichtbar | sichtbar | sichtbar |
| Notiz | sichtbar | sichtbar | sichtbar | sichtbar |

Für Alltagskontakte kann `assistant` genügen. Krisen, Beratungen und Hausverbote benötigen mindestens `staff`; sensible Freitexte gehören in die Verantwortung von `lead` oder `facility_admin`. Rollen werden nicht durch das Paket oder eine Seed-Automatik vergeben.

## Verfügbare Berichte

- Der **Beispiel-Sachbericht** zählt Kontakte, Beratung, Vermittlung und Krisen sowie `veranstaltungsteilnahme` in der eigenen Zeile „Gruppenangebote“. Notiz und Hausverbot sind mit `excluded` ausdrücklich ausgenommen.
- Der **Leistungs-Bericht** wertet die Jugend-Optionen von `leistungen` an Kontakt und mobiler Bezugsarbeit aus.
- Der Bericht **Beratungsstunden** wertet `dauer` an Ereignissen mit `report_key: contact` aus. Im gelieferten Katalog trägt Kontakt dieses Feld; Beratung und Krise haben bewusst kein Minutenfeld.
- Die **Krisenarten-Verteilung** wertet `art-der-krise` aus.
- Die **Hausverbots-Übersicht** erkennt `hausverbot-jugend` über `system_type: ban` und zählt ausgesprochene, aktive und abgelaufene Hausverbote.

Für Berichte empfiehlt sich eine k-Anonymitäts-Schwelle von 5. Bei kleinen Gruppen unterdrückt die Anwendung dadurch Werte im Export.

## Betreiber-Empfehlungen

Diese Einstellungen sind nicht Teil des Paketformats und werden nicht automatisch gesetzt:

- `retention_identified_days`: **365 Tage** als Ausgangswert für identifizierte Jugendhilfe-Daten.
- `retention_anonymous_days`: **90 Tage**.
- k-Anonymitäts-Schwelle: **5**.
- Die gelieferten Zeitfenster „Nachmittag“ (14–18 Uhr) und „Abend“ (18–22 Uhr) an Öffnungs- und Streetwork-Zeiten anpassen. Diese zwei Fenster passen besser zur offenen Jugendarbeit als Früh-, Spät- und Nachtdienst.
- Die Schnellvorlagen „Kontakt JZ-Raum“ und „Veranstaltung (Personenzahl)“ fachlich prüfen und bei Bedarf ergänzen.
- Eine vorbefüllte Krisen-Schnellvorlage wird bewusst **nicht** ausgeliefert: Schnellvorlagen-Prefills sind nur an Typen mit Sensitivität `normal` erlaubt; bei `elevated` würde der Sicherheitsfilter die Vorbelegung ohnehin entfernen.

Outcome Goals werden ebenfalls nicht automatisch angelegt. Mögliche fachliche Bezeichnungen sind „Ausbildungsanschluss“, „Wohnungslose Situation beendet“ und „Schulabschluss“; sie sind Beispiele für die Betreiberkonfiguration, keine Paketdaten.

## Fachliche Entscheide

- Kontakt, Veranstaltung, mobile Bezugsarbeit, Vermittlung und Notiz bleiben ohne Mindest-Kontaktstufe anonym nutzbar. Krise und Hausverbot verlangen `identified`, Beratung verlangt `qualified`.
- `weitervermittlung-jugend` hat einen eigenen Slug, weil es eine Auswahl ist; das gleichnamige Standardfeld `weitervermittlung` ist Text und sein Feldtyp unveränderlich.
- `sorgeberechtigte-informiert` bleibt bewusst normal und unverschlüsselt; das Feld hält nur den Ja-/Nein-Status fest, keine Begründung oder Gesundheitsinformation.
- `hausverbot-jugend` verwendet `system_type: ban` und exakt `grund`/`bis`/`aktiv`, damit Banner und Hausverbots-Auswertung funktionieren.
- Das Paket enthält keine Terminplanung, Klarnamen-Akte oder Pseudonym-Liste für Gruppenangebote.

# Paket „Beratungsstelle“

**Refs:** #2145 · #1141

Das Paket richtet Anlaufstelle für eine offene Sprechstunde und Sozialberatung ein. Menschen kommen ohne Termin und ohne Klarnamen; die Kurzberatung bleibt deshalb anonym dokumentierbar. Ein Beratungsgespräch beginnt ab einem stabilen Pseudonym, damit die Einrichtung Folgetermine und Vereinbarungen sicher zuordnen kann. Krisen, Begleitungen zu Behörden oder Ämtern und Vermittlungen sind eigene Typen. Ein Hausverbot ist mitgeliefert, kann aber von der Einrichtung deaktiviert werden.

## Dokumentationstypen

| Slug | Name | Sensitivität | Strichliste | Mindest-Kontaktstufe | Systemtyp | Berichtsschlüssel |
|---|---|---|---:|---|---|---|
| `kontakt` | Kontakt (Sprechstunde) | normal | ja | — | — | `contact` |
| `kurzberatung` | Kurzberatung (anonym) | normal | ja | — | — | `counseling` |
| `beratungsgespraech` | Beratungsgespräch | erhöht | nein | `identified` | — | `counseling` |
| `krisengespraech` | Krisengespräch | erhöht | nein | — | `crisis` | `crisis` |
| `begleitung` | Begleitung | normal | nein | `identified` | — | `accompaniment` |
| `vermittlung` | Vermittlung | normal | ja | — | — | `referral` |
| `notiz` | Notiz | normal | ja | — | — | `excluded` |
| `hausverbot` | Hausverbot | erhöht | nein | — | `ban` | `excluded` |

## Felder

| Typ | Felder |
|---|---|
| Kontakt (Sprechstunde) | `dauer`, `alterscluster`, `leistungen`, `notiz` |
| Kurzberatung (anonym) | `thema-kurz`, `dauer`, `weiterverweis` |
| Beratungsgespräch | `thema`, `dauer`, `vereinbarungen`, `naechster-termin`, `scan-bescheid` |
| Krisengespräch | `art-der-krise`, `dauer`, `notiz-krise`, `weitervermittlung` |
| Begleitung | `ziel`, `datum`, `uhrzeit`, `notiz-begleitung` |
| Vermittlung | keine Felder |
| Notiz | `notiz` |
| Hausverbot | `grund` (Pflicht), `bis`, `aktiv` |

Die Auswahlwerte sind auf die Beratungs-Domäne zugeschnitten:

- `leistungen`: `information`, `formularhilfe`, `telefonat-fuer-klientin`, `weiterverweis`, `wartebereich`, `sonstiges`
- `thema-kurz`: `wohnen`, `geld-schulden`, `arbeit`, `gesundheit`, `familie-beziehung`, `behoerden-antraege`, `aufenthalt`, `sonstiges`
- `weiterverweis`: `interne-beratung`, `fachstelle`, `behoerde`, `keine`
- `art-der-krise`: `akute-belastung`, `suizidal`, `gewalt`, `wohnungsverlust`, `finanzielle-notlage`, `psychische-krise`, `sonstiges`

## Sensitivität und Rollen

| Rolle | Sichtbare Typen |
|---|---|
| Empfang (`assistant`) | Kontakt, Kurzberatung, Vermittlung, Notiz, Begleitung — ohne hochsensible Felder |
| Fachkraft (`staff`) | alle Typen bis `elevated` — hochsensible Felder bleiben verborgen |
| Leitung (`lead`) und Einrichtungsleitung (`facility_admin`) | alle Typen und Felder |

Konkret bedeuten die Ebenen:

- `assistant` sieht die Detailseite eines Ereignisses mit Sensitivität `elevated` nicht.
- `staff` sieht keine Ereignisse mit Sensitivität `high` und keine Felder mit `sensitivity: high`.
- Die Felder `thema`, `vereinbarungen`, `notiz-krise`, `notiz-begleitung` und `scan-bescheid` sind verschlüsselt und hochsensibel.

## Berichte

- **Sachbericht:** alle acht Typen sind über `contact`, `counseling`, `crisis`, `accompaniment`, `referral` oder `excluded` zugeordnet.
- **Beratungsstunden:** `kontakt`, `kurzberatung`, `beratungsgespraech` und `krisengespraech` zählen über `dauer` (`dauer_minuten`); alle vier `contact`/`counseling`/`crisis`-Typen tragen ein Minutenfeld. Gespeicherte Dauerwerte können dennoch fehlen oder unbrauchbar sein. Minuten einer Zeile werden unterdrückt („—“), wenn ihre Ereigniszahl unterdrückt ist oder weniger als k Ereignisse verwertbare Dauerwerte beitragen. Auch größere Ereigniszellen können durch Sekundärunterdrückung entfallen; fehlt ein veröffentlichter Zeilen-Minutenwert, wird auch die Minuten-Gesamtsumme unterdrückt. Zwei Kontakte mit je 30 Minuten bleiben bei `k=5` trotz vollständig definierter Dauerfelder unterdrückt. Der Vollständigkeitshinweis „Ereignisse ohne verwertbare Dauer sind in den Minutensummen nicht enthalten“ erscheint unabhängig davon: Er steht datenunabhängig und unbeziffert in jedem Beratungsstunden-Bericht, auch hier. Anonyme und pseudonyme Beratung erscheinen gemeinsam in der Sachbericht-Zeile „Beratung“.
- **Beispiel: Leistungs- und Krisenarten-Verteilung:** Dieser eine Bericht ist im Beratungsstelle-Paket über beide Gruppen verfügbar: Der Leistungs-Block wertet `leistungen` an Kontakt aus, der Krisen-Block `art-der-krise` (`report_key: art_der_krise`) an Krisengespräch.

## Betreiber-Empfehlungen

- Retention: 90 Tage für einfache Ereignisse und 365 Tage für erhöhte Ereignisse sind ein solider Start. `retention_qualified_days` sollte die Einrichtung trägerrechtlich prüfen; für Beratungsakten sind zum Beispiel 1825 Tage (5 Jahre) üblich, aber keine automatisch richtige Vorgabe.
- Statistische Veröffentlichung: `k=5` ist die Mindestgruppengröße für veröffentlichte Aggregate; kleinere Gruppen werden unterdrückt. Backup-Aufbewahrung verwendet davon unabhängige Zeitfristen; siehe [Admin-Handbuch § 3 — Backup und Wiederherstellung](admin-guide.md#3-backup-und-wiederherstellung).
- Zeitfenster: „Sprechstunde Vormittag“ (08:00–12:00), „Sprechstunde Nachmittag“ (13:00–17:00) und „Termine“ (17:00–20:00) sind vorbereitet und können angepasst werden.
- Schnellvorlagen: „Kurzinfo Sprechstunde“, „Formularhilfe“ und „Telefonische Kurzberatung“ sind vorbereitet.
- Rollen: Empfang sollte nur die niedrigschwelligen Typen sehen; Fachkraft übernimmt Beratungs- und Krisengespräche; Leitung prüft hochsensible Inhalte und Hausverbote.
- Hausverbot: der Typ ist bewusst mitgeliefert und kann deaktiviert werden, wenn die Einrichtung keine Hausverbote dokumentiert.

## Entscheide

1. `beratungsgespraech` verlangt `identified` — ein Pseudonym genügt. Die anonyme Kurzberatung ist der Vorlauf.
2. `hausverbot` wird als achter Typ mitgeliefert, steht am Ende der Reihenfolge und kann deaktiviert werden.
3. `kurzberatung` zählt über `counseling` und `dauer_minuten` automatisch in den Bericht „Beratungsstunden“ sowie in die Sachbericht-Zeile „Beratung“. Anonyme und pseudonyme Beratung stehen dort in einer Zeile.

## Einspielen

Für eine neue Einrichtung:

```bash
python manage.py setup_facility --package beratungsstelle
```

Für eine bestehende, noch leere oder vollständig vorbereitete Einrichtung:

```bash
python manage.py load_package --package beratungsstelle --facility <uuid>
```

Pakete sind **Ersatz, kein Zusatz**: `beratungsstelle.yaml` ersetzt den Standardkatalog und wird nicht zusätzlich zu `default.yaml` eingespielt. Der Seed nutzt dieses Paket für die Zweigstelle Nord — ab `--scale=medium` (#2148).

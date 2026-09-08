# Paket „Frauenhaus“

Das Paket richtet eine Schutzwohnung oder Fachberatungsstelle gegen Gewalt mit acht passenden Dokumentationstypen ein. Personen werden unter Pseudonym dokumentiert; eine identifizierte oder qualifizierte Kontaktstufe beschreibt den Arbeitsstand und ist kein Klarnamen-Zwang. Wegen der hohen Dichte an Gewalt-, Gesundheits- und Schutzinformationen trennt der Katalog Empfangsdaten, Fachberatung und Gefährdungslagen durch Sensitivitätsstufen. Korrespondenz bleibt außerhalb der Anwendung, etwa am Telefon oder auf Papier: Die Anwendung dokumentiert die Arbeit, sie ist keine vollständige Akte.

## Einspielen

Eine neue Einrichtung erhält das Paket beim interaktiven Einrichten:

```console
python manage.py setup_facility --package frauenhaus
```

Das Frauenhaus-Paket ist **Ersatz, nicht Zusatz**. Es wird in eine neue, katalogfreie Einrichtung eingespielt und nicht auf den Standardkatalog aufgesetzt. Ein bewusstes erneutes Laden erfolgt mit `python manage.py load_package --package frauenhaus --facility <UUID>`; `--dry-run` zeigt die Änderungen vorab.

## Dokumentationstypen

`tally` kennzeichnet Typen, die zusätzlich für die Strichliste vorgesehen sind.

| Slug | Name | Sensitivität | tally | min_contact_stage | system_type | report_key |
|---|---|---|:---:|---|---|---|
| `anfrage` | Anfrage (Erstkontakt) | normal | ja | `identified` || `contact` |
| `beratung` | Beratungsgespräch | elevated | nein | `qualified` || `counseling` |
| `gefaehrdungslage` | Gefährdungslage | **high** | nein | `qualified` | `crisis` | `crisis` |
| `begleitung` | Begleitung (Behörde/Gericht/Polizei) | normal | nein | `identified` || `accompaniment` |
| `schutzmassnahme` | Schutzmaßnahme | elevated | nein | `identified` || `protection` |
| `wohnungswechsel` | Wohnungswechsel/Übergang | normal | nein | `identified` || `contact` |
| `vermittlung` | Vermittlung | normal | ja | `identified` || `referral` |
| `notiz` | Notiz | normal | ja ||| `excluded` |

`schutzmassnahme` trägt bewusst **keinen** `system_type`: Die Maßnahme schützt die Person und ist kein Hausverbot gegen sie. Daher speist dieser Typ weder Hausverbots-Banner noch Hausverbots-Übersicht. `gefaehrdungslage` trägt dagegen `system_type: crisis` und erscheint dadurch in den Krisen-Highlights von Zeitstrom und Übergabe. Diese Ansichten filtern nach Rollensichtbarkeit: Eine `staff`-Schicht sieht die Gefährdungslage in der Übergabe nicht; wer sie dort lesen soll, braucht mindestens `lead`.

## Felder

| Typ | Felder |
|---|---|
| `anfrage` | `kontaktweg` (Telefon, persönlich, weitergeleitete Mail, dritte Stelle), `anliegen` (Unterbringung, Beratung, Rechtliches, Finanzen, Kind, Sonstiges), `aufnahme-moeglich` |
| `beratung` | `thema` (high, verschlüsselt), `dauer` (Minuten), `vereinbarungen` (high, verschlüsselt), `naechster-schritt`, `scan-dokument` (Datei, high, verschlüsselt) |
| `gefaehrdungslage` | `ausloeser` (Kategorie mit `report_key: art_der_krise`), `notiz-gefaehrdung` (high, verschlüsselt), `eskalationskanal`; durch den high-Typ sind **alle drei Felder effektiv high** |
| `begleitung` | `ziel`, `datum`, `uhrzeit`, `notiz-begleitung` (high, verschlüsselt) |
| `schutzmassnahme` | `art` (einstweilige Anordnung, Schutzantrag, Kontaktverbot, Räumungsankündigung, Sonstiges), `gueltig-bis` |
| `wohnungswechsel` | `von-nach` (Aufnahme, interner Wechsel, Auszug in eigene Wohnung, Auszug in andere Einrichtung, Sonstiges), `datum` |
| `vermittlung` | `vermittelt-an` (Rechtsberatung, Jugendamt, Jobcenter, ärztlich, Therapie, Sonstiges) |
| `notiz` | `notiz` |

`scan-dokument` ist das **einzige Datei-Feld** des Pakets. Dateien werden im File-Vault verschlüsselt gespeichert. Neue JPG- und PNG-Uploads durchlaufen außerdem die Metadaten-Bereinigung aus #2128; Dokumenteigenschaften in PDF-Dateien werden davon nicht bereinigt und müssen vor dem Upload organisatorisch geprüft werden.

## Sensitivität und Rollen

Die Sichtbarkeit folgt der Rollenobergrenze der Anwendung. Ein nicht sichtbares Ereignis wird mit HTTP 404 verborgen, damit seine Existenz nicht verraten wird.

| Dokumentationstyp | assistant | staff | lead | facility_admin |
|---|:---:|:---:|:---:|:---:|
| Anfrage (Erstkontakt) | sichtbar | sichtbar | sichtbar | sichtbar |
| Beratungsgespräch | **nicht sichtbar** | sichtbar | sichtbar | sichtbar |
| Gefährdungslage | **nicht sichtbar** | **nicht sichtbar** | sichtbar | sichtbar |
| Begleitung | sichtbar | sichtbar | sichtbar | sichtbar |
| Schutzmaßnahme | **nicht sichtbar** | sichtbar | sichtbar | sichtbar |
| Wohnungswechsel/Übergang | sichtbar | sichtbar | sichtbar | sichtbar |
| Vermittlung | sichtbar | sichtbar | sichtbar | sichtbar |
| Notiz | sichtbar | sichtbar | sichtbar | sichtbar |

Felder erben mindestens die Sensitivität ihres Typs. Die folgende Matrix nennt jedes Feld und damit insbesondere jedes explizit oder durch Vererbung effektiv hochsensible Feld.

| Typ | Felder | effektiv | assistant | staff | lead | facility_admin |
|---|---|---|:---:|:---:|:---:|:---:|
| Anfrage | `kontaktweg`, `anliegen`, `aufnahme-moeglich` | normal | sichtbar | sichtbar | sichtbar | sichtbar |
| Beratung | `dauer`, `naechster-schritt` | elevated | **nicht sichtbar** | sichtbar | sichtbar | sichtbar |
| Beratung | `thema`, `vereinbarungen`, `scan-dokument` | **high** | **nicht sichtbar** | **nicht sichtbar** | sichtbar | sichtbar |
| Gefährdungslage | `ausloeser`, `notiz-gefaehrdung`, `eskalationskanal` | **high** | **nicht sichtbar** | **nicht sichtbar** | sichtbar | sichtbar |
| Begleitung | `ziel`, `datum`, `uhrzeit` | normal | sichtbar | sichtbar | sichtbar | sichtbar |
| Begleitung | `notiz-begleitung` | **high** | **nicht sichtbar** | **nicht sichtbar** | sichtbar | sichtbar |
| Schutzmaßnahme | `art`, `gueltig-bis` | elevated | **nicht sichtbar** | sichtbar | sichtbar | sichtbar |
| Wohnungswechsel | `von-nach`, `datum` | normal | sichtbar | sichtbar | sichtbar | sichtbar |
| Vermittlung | `vermittelt-an` | normal | sichtbar | sichtbar | sichtbar | sichtbar |
| Notiz | `notiz` | normal | sichtbar | sichtbar | sichtbar | sichtbar |

Damit sind Auslöser und Eskalationskanal einer Gefährdungslage trotz unverschlüsselter Kategoriewerte niemals unterhalb `lead` sichtbar. Freitext und Dateiinhalt mit eigener `sensitivity: high` werden zusätzlich feldverschlüsselt gespeichert.

## Berichte

- **Sachbericht:** Alle acht Typen sind über bekannte Berichtsschlüssel zugeordnet. `schutzmassnahme` erscheint in der eigenen Zeile **„Schutz“**; Notizen sind mit `excluded` ausdrücklich ausgenommen.
- **Beratungsstunden:** Beratungsgespräche liefern über `dauer` (`dauer_minuten`) dokumentierte Minuten in der Kategorie `counseling`. Wegen ihrer Berichtsschlüssel bezieht der Bericht auch `anfrage`, `wohnungswechsel` und `gefaehrdungslage` ein; da diese Typen kein `dauer`-Feld haben, erscheinen ihre Ereignisse mit 0 Minuten und erzeugen erwartungsgemäß den Vollständigkeitshinweis „Ereignisse ohne Wertangabe“.
- **Beispiel: Leistungs- und Krisenarten-Verteilung:** Dieser eine Bericht ist im Frauenhaus-Paket über `ausloeser` (`report_key: art_der_krise`) verfügbar und füllt nur den Krisen-Block; ein Leistungs-Block ist mangels `report_key: leistungen` nicht verfügbar. Der publizierte Spaltenwert bleibt bewusst `art-der-krise`, der `summary_key` bleibt `events_art_der_krise` – beide sind vom Feldnamen „Auslöser“ entkoppelt.
- **Hausverbots-Übersicht:** bewusst **nicht verfügbar**, weil das Paket keinen Typ mit `system_type: ban` enthält.

Die konfigurierte k-Anonymitäts-Schwelle gilt auch für diese Berichte; kleine Fallzahlen werden unterdrückt.

## Betreiber-Empfehlungen

Diese Einstellungen sind nicht Teil des Paketformats und werden nicht automatisch gesetzt:

- `retention_qualified_days`: passend zur Rechtsgrundlage und den Trägervorgaben festlegen, beispielsweise **1825 Tage** statt des allgemeinen Ausgangswerts 3650.
- `k_anonymity_threshold`: bei **5** belassen; die Unterdrückung kleiner Zahlen ist gerade bei kleinen Häusern gewollt.
- `allowed_file_types`: auf **pdf, jpg, jpeg, png** begrenzen.
- Unter **Einstellungen → Mitschauen-Schutz** eine unverfängliche `quick_exit_url` festlegen und `privacy_screen_on_blur` aktivieren. Schnell-Exit und Sichtschutz schützen am Empfangsplatz; sie ersetzen keinen physischen Blickschutz, und beim Schnell-Exit gehen ungespeicherte Eingaben verloren.
- Die vorbereiteten Zeitfenster „Frühdienst“, „Spätdienst“ und „Nachtdienst“ an den tatsächlichen 24/7-Schichtbetrieb anpassen.
- Die Schnellvorlagen „Telefonische Anfrage Unterbringung“ und „Belegstand vermerkt“ fachlich prüfen. Nur die erste Vorlage setzt `kontaktweg: telefon` und `anliegen: unterbringung` vorab; Vorbelegungen sind ausschließlich an Typen mit Sensitivität `normal` zulässig.
- Empfang als `assistant`, Fachkräfte als `staff` und die für Gefährdungslagen zuständigen Personen als `lead` oder `facility_admin` einrichten.

Es gibt keine Seed-Automatik für Betreiber-Settings oder Rollen. Retention, erlaubte Dateitypen, k-Anonymität, Mitschauen-Schutz und Benutzerrollen werden nach dem Einspielen bewusst durch die Anwendungsbetreuung festgelegt.

## Fallarbeit und fachliche Entscheide

Fall, Episode und OutcomeGoal bleiben unverändert nutzbar. Sinnvolle OutcomeGoal-Beispiele sind „Sichere Wohnsituation erreicht“, „Gewaltschutzantrag eingereicht“ und „Finanzielle Erstsicherung“; das Paket legt diese Texte nicht automatisch an.

- `gefaehrdungslage` ist der einzige Typ mit `system_type: crisis` und wegen seiner effektiven `high`-Sensitivität nur für `lead` und `facility_admin` sichtbar.
- `schutzmassnahme` ist ausdrücklich kein Hausverbot und besitzt keinen `system_type`.
- Korrespondenz und vollständige Aktenführung bleiben außerhalb der Anwendung.
- Ein optionaler Belegstand-Zähler bleibt dem Folgeticket #2142 vorbehalten.

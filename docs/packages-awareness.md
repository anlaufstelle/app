# Paket „Awareness“

Das Paket richtet eine dauerhafte Facility für ein Awareness-Team ein, das bei Festivals, in Klubs oder bei anderen Veranstaltungen arbeitet. Eine Veranstaltung ist dabei **keine eigene Facility**: Die Facility steht für das Team beziehungsweise die Organisation. Einzelne Veranstaltungen werden immer über den Datumsbereich in Statistik und Export unterschieden; nur Awareness-Kontakt, Versorgung und Vermittlung tragen zusätzlich das Feld `veranstaltung`. Die Dokumentation ist auf überwiegend anonyme Kurzkontakte, Zeitdruck und sensible Vorfallsdaten zugeschnitten; kleine Fallzahlen bleiben in Abschlussberichten durch k-Anonymität unterdrückt.

## Einspielen

Eine neue Einrichtung erhält das Paket beim interaktiven Einrichten:

```console
docker compose -f docker-compose.prod.yml run --rm maintenance setup_facility --package awareness
```

Die Befehle laufen im Dienst `maintenance` (Admin-Rolle, [Ops-Runbook § 9](ops-runbook.md#db-rollenmodell-goldstandard)); unter der App-Rolle von `web` bricht `setup_facility` vor der ersten Eingabe ab.

Das Awareness-Paket ist **Ersatz, nicht Zusatz**. Es wird in eine neue, katalogfreie Einrichtung eingespielt und nicht auf den Standardkatalog aufgesetzt. Ein bewusstes erneutes Laden erfolgt mit `docker compose -f docker-compose.prod.yml run --rm maintenance load_package --package awareness --facility <UUID>`; `--dry-run` zeigt die Änderungen vorab.

## Dokumentationstypen

Keiner der sieben Typen verlangt eine Mindest-Kontaktstufe. Auch Gewaltvorfall und Krisensituation bleiben ohne `min_contact_stage`, weil betroffene Personen häufig anonym bleiben müssen. `tally` kennzeichnet Typen, die zusätzlich für eine personenlose Strichliste vorgesehen sind.

| Slug | Name | Sensitivität | tally | min_contact_stage | system_type | report_key |
|---|---|---|:---:|---|---|---|
| `awareness-kontakt` | Awareness-Kontakt | normal | ja | — | — | `contact` |
| `versorgung` | Versorgung | normal | ja | — | — | `supply` |
| `gewaltvorfall` | Gewaltvorfall | elevated | nein | — | — | `incident` |
| `krisensituation` | Krisensituation | elevated | nein | — | `crisis` | `crisis` |
| `orga-meldung` | Orga-Meldung (Platzverweis) | elevated | nein | — | `ban` | `excluded` |
| `vermittlung` | Vermittlung | normal | ja | — | — | `referral` |
| `notiz` | Notiz | normal | ja | — | — | `excluded` |

## Felder

| Typ | Felder |
|---|---|
| `awareness-kontakt` | `anliegen` (Mehrfachauswahl: Orientierung, Gespräch, Belästigung, Unwohlsein, Begleitung, Sonstiges), `alterscluster`, `dauer` (Minuten), `veranstaltung` |
| `versorgung` | `bedarf` (Mehrfachauswahl: Wasser, Decke, Ruheraum, Gehörschutz, Hygieneartikel, Sonstiges), `veranstaltung` |
| `gewaltvorfall` | `art-des-vorfalls` (acht Vorfallsarten), `beteiligung`, `ort-im-veranstaltungsgelaende` (high, verschlüsselt), `weitergabe-an`, `folgesituation`, `notiz-vorfall` (high, verschlüsselt) |
| `krisensituation` | `art-der-krise` (mit `report_key: art_der_krise`), `notiz-krise` (high, verschlüsselt), `weitervermittlung` |
| `orga-meldung` | `grund` (Pflichtfeld), `bis`, `aktiv`; die Slugs entsprechen exakt der Hausverbots-Projektion |
| `vermittlung` | `vermittelt-an` (Sanität, Psy-Support, Security, Taxi / Safe Home, Sonstiges), `veranstaltung` |
| `notiz` | `notiz` |

Das gesamte Paket enthält bewusst **kein Datei-Feld**. Fotos von betroffenen Personen dürfen nicht gespeichert werden; Vorfälle werden kategorisiert und notwendige Freitexte verschlüsselt dokumentiert.

## Sensitivität und Rollen

Die Sichtbarkeit folgt der Rollenobergrenze der Anwendung. Ein nicht sichtbares Ereignis wird mit HTTP 404 verborgen, damit seine Existenz nicht verraten wird.

| Dokumentationstyp | assistant | staff | lead | facility_admin |
|---|:---:|:---:|:---:|:---:|
| Awareness-Kontakt | sichtbar | sichtbar | sichtbar | sichtbar |
| Versorgung | sichtbar | sichtbar | sichtbar | sichtbar |
| Gewaltvorfall | **nicht sichtbar** | sichtbar | sichtbar | sichtbar |
| Krisensituation | **nicht sichtbar** | sichtbar | sichtbar | sichtbar |
| Orga-Meldung | **nicht sichtbar** | sichtbar | sichtbar | sichtbar |
| Vermittlung | sichtbar | sichtbar | sichtbar | sichtbar |
| Notiz | sichtbar | sichtbar | sichtbar | sichtbar |

Innerhalb eines sichtbaren Typs gilt zusätzlich die Sensitivität des einzelnen Felds:

| High-Feld | Typ | assistant | staff | lead | facility_admin |
|---|---|:---:|:---:|:---:|:---:|
| `ort-im-veranstaltungsgelaende` | Gewaltvorfall | **nicht sichtbar** | **nicht sichtbar** | sichtbar | sichtbar |
| `notiz-vorfall` | Gewaltvorfall | **nicht sichtbar** | **nicht sichtbar** | sichtbar | sichtbar |
| `notiz-krise` | Krisensituation | **nicht sichtbar** | **nicht sichtbar** | sichtbar | sichtbar |

Empfohlene Rollenverteilung: Die Koordination arbeitet als `lead`; Crew-Mitglieder erhalten abhängig vom Sichtbarkeitsbedarf `staff` oder `assistant`. In Produktion ist MFA per Rollen-Default für alle Rollen verpflichtend — für `lead`, `staff` und `assistant` nach 30 Tagen Übergangsfrist ab der ersten Anmeldung. Ein zusätzlicher facility-weiter MFA-Zwang (sofort, ohne Frist) ist bei ehrenamtlicher Crew auf privaten Geräten nicht nötig. Wer für die Crew bewusst keine Pflicht will, entfernt `staff`/`assistant` aus `MFA_ENFORCED_ROLES_GRACE` (Betrieb, siehe [Admin-Handbuch § 2.7](admin-guide.md#27-zwei-faktor-authentifizierung-2fa)).

## Berichte und Veranstaltungssicht

Kontakt, Versorgung, Gewaltvorfall, Krise und Vermittlung fließen über ihre bekannten `report_key`-Werte in den Beispiel-Sachbericht ein. Der Bericht **Beratungsstunden** bezieht Ereignisse mit `report_key: contact` und `crisis` ein — also Awareness-Kontakt und Krisensituation. Nur Awareness-Kontakt trägt `dauer`; Krisensituation hat kein Minutenfeld, ihre Zeile zeigt deshalb „—“ statt eines Minutenwerts. Der Vollständigkeitshinweis „Ereignisse ohne verwertbare Dauer sind in den Minutensummen nicht enthalten“ erscheint trotzdem: Er steht datenunabhängig und unbeziffert in jedem Beratungsstunden-Bericht. Der Bericht **Beispiel: Leistungs- und Krisenarten-Verteilung** ist im Awareness-Paket nur über den Krisen-Block verfügbar (`art-der-krise`, `report_key: art_der_krise`); ein Leistungs-Block ist mangels `report_key: leistungen` nicht verfügbar, weil das Paket kein `leistungen`-Feld enthält. Orga-Meldung und Notiz sind mit `excluded` ausdrücklich von fachlichen Berichtskategorien ausgenommen.

Für eine Veranstaltung wird in Statistik und Export immer ein Datumsbereich gewählt. Nur Awareness-Kontakt, Versorgung und Vermittlung lassen sich zusätzlich über ihr Feld `veranstaltung` gruppieren; Gewaltvorfall, Krisensituation, Orga-Meldung und Notiz tragen dieses Feld nicht. `TimeFilter` bildet ausschließlich Tageszeiten ab; die mitgelieferten Fenster „Abend“ (18:00–00:00) und „Nacht“ (00:00–06:00) sind keine Veranstaltungscontainer. Ein echtes Veranstaltungs-Aggregat ist in #2143 vorgesehen. Abschlussberichte sollten die konfigurierte k-Anonymitäts-Schwelle verwenden; kleine Gruppen werden dadurch unterdrückt.

## Betreiber-Empfehlungen

Diese Einstellungen sind nicht Teil des Paketformats und werden nicht automatisch gesetzt:

- `retention_anonymous_days`: je nach Veranstaltung und Rechtsgrundlage **14 bis 30 Tage**.
- `retention_identified_days`: **60 Tage** als knapper Ausgangswert, anschließend organisatorisch prüfen.
- `allowed_file_types`: restriktiv konfigurieren, obwohl das Paket selbst keine Datei-Felder enthält.
- MFA-Pflicht über den Rollen-Default belassen (Anwendungsbetreuung sofort, Leitung und Crew nach Übergangsfrist); `mfa_enforced_facility_wide` für BYOD-/Ehrenamts-Teams nicht zusätzlich aktivieren. Abweichungen nur über `MFA_ENFORCED_ROLES_GRACE`.
- Die gelieferten Zeitfenster „Abend“ und „Nacht“ an die tatsächlichen Einsatzzeiten anpassen.
- Die Schnellvorlagen „Wasser gegeben“ und „Ruheraum begleitet“ fachlich prüfen und bei Bedarf ergänzen. Schnellvorlagen mit Vorbelegung sind nur an Typen der Sensitivität normal wirksam — der Sicherheitsfilter verwirft Vorbelegungen an erhöhten Typen.

Es gibt keine Seed-Automatik für Betreiber-Settings oder Rollen. Retention, erlaubte Dateitypen, k-Anonymität, MFA und Benutzerrollen werden nach dem Einspielen bewusst durch die Anwendungsbetreuung festgelegt.

## Fachliche Entscheide

- Alle Typen bleiben anonym nutzbar; auch bei Gewalt- und Krisensituationen wird keine Identifizierung erzwungen.
- `orga-meldung` verwendet `system_type: ban` und exakt `grund`/`bis`/`aktiv`, damit Banner und Hausverbots-Auswertung funktionieren.
- Die Veranstaltung wird als Datumsbereich dokumentiert, nicht als Facility oder TimeFilter-Container; nur Awareness-Kontakt, Versorgung und Vermittlung führen zusätzlich den Feldwert `veranstaltung`.
- Es gibt kein `leistungen`-Feld und damit keinen Leistungs-Bericht; Versorgung hat die eigene Berichtskategorie `supply`.
- Kein Datei-Feld und keine Fotodokumentation von betroffenen Personen.

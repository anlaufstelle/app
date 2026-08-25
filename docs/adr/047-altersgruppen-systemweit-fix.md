# ADR-047: Altersgruppen bleiben systemweit fix

- **Status:** Accepted
- **Date:** 2026-08-24
- **Deciders:** Tobias Nix
- **Refs:** #1877 (RFC), [#1941](https://github.com/anlaufstelle/app/issues/1941#issuecomment-5385075873) (M4-Entscheid)

## Context

Anlaufstelle speichert an einer Person kein Geburtsdatum und kein genaues Alter,
sondern ausschließlich eine grobe Altersgruppe. Das ist bewusste
Datenminimierung. Die vier Werte liegen heute als `Client.AgeCluster` im
Kernmodell:

| stabiler Wert | sichtbares Label |
|---|---|
| `u18` | Unter 18 |
| `18_26` | 18–26 |
| `27_plus` | 27+ |
| `unknown` | Unbekannt |

Diese Werte sind mehr als die Optionen eines Formularfelds. Sie werden unter
anderem von Personen-Erfassung und -Filter, CSV-Import, Offline-Payload,
Berichtigung und Art.-15-Auskunft verwendet. Statistik-Dashboard und
Datensatz-Berichte gruppieren danach. Monats-Snapshots frieren Schlüssel und
Label ein; der Hybridpfad führt Snapshot- und Live-Segmente wieder über den
Schlüssel zusammen. Die Retention nutzt `(age_cluster, contact_stage)` als
K-Anonymitäts-Äquivalenzklasse und kann eine zu kleine Klasse auf `unknown`
zurückstufen.

Eine einrichtungsbezogene Konfiguration sähe an der Oberfläche klein aus, wäre
fachlich aber kein normales Settings-Feld. Ändert eine Einrichtung Grenzen,
kennt Anlaufstelle für Bestandsdaten nur den alten Bucket, nicht das Alter, aus
dem ein neuer Bucket berechnet werden könnte. Alte und neue Monate wären ohne
Version des Bucket-Sets nicht vergleichbar. Feinere Gruppen würden außerdem die
Kleinstfall-Unterdrückung in Berichten und die Retention-Degradation verändern.

Das frei konfigurierbare Zusatzfeld `alterscluster` aus dem Standardpaket ist
davon unabhängig: Es erfasst eine Auswahl an einem Ereignis, etwa für einen
anonymen Kontakt. Seine Optionen konfigurieren weder `Client.age_cluster` noch
die eingebauten Altersgruppen-Statistiken.

## Decision

### 1. `Client.AgeCluster` ist ein systemweiter, fester Katalog

Die vier Werte und ihre Grenzen gelten installationsweit. Sie sind nicht pro
Einrichtung, Konfigurationspaket oder Bericht änderbar. Es entsteht weder ein
Settings-JSON-Feld noch ein Bucket-Katalogmodell.

Die Administrationsoberfläche beschreibt diese Grenze ausdrücklich. Das
Fachkonzept und die Glossare dürfen keine Konfigurierbarkeit versprechen, die
der Code nicht besitzt.

### 2. Eine Änderung ist eine versionierte Datenmodell-Entscheidung

Eine spätere Änderung am Katalog ist keine harmlose Choice-Erweiterung. Sie
braucht vorab einen eigenen ADR und ein Konzept für:

1. die Bedeutung und Versionierung eines Bucket-Sets,
2. Bestandswerte, die mangels Rohalter nicht neu zugeordnet werden können,
3. Vergleich und Merge von Live-Daten und bestehenden Monats-Snapshots,
4. beide K-Anonymitäts-Pfade (Berichtssuppression und Retention),
5. Import, Export, Offline-Daten und Berichtigungen,
6. das Berichtsvokabular und die sichtbare Zuordnung über `report_key`.

Der in #1847 und
[ADR-042](042-report-key-berichtszuordnung.md) etablierte `report_key` ist die
Andockstelle für Berichtsvokabular, macht die Personen-Buckets selbst aber
nicht konfigurierbar.

### 3. Keine nachträgliche Sammlung eines genauen Geburtsdatums als Abkürzung

Der Wunsch nach anderen Buckets rechtfertigt für sich allein kein Geburtsdatum
und kein genaues Alter. Eine solche Datenerweiterung müsste einen eigenen
Zweck, Rechtsgrundlage, Zugriff, Retention und Schutzbedarf nachweisen. Diese
ADR trifft dafür keine Vorentscheidung.

### 4. Gemeinsame Labels werden aus dem Modell abgeleitet

Wo ein Bericht dieselben vier sichtbaren Labels benötigt, soll er sie aus
`Client.AgeCluster.choices` beziehen statt eine zweite Tabelle zu pflegen. Der
Beispiel-Sachbericht tut dies mit diesem Entscheid. Stabile Schlüssel im
persistierten Snapshot bleiben unverändert.

## Wiedereröffnungs-Bedingungen

Die Konfigurierbarkeit wird erst neu bewertet, wenn mindestens eine der
folgenden Voraussetzungen erfüllt ist:

1. Eine verbindliche fachliche oder externe Berichtsvorgabe verlangt andere
   Grenzen und lässt sich nicht als eigener, über `report_key` gebundener
   Bericht abbilden.
2. Ein versioniertes Bucket-Set kann an jeden gespeicherten Personenwert und
   jeden Snapshot gebunden werden.
3. Für Altwerte ist ausdrücklich entschieden, ob sie dauerhaft im alten Set
   bleiben, nur „ab jetzt“ gelten oder auf einer zulässigen Rohangabe neu
   berechnet werden; eine stillschweigende Neuzuordnung ist ausgeschlossen.
4. Die Folgen für Kleinstfall-Unterdrückung und Retention sind anhand realer
   Mindestgrößen bewertet und fail-closed getestet.
5. Import-, Export-, Offline- und Plugin-Verträge können mehr als ein Bucket-Set
   eindeutig transportieren.

Bis dahin ist „andere Zielgruppe“ allein kein sicherer Migrationsplan.

## Consequences

### Positiv

- Bestands- und Neudaten behalten dieselbe Bedeutung; Snapshot-Zeiträume sind
  ohne zusätzliche Versionsdimension zusammenführbar.
- Die Datenminimierung bleibt erhalten: kein Geburtsdatum, kein exaktes Alter.
- K-Anonymitätsgruppen und Berichtsvokabular ändern sich nicht durch lokale
  Administration.
- Dokumentation und Code sagen nun dieselbe Wahrheit; die zweite Labeltabelle
  im Beispielbericht entfällt.

### Negativ / bewusst akzeptiert

- Seniorenarbeit, Jugendhilfe oder andere Fachgebiete können die Grenzen nicht
  an ihre Zielgruppe anpassen.
- `27+` ist für viele Einrichtungen sehr grob; ein erweitertes festes Set würde
  diese Lücke nur verschieben und Bestandswerte weiterhin nicht aufteilen.
- Eine künftige echte Konfigurierbarkeit wird größer, weil sie Versionierung und
  mehrere Datenpfade gemeinsam lösen muss.

## Alternatives considered

- **Frei pro Einrichtung konfigurieren:** fachlich flexibel, aber ohne
  Bucket-Set-Version und Rohalter nicht migrations- oder snapshotfähig;
  zusätzliche RLS-, UI-, Import-/Export- und K-Anonymitätsfläche — verworfen.
- **Erweitertes festes Set:** vermeidet ein neues Modell, löst aber
  zielgruppenspezifische Schnitte nicht und kann `27+` im Bestand nicht
  nachträglich teilen — verworfen.
- **Presets über Konfigurationspakete:** passt zum No-Code-Werkzeug, benötigt
  aber trotzdem eine persistierte Set-Version und eine Semantik für
  Bestandswerte; Pakete allein lösen das Datenproblem nicht — verworfen.
- **Genaues Alter oder Geburtsdatum speichern:** würde eine Neuberechnung
  erlauben, vergrößert aber die personenbezogene Datenmenge erheblich und hat
  für diesen Wunsch keinen eigenständigen Zweck — verworfen.

## References

- Modell: [`core/models/client.py`](../../src/core/models/client.py)
- Statistik und Snapshot-Merge:
  [`dashboard/statistics.py`](../../src/core/services/dashboard/statistics.py),
  [`dashboard/snapshot.py`](../../src/core/services/dashboard/snapshot.py)
- Bericht: [`reporting/backends/sample_report_1.py`](../../src/core/reporting/backends/sample_report_1.py)
- RFC/Entscheid: #1877,
  [#1941](https://github.com/anlaufstelle/app/issues/1941#issuecomment-5385075873)

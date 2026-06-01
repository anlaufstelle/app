
## K-Anonymität im Detail

**K-Anonymität** ist ein etabliertes Datenschutzprinzip gegen Re-Identifikation in
Statistiken und Berichten. Eine Auswertung ist *k-anonym*, wenn sich jede Person in
Bezug auf die ausgewerteten Merkmale von mindestens **k − 1** anderen Personen nicht
unterscheiden lässt — jede Merkmalskombination kommt also **mindestens k-mal** vor.

**Alltagsbeispiel (k = 5):** Eine Statistik gruppiert Kontakte nach *Alterscluster*
und *Geschlecht*. Gibt es in einer Einrichtung nur **eine** Person der Kombination
„unter 18 / divers", würde diese Zeile faktisch eine einzelne Person offenlegen
obwohl die Statistik „anonym" wirkt. Bei k = 5 wird eine solche Gruppe mit weniger
als 5 Personen **unterdrückt** (kein Zahlenwert, sondern „—" bzw. `suppressed`),
sodass aus dem Aggregat niemand zurückverfolgt werden kann.

**In Anlaufstelle:**

- Der Schwellwert ist pro Einrichtung über das Setting
 [`k_anonymity_threshold`](https://github.com/anlaufstelle/app/blob/main/src/core/models/settings.py)
 konfigurierbar (**Default 5**).
- Datenschutzfreundliche externe Berichte
 ([`core/services/external_report.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/external_report.py))
 wenden die Schwelle auf alle Aggregate an und entfernen Pseudonym-Rankings vollständig.
- Optional kann der Retention-Löschpfad Personendaten per K-Anonymisierung
 verallgemeinern statt sie hart zu pseudonymisieren (Setting
 `retention_use_k_anonymization`).

Abgrenzung: **Pseudonymisierung** ersetzt direkte Identifikatoren durch ein Pseudonym
(Einzeldatensatz bleibt bestehen); **K-Anonymität** schützt zusätzlich vor
Re-Identifikation über *Kombinationen* indirekter Merkmale in Aggregaten.

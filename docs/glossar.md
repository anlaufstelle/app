
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
 ([`core/services/dashboard/external_report.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/dashboard/external_report.py))
 wenden die Schwelle auf alle Aggregate an und entfernen Pseudonym-Rankings vollständig.
- Optional ersetzt der Retention-Löschpfad den Hard-Delete durch
 K-Anonymisierung des Client-Records (Setting `retention_use_k_anonymization`).
 **Beide Pfade tilgen seit #1094 dieselbe Freitext-Kaskade** auf
 Fälle/Episoden/Aufgaben (im Retention-Bridge-Layer); die Primitive
 `k_anonymize_client` selbst bleibt client-only. Siehe **k-Anon-Retention** unten.

Abgrenzung: **Pseudonymisierung** ersetzt direkte Identifikatoren durch ein Pseudonym
(Einzeldatensatz bleibt bestehen); **K-Anonymität** schützt zusätzlich vor
Re-Identifikation über *Kombinationen* indirekter Merkmale in Aggregaten.

## Datenschutz-Begriffe

**Personenbezogene Daten / PII** — Informationen, die eine natürliche Person direkt
(Name, Adresse, Klient-ID) oder indirekt über *Quasi-Identifikatoren* (Alter, Region,
Anliegen in Kombination) identifizierbar machen (Art. 4 Nr. 1 DSGVO). Gesundheits-/
Sozialdaten sind *besondere Kategorien* (Art. 9). Höchstes Leak-Risiko: Freitext.

**Pseudonymisierung** — ersetzt direkte Identifikatoren durch ein Pseudonym; der
Einzeldatensatz bleibt bestehen und ist mit Zusatzwissen re-identifizierbar. Schwächer
als Anonymisierung. Abgrenzung zu **K-Anonymität** siehe oben.

**Speicherbegrenzung** — Grundsatz aus Art. 5(1)(e) DSGVO: Personendaten nicht länger
als für den Zweck erforderlich aufbewahren. In Anlaufstelle über das Retention-Modell ([ADR-021](adr/021-retention-modell.md))
umgesetzt.

**Soft-Delete-Strategien** — die vier Retention-Strategien statt Hard-Delete:
`anonymous` (sofort nach Frist), `identified` (Anonymisierung nach Frist),
`qualified` (Freigabe via RetentionProposal nötig), `document_type` (Frist pro
Dokumenttyp). Details: [ADR-021](adr/021-retention-modell.md).

**Legal Hold** — Einfrier-Marker pro Entität: hält jede Soft-Delete- und
Anonymisierungs-Pipeline an, solange ein laufendes Verfahren die Daten bindet
([ADR-021](adr/021-retention-modell.md)).

**k-Anon-Retention** — Spielart des Retention-Löschpfads: am Ende der Frist wird der
Client-Record **k-anonymisiert** (Setting `retention_use_k_anonymization`, Schwelle
`k_anonymity_threshold`) statt hart gelöscht — bleibt statistisch auswertbar, ist aber
nicht mehr re-identifizierbar. Seit #1094 tilgt der Retention-Bridge-Layer im
K-Anon-Zweig zusätzlich den Fall-/Episoden-/Aufgaben-Freitext (dieselben Helfer wie der
Hard-Pfad); die Primitive `k_anonymize_client` bleibt bewusst client-only. Abzugrenzen
von der **Bucket-Suppression** im externen Bericht. Details: [ADR-021](adr/021-retention-modell.md),
[ADR-023](adr/023-k-anonymization-statistik.md).

**Quasi-Identifikator / Äquivalenzklasse** — Merkmale, die einzeln nicht, in
Kombination aber identifizieren. Die Äquivalenzklasse der K-Anonymisierung ist
`(facility, age_cluster, contact_stage)` ([ADR-023](adr/023-k-anonymization-statistik.md)).

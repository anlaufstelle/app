# ADR-023: K-Anonymisierung fuer externe Statistik

- **Status:** Accepted
- **Date:** 2026-05-28
- **Deciders:** Tobias Nix
- **Refs:** #535

## Context

Anlaufstelle erzeugt unter `/statistics/external/` Berichte fuer Foerdermittelgeber, Kommunen und andere externe Stellen. Diese Berichte aggregieren Klient-Daten ueber Zeitraeume — z.B. „Unterschiedliche Personen", „Nach Dokumentationstyp", „Nach Altersgruppe". Auch nach Aggregation bleibt ein **Re-Identifikationsrisiko**, wenn ein Aequivalenzklasse-Bucket sehr klein wird:

- In einer kleinen Beratungsstelle kann „Anzahl Personen 65+ in Q2" gleich `1` sein. Ein externer Empfaenger, der die Einrichtung kennt, kann diese Person mit hoher Wahrscheinlichkeit identifizieren.
- Dokumentationstypen mit niedriger Frequenz (Spezialberatungen, seltene Themen) haben dasselbe Problem.
- Der externe Bericht enthaelt absichtlich keinen Pseudonym-Bezug — aber rohe Counts unter dem Pseudonymitaets-Schutz verraten unter Umstaenden mehr als gewollt.

DSGVO Art. 5(1)(c) (Datenminimierung) und das Verarbeitungs-Verzeichnis fuer die externe Statistik verlangen, dass „Empfaenger sehen nicht mehr, als zur Zweckerfuellung noetig". Eine Schwelle, unterhalb derer Aggregate **unterdrueckt** statt veroeffentlicht werden, ist die uebliche Antwort.

Die Schwellenwahl ist eher Policy als Architektur — die *Entscheidung fuer ein bestimmtes Verfahren und einen bestimmten Default-Wert* hat aber direkte Code-Konsequenzen (Modell-Flags, Generalisierungs-Regeln, Bucket-Counts) und gehoert deshalb in eine ADR.

## Decision

Anlaufstelle implementiert **k-Anonymisierung mit Default-Schwelle `k=5`** in [`src/core/services/compliance/k_anonymization.py`](../../src/core/services/compliance/k_anonymization.py).

- **Schwelle pro Einrichtung konfigurierbar**, Default `k=5`. Settings-Eintrag erlaubt Anpassung; eine Senkung unter `k=3` wird im UI als Risiko markiert.
- **Aequivalenzklasse fuer Klienten:** `(facility, age_cluster, contact_stage)`. `count_clients_in_bucket(facility, age_cluster, contact_stage)` liefert die Bucket-Groesse; `is_k_anonymous(client, k)` prueft die Schwelle vor Generalisierung.
- **Generalisierungs-Regeln** in `k_anonymize_client(client, k)`:
 - `pseudonym` → `anon-<sha256(pk)[:12]>` (deterministisch, nicht reversibel)
 - `notes` → `""` (Freitext leakt Identitaet)
 - `age_cluster` bleibt (bereits gebucketed)
 - `contact_stage` bleibt (low-cardinality)
 - `is_active` → `False`, `k_anonymized` → `True` (Flag fuer Wiedererkennung in spaeteren Laeufen)
- **Bericht-Aggregate (Statistik-Seite):** Buckets mit `count < k` werden als **„unterdrueckt"** ausgewiesen, **nicht** mit der echten Zahl. Das betrifft die Kennzahlen „Unterschiedliche Personen", „Nach Dokumentationstyp" und „Nach Altersgruppe" in `/statistics/external/` (siehe [`docs/user-guide.md` § Externe Berichte](../user-guide.md)).
- **Datenschutzprofil-Kopf** im Bericht zeigt Einrichtung, Profil (`external`), Zeitraum, `k`-Schwelle und Erzeugungs-Zeitpunkt — damit der Empfaenger nachvollziehen kann, unter welcher Aggregations-Politik die Zahlen entstanden sind.
- **Trennung von `Client.anonymize`:** `k_anonymize_client` ist *additiv* — sie aendert nur das Client-Record. Kaskadierende Loeschungen in Cases/Episodes/WorkItems bleiben Sache von `Client.anonymize` (Retention-Pipeline, [ADR-021](021-retention-modell.md)).

## Consequences

- **+** Re-Identifikation ueber kleine Buckets wird im externen Bericht systematisch verhindert. Empfaenger sieht „unterdrueckt" statt „1".
- **+** Schwellenwahl ist im Einrichtungs-Setting transparent dokumentiert und im Bericht-Kopf sichtbar — keine versteckte Policy.
- **+** Generalisierungs-Regeln sind deterministisch — wiederholte k-Anonymisierung desselben Records liefert denselben Bucket, was Statistik-Snapshots ueber Zeitraeume vergleichbar haelt.
- **+** Trennung zu `Client.anonymize` (Retention) macht beide Pfade einzeln testbar und vermeidet implizite Kaskaden.
- **−** `k=5` ist ein Kompromiss — kleinere Einrichtungen werden ueberproportional oft „unterdrueckt"-Eintraege sehen, was die Aussagekraft des externen Berichts schwaecht.
- **−** Das Modell schuetzt nicht gegen **Kombinations-Angriffe ueber mehrere Berichte** (Differenzbildung zwischen Quartalen). Echte Differential Privacy waere robuster, aber deutlich aufwendiger.
- **−** `notes` werden bei Generalisierung geleert — ein qualitativ wertvolles Freitextfeld geht im k-anonymisierten Bestand verloren. Akzeptiert: Freitext ist die hoechste Leak-Quelle.
- **−** Schwellenwahl muss bei jedem neuen Bericht-Aggregat mitgedacht werden; reine Code-Erweiterungen ohne Bewusstsein fuer k waeren ein Compliance-Regress.

## Alternatives considered

- **`k=3`** — feiner aufgeloest, mehr verwertbare Zahlen. Verworfen als Default: liegt unter der ueblichen Empfehlung fuer kleine Datensets im Sozialbereich; lokal aber konfigurierbar.
- **Vollstaendige Suppression jeglicher Counts < 10** — robuster, aber zerstoert die Aussagekraft des Berichts fast komplett. Verworfen.
- **Reine Pseudonym-Loeschung ohne Bucket-Suppression** — adressiert nur den direkten Klar-Identifier, nicht die Inferenz ueber Bucket-Groesse. Verworfen, weil das genau das Problem ist, das die externe Statistik aufwirft.
- **Differential Privacy mit Rauschen.** Vertagt: brauchen Library (z.B. `python-dp` / Google DP), neue Hyperparameter (Epsilon-Budget) und ein Bedrohungs-Modell-Update. Fuer die Zielgruppe (kleine Traeger, externer Bericht 1–4× pro Jahr) ist der Aufwand aktuell nicht im Verhaeltnis zum Schutzgewinn.
- **K-Anonymisierung erst beim Bericht-Rendering, nicht im Datensatz.** Wird parallel genutzt — die Berichts-Suppression rendert nur die Aggregate. Die `k_anonymize_client`-Funktion ist zusaetzlich ein Werkzeug fuer die Retention-Pipeline ([ADR-021](021-retention-modell.md)), wenn ein Klient aus der laufenden Statistik entfernt, aber im Aggregat erhalten bleiben soll.

## References

- [`src/core/services/compliance/k_anonymization.py`](../../src/core/services/compliance/k_anonymization.py) — `k_anonymize_client`, `is_k_anonymous`, `count_clients_in_bucket`
- [`src/core/migrations/0049_k_anonymization.py`](../../src/core/migrations/0049_k_anonymization.py) — `k_anonymized`-Flag
- [`docs/user-guide.md` § Datenschutzfreundliche externe Berichte](../user-guide.md)
- [ADR-013](013-dsgvo-art16-no-selfservice.md) — DSGVO Art. 16 (warum es kein Self-Service-Korrekturschluss zur Anonymisierung gibt)
- [ADR-021](021-retention-modell.md) — Retention-Modell (orchestriert die Anonymisierungs-Faelle)
- Issue #535 — k-Anonymisierung

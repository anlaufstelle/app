# Performance Budgets (Refs #825)

Diese Budgets sind Ziel- und Referenzwerte für Self-Hoster und Beitragende. Gemessen wird mit [Locust](https://locust.io/) gegen eine repräsentativ befüllte, isolierte Testumgebung; die Werte sind 95-Perzentil-Antwortzeiten in Millisekunden. Es gibt keine automatisierte CI-Durchsetzung.

| Endpoint | Aufruf | Budget (p95) | Begründung |
|---|---|---|---|
| Zeitstrom | `GET /` | **500 ms** | Hauptlandeseite — Refs #740 Sidebar-Prefetch |
| Personen-Liste | `GET /clients/` | **500 ms** | Pagination + Annotation `last_contact=Max(...)` |
| Fall-Liste | `GET /cases/` | **500 ms** | Select-related auf `client` und `lead_user` |
| WorkItem-Inbox | `GET /workitems/` | **500 ms** | Drei Listen, jeweils auf `WORKITEM_INBOX_CAP` |
| Suche | `GET /search/?q=...` | **500 ms** | seit Refs #827 auf `search_text`-GIN-Index |
| Statistik-Dashboard | `GET /statistics/?zeitraum=letzte30` | **1000 ms** | MV-Hybrid — Refs #683 |
| PDF-Export | `GET /statistics/export/pdf/...` | **5000 ms** | WeasyPrint — synchron |
| CSV-Export | `GET /statistics/export/csv/...` | **2000 ms** | Stream, aber alle Events ein Halbjahr |

## Lokale Prüfung

Die Referenzmessung verwendet das mitgelieferte `locustfile.py`, fünf gleichzeitige Nutzer:innen, eine Spawn-Rate von eins und zwei Minuten Laufzeit. Gegen eine bereits gestartete lokale Testinstanz lautet der Aufruf:

```bash
locust -f locustfile.py --host=http://localhost:8000 --headless \
  --users 5 --spawn-rate 1 --run-time 2m --csv perf
```

Die Spalte `95%` in `perf_stats.csv` wird anschließend mit der Tabelle oben oder der maschinenlesbaren Datei [`performance-budgets.json`](performance-budgets.json) verglichen. Self-Hoster können dieselbe Messung gegen ihre Staging-Umgebung fahren; Hardware, Datenmenge und Commit-SHA gehören zum Ergebnis, damit Messungen vergleichbar bleiben. Eine Überschreitung ist ein Untersuchungsanlass, kein automatisch erzwungenes Release-Gate.

## Budget-Updates

Wenn ein Endpoint dauerhaft über Budget liegt: zuerst Root-Cause klären (N+1, fehlender Index, MV-Drift). Eine Budget-Erhöhung gehört in einen separaten PR mit Begründung im Commit-Body und Update der hier verlinkten Quell-Issues.

Die maschinell auswertbare Form steht in [`performance-budgets.json`](performance-budgets.json) — dort nur diese Tabelle als Map `endpoint → ms` einpflegen.

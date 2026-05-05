# Performance Budgets (Refs [#825](https://github.com/tobiasnix/anlaufstelle/issues/825))

Schwellen für die nächtliche Last-Tests-Schicht ([Locust](https://locust.io/) auf der Seed-`large`-Datenbasis). Werte sind 95-Perzentil-Antwortzeiten in Millisekunden — überschreitet ein Endpoint sein Budget, scheitert der `perf-nightly`-Workflow.

| Endpoint | Aufruf | Budget (p95) | Begründung |
|---|---|---|---|
| Zeitstrom | `GET /` | **500 ms** | Hauptlandeseite — Refs [#740](https://github.com/tobiasnix/anlaufstelle/issues/740) Sidebar-Prefetch |
| Klientel-Liste | `GET /clients/` | **500 ms** | Pagination + Annotation `last_contact=Max(...)` |
| Fall-Liste | `GET /cases/` | **500 ms** | Select-related auf `client` und `lead_user` |
| WorkItem-Inbox | `GET /workitems/` | **500 ms** | Drei Listen, jeweils auf `WORKITEM_INBOX_CAP` |
| Suche | `GET /search/?q=...` | **500 ms** | seit Refs [#827](https://github.com/tobiasnix/anlaufstelle/issues/827) auf `search_text`-GIN-Index |
| Statistik-Dashboard | `GET /statistik/?period=month` | **1000 ms** | MV-Hybrid — Refs [#683](https://github.com/tobiasnix/anlaufstelle/issues/683) |
| PDF-Export | `GET /statistik/exports/pdf/...` | **5000 ms** | WeasyPrint — synchron |
| CSV-Export | `GET /statistik/exports/csv/...` | **2000 ms** | Stream, aber alle Events ein Halbjahr |

## Workflow

Der Nightly-Workflow (`.github/workflows/perf-nightly.yml`) startet einen seedgefüllten Container, fährt Locust headless gegen `localhost:8000` mit `--users 5 --spawn-rate 1 --run-time 2m --csv perf` und parst danach `perf_stats.csv`. Verletzte Budgets blocken den Job mit Exit-Code ≠ 0.

Bei Verletzung erzeugt das Workflow-Step `Open issue on regression` (über [peter-evans/create-issue-from-file](https://github.com/peter-evans/create-issue-from-file)) ein neues Issue mit dem Diff zum letzten erfolgreichen Lauf. Slack-Alerts sind aktuell **nicht** verdrahtet — die Issue-Öffnung deckt den Inbound-Pfad.

## Budget-Updates

Wenn ein Endpoint dauerhaft über Budget liegt: zuerst Root-Cause klären (N+1, fehlender Index, MV-Drift). Eine Budget-Erhöhung gehört in einen separaten PR mit Begründung im Commit-Body und Update der hier verlinkten Quell-Issues.

Die maschinell auswertbare Form steht in [`performance-budgets.json`](performance-budgets.json) — dort nur diese Tabelle als Map `endpoint → ms` einpflegen.

# ADR-010: Synchrone PDF-Generierung ohne Task-Queue

- **Status:** Accepted
- **Date:** 2026-04-17
- **Deciders:** Tobias Nix

## Context

Drei Endpunkte erzeugen PDFs serverseitig mit WeasyPrint (DSGVO-Datenauskunft, Halbjahresbericht, Jugendamt-Bericht). Aktuell laufen sie **synchron im Request-Thread** und blockieren den HTTP-Worker für Sekunden bis (im Extremfall) zweistellige Sekunden.

Naheliegende Lösung: Task-Queue (Celery/RQ/Dramatiq) mit Worker-Prozessen, asynchroner Statusseite und Download-Token. Damit kämen aber:

- Zusätzlicher Service (Broker: Redis/RabbitMQ) mit Backup-, Monitoring- und Failover-Anforderungen.
- Komplexere Lokalentwicklung (Worker mitstarten, Tasks debuggen).
- Async-Statusseite, Polling, Token-Lifecycle, eventueller Re-Auth bei langem Job.
- Operativer Aufwand für jede Zielinstallation (oft kleine Träger ohne Ops-Team).

Detaillierte Aufwands- und Nutzen-Analyse in [`docs/async-pdf-evaluation.md`](./async-pdf-evaluation.md).

## Decision

PDF-Generierung bleibt **synchron**. Mitigationen statt Async-Queue:

- **Rate-Limiting** auf den teuren Endpunkten (`@ratelimit(rate="10/h")` pro User).
- **Hybrid-Statistiken** (vorberechnete Aggregate für Jugendamt-Bericht), wo möglich.
- **Decryption-Caching** (`lru_cache` auf `get_fernet()`, `iter_records` statt Volladung).
- **WSGI/Gunicorn**-Tuning: ausreichend Worker, Timeouts an den realistischen Worst-Case angepasst.

Ein Wechsel auf Async-Queue ist eine spätere Option, falls Lastprofil oder Featureumfang es erzwingt. Diese ADR wird dann durch eine Folge-ADR superseded.

## Consequences

- **+** Kein zusätzlicher Service-Footprint — Installation bleibt „PostgreSQL + Webserver".
- **+** Einfacher Code-Pfad: ein Endpunkt, eine Antwort, ein Download.
- **+** Lokalentwicklung und CI bleiben einfach.
- **−** Ein PDF-Request blockiert einen Worker für die Generierungsdauer. Kapazitätsplanung muss das berücksichtigen (Worker-Anzahl ≥ erwartete parallele PDF-Requests).
- **−** Sehr große Datenauskünfte (langjährige Klientel mit hunderten Events) können nahe an HTTP-Timeouts kommen. Falls beobachtbar: erst mitigations (Pagination, Hintergrund-Mail mit Link), dann Async-Queue.

## Alternatives considered

- **Celery + Redis:** Verworfen — siehe Context. Sinnvoll bei Lastsignal, nicht prophylaktisch.
- **Django-Q / django-rq:** Gleiche Grundüberlegung wie Celery, geringerer Komplexitätszuwachs, aber immer noch zusätzlicher Service.
- **Threadpool im selben Prozess:** Verworfen — verschiebt die Blockade nur, löst sie nicht; macht Memory-Profil unvorhersehbar.

## References

- [`docs/async-pdf-evaluation.md`](./async-pdf-evaluation.md)
- [`src/core/services/client_export.py`](././src/core/services/client_export.py), [`src/core/services/export.py`](././src/core/services/export.py)

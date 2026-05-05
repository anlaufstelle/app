# ADR-001: Greenfield-Rewrite statt Prototyp-Weiterbau

- **Status:** Accepted
- **Date:** 2026-03-19
- **Deciders:** Tobias Nix

## Context

Es existierte ein lauffähiger Prototyp ([`tobiasnix/anlaufstelle-prototyp`](https://github.com/tobiasnix/anlaufstelle-prototyp)) mit Code, ~30 Issues, Seed-Daten und DSGVO-Vorlagen. Bei einer Architektur­bestandsaufnahme zeigten sich fünf strukturelle Schulden, die einen produktiven Einsatz für eine niedrigschwellige soziale Einrichtung gefährdet hätten:

1. Kein konsistentes Mandanten-/Facility-Scoping auf Datenbankebene.
2. Ad-hoc-Verschlüsselung ohne Key-Rotation und ohne klare Failure-Semantik.
3. Mischung aus FBVs/CBVs und Business-Logik in Templates und Views.
4. Kein durchgängiges Audit-Trail für sensitive Datenoperationen.
5. Test-Setup ohne tragfähige E2E-Schicht — UI-Regressionen blieben unentdeckt.

Zwei Optionen standen zur Wahl: schrittweise Sanierung des Prototyps, oder Neubau auf Basis eines vorab erstellten Umsetzungs­konzepts (v1.0).

## Decision

Greenfield-Rewrite. Das neue Repo `anlaufstelle` startet mit Django 5.1 / Python 3.13 / PostgreSQL 16 und übernimmt aus dem Prototyp ausschließlich konzeptionelle Erkenntnisse (Datenmodell, Personas, Abläufe), nicht den Code.

## Consequences

- **+** Sicherheits- und Mandantengrenzen sind von Tag 1 in Models, Middleware und Migrations verankert (ADR-005).
- **+** Service-Layer-Trennung (ADR-002) ist enforciert, nicht nachgerüstet.
- **+** Keine Migrations-Brücke aus dem Prototyp nötig — Datenmodell durfte konsolidiert werden.
- **−** Re-Implementierungs­aufwand für Funktionen, die im Prototyp bereits liefen (Seed, DSGVO-Templates, Statistik).
- **−** Prototyp-Issues mussten manuell auf Relevanz geprüft und neu eröffnet werden.

## Alternatives considered

- **Sanierung des Prototyps:** Verworfen — die fünf Schulden hängen wechselseitig zusammen (z.B. RLS verlangt sauberes Facility-Modell verlangt sauberen Service-Layer), eine Teilsanierung hätte den Aufwand nicht reduziert.
- **Wechsel des Frameworks (FastAPI/Rails/Phoenix):** Verworfen — Django liefert ORM, Admin, Auth, Forms, Templating, Migrations „aus einer Hand"; ein Frameworkwechsel hätte den Vorteil gegenüber Sanierung wieder aufgebraucht.

## References

- [docs/fachkonzept-anlaufstelle.md](../fachkonzept-anlaufstelle.md)
- Erste Commits ab 2026-03-19 (`5b35b40 feat: initialize Django project structure`)

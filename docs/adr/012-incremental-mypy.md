# ADR-012: Inkrementelles mypy mit Strict-Zone für Services

- **Status:** Accepted
- **Date:** 2026-04-26
- **Deciders:** Tobias Nix

## Context

Das Projekt war beim Einführungs­zeitpunkt ohne durchgängige Typannotationen gewachsen. Eine sofortige Strict-mypy-Aktivierung hätte hunderte Findings produziert — überwiegend in Code, der gar nicht im Fokus stand (Templates-Tags, Migrations, Test-Helpers). Ergebnis wäre, dass jede unrelated-Änderung sich an Typfehler-Backlog stoßen würde, bis die Wand abgebaut ist.

Gleichzeitig ist es **wertvoll**, Typdisziplin gerade dort zu haben, wo Bugs am teuersten sind: im Service-Layer (ADR-002), der die einzige Schreibstelle für Geschäftslogik, AuditLog und Verschlüsselung ist.

## Decision

Inkrementelle Einführung in zwei Geschwindigkeiten, konfiguriert in [`pyproject.toml`](././pyproject.toml):

- **Baseline (gesamtes Repo):** `ignore_missing_imports`, `warn_redundant_casts`, **kein** `strict_optional`, **kein** `disallow_untyped_defs`. Findet offensichtliche Fehler, ohne Wand zu produzieren.
- **Strict-Zone (`core.services.*`):** `strict_optional`, `warn_unused_ignores`. Hier muss jede neue Funktion typsauber sein.
- **Ausgenommen:** Migrations (Django-generiert), Tests (Fixtures dürfen lockerer sein).
- CI führt mypy aus; neue Strict-Zone-Verstöße brechen den Build.

Erweiterung erfolgt **modulweise** über zusätzliche `[[tool.mypy.overrides]]`-Blöcke. Wenn ein Modul typsauber ist, wird es in die Strict-Zone aufgenommen.

## Consequences

- **+** Sofortige Wirkung dort, wo Bugs teuer sind (Services), ohne den Rest zu blockieren.
- **+** CI-Signal ist verlässlich — `mypy passing` heißt, dass Strict-Zone strict ist, nicht dass nichts geprüft wird.
- **+** Onboarding-Pfad klar: „erst die berührten Module typsauber machen, dann zur Strict-Zone hinzufügen".
- **−** Repo hat zwei Standards parallel — neue Beitragende müssen wissen, welcher gilt.
- **−** Ohne Disziplin droht die Strict-Zone nicht zu wachsen. Gegenmittel: bei jedem größeren Service-Touch prüfen, ob auch andere Module promotebar sind.

## Alternatives considered

- **Strict für alles, sofort:** Verworfen — siehe Context, Wand-Effekt.
- **Kein mypy:** Verworfen — Klassen-Bug in Encryption, RLS-Manager oder Audit hätte ohne Typprüfung in Produktion gehen können; Pytest fängt nicht alles.
- **`pyright` statt `mypy`:** Funktional vergleichbar; `mypy` plus `django-stubs` ist im Django-Ökosystem besser etabliert.

## References

- [`pyproject.toml`](././pyproject.toml) (`[tool.mypy]` und `[[tool.mypy.overrides]]`)
- Audit-Maßnahme #37 /

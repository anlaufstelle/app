# ADR-003: HTMX + Alpine.js + Tailwind statt SPA

- **Status:** Accepted
- **Date:** 2026-03-19
- **Deciders:** Tobias Nix

## Context

Die Zielgruppe (niedrigschwellige soziale Einrichtungen) arbeitet auf heterogener Hardware, häufig mit eingeschränkter IT-Unterstützung. Eine SPA-Architektur (React/Vue) hätte bedeutet:

- Build-Pipeline mit Node-Toolchain in Produktion und CI.
- Separates State-Management (Auth, CSRF, Permissions) auf Client-Seite, das mit dem Django-Backend synchron bleiben muss.
- Größeres JS-Bundle, längere Time-to-Interactive auf älteren Browsern.
- Höhere Einstiegshürde für künftige Mitwirkende — sowohl Frontend- als auch Backend-Stack.

Gleichzeitig braucht das UI durchaus Interaktivität (Inline-Editing, Filter, Modal-Dialoge, Live-Suche). Reines Server-Side-Rendering ohne Aufwertung wäre zu rau.

## Decision

- **HTMX** für serverseitige Partials und Interaktionen — Backend rendert HTML-Fragmente, kein JSON-API-Layer für die UI.
- **Alpine.js** für lokal begrenzte Client-State (Dropdowns, Toggles, Form-Bedingungen).
- **Tailwind CSS** für Styling, Tailwind-Widget-Klassen direkt in den Form-Definitionen.
- HTMX-Partials liegen unter `src/templates/core/<feature>/partials/`. Views unterscheiden Voll- vs. Partial-Response über `request.headers.get("HX-Request")`.

## Consequences

- **+** Ein Stack (Python/Django) für 95 % der Logik — Auth, Permissions, Validation laufen ungeteilt im Backend.
- **+** Kein separater API-Layer für das UI; Endpunkte für externe Konsumenten (z.B. Statistik-Export) bleiben dadurch klar abgegrenzt.
- **+** Kleines JS-Bundle, funktioniert auf älteren Browsern und mit eingeschränktem JS.
- **−** Komplexe Client-State-Workflows (Drag-and-Drop über mehrere Listen, Real-Time-Collaboration) wären mit HTMX/Alpine umständlich.
- **−** Tailwind-Klassen im HTML können Templates lang machen; Komponenten-Abstraktion erfolgt über Django-Template-Includes statt JS-Komponenten.

## Alternatives considered

- **React/Vue SPA:** Verworfen — siehe Context.
- **Django ohne JS-Aufwertung:** Verworfen — Inline-Editing und HTMX-Partials sind für UX kritisch.
- **Stimulus + Turbo:** Gleichwertige Option, aber das Django-/HTMX-Ökosystem ist im Python-Raum besser belegt (django-htmx, django-template-partials).

## References

- [`src/templates/`](././src/templates), [`pyproject.toml`](././pyproject.toml) (django-htmx, django-template-partials)

# ADR-017: Deployment-Topologie — Plain Docker Compose primär, Multi-Stage als parallele Stacks

- **Status:** Accepted
- **Date:** 2026-05-08
- **Deciders:** Tobias Nix

## Context

Das Projekt steht vor seiner ersten oeffentlichen Live-Schaltung (`dev.anlaufstelle.app`) und braucht in den naechsten Iterationen ausserdem `stage.anlaufstelle.app`, perspektivisch Feature-Branch-Subdomains, sowie eine Live-Demo-Instanz unter `anlaufstelle.app` auf einer eigenen Maschine. Damit stand zur Wahl, ob die Live-Pipeline weiterhin auf reinem `docker compose up` aufsetzt oder ob ein PaaS-Layer (Coolify, Dokploy) die Multi-Stage-Welt orchestriert.

Die Frage war nicht trivial:

- Eine Recherche-Empfehlung aus Issue #320 (2026-03-23, geschlossen) hatte **Hetzner + Coolify Cloud** vorgeschlagen — wegen PR-Previews, Web-UI fuer Env-Vars und minimalem DevOps-Aufwand. Issue #554 sollte das umsetzen, wurde aber nie durchgezogen.
- Das ADR-Backlog hat „Deployment-Target" explizit als offen gefuehrt, abhaengig von „Erfahrungen im ersten Pilot-Deployment". Das Pilot ist nie gelaufen, die ADR nie geschrieben.
- Das **Fachkonzept** ist an mehreren Stellen klar gegenlaeufig:
  - §163: „Anlaufstelle muss mit `docker compose up` installierbar sein."
  - §247 (Persona Jonas): Trager-IT, kann Docker, kein Coolify.
  - §928: „Keine SaaS-Plattform … Cloud-Lock-in ist ein Anti-Pattern."
  - §1009: „Docker Compose als primaerer Deployment-Pfad. Das Versprechen `docker compose up` ist eine **harte Anforderung**."
- Ein PaaS-Layer haette unser internes Live-Setup vom Self-Host-Pfad **entkoppelt**, den wir Tragern empfehlen — wir wuerden nicht mehr leben, was wir verkaufen.

Gleichzeitig ist die Multi-Stage-Welt nicht hypothetisch, sondern in unmittelbarer Sicht. Eine Topologie, die heute fuer `dev` funktioniert und morgen fuer `stage`/Feature-Branches umgebaut werden muesste, waere falsch.

## Decision

**Plain Docker Compose ist und bleibt der primaere Deployment-Pfad.** Multi-Stage-Skalierung wird durch ein einfaches, file-basiertes Pattern erreicht, das Plain-Compose-Eigenschaften erhaelt:

1. **Pro Stage ein eigener Compose-Stack** — `docker-compose.dev.yml`, spaeter `docker-compose.stage.yml`, ggf. `docker-compose.feature-<name>.yml`. Container-Namen pro Stage isoliert (Compose macht das via Project-Name automatisch), eigene Volumes (`pgdata_<stage>`, `media_<stage>`).
2. **Solange nur eine Stage existiert (heute):** Caddy laeuft als Service im jeweiligen Compose-Stack, eigenes `Caddyfile.<stage>` daneben — minimal, ohne Reverse-Proxy-Plumbing.
3. **Sobald >1 Stage gleichzeitig auf derselben Maschine laeuft:** Caddy wird in einen eigenen `docker-compose.proxy.yml`-Stack ausgegliedert, mit zentralem `Caddyfile` und `import sites.d/*.caddy`. Jeder Stage-Stack legt sein File in `sites.d/` ab. Reverse-Proxy zielt ueber das gemeinsame Docker-Netzwerk auf den jeweiligen `web`-Container der Stage. Diese Migration ist Bestandteil des `stage`-Plans, **nicht** dieses ADRs.
4. **Live-Demo (`anlaufstelle.app`)** laeuft als isolierter Single-Stack auf einer **eigenen Maschine**. Architektur identisch zum Self-Host-Setup, das wir Tragern empfehlen — Demo dient als Live-Referenz fuer das Versprechen aus §1009.
5. **Coolify** bleibt als **alternativer**, nicht-primaerer Pfad in [`docs/coolify-deployment.md`](./coolify-deployment.md) dokumentiert: fuer Trager, die Coolify bereits einsetzen, oder fuer kuenftige Bedarfsfaelle, die Plain Compose nachweislich nicht abdeckt. Der Pfad waechst und schrumpft mit nachgewiesenem Nutzen, nicht mit Bequemlichkeit.

## Consequences

**Positiv:**

- Selbsthosting-Versprechen aus §1009 bleibt einloesbar — wir und Trager wie Persona Jonas nutzen denselben Pfad.
- Live-Demo ist gleichzeitig die operative Self-Host-Referenz; Bugs am Self-Host-Pfad sehen wir vor Tragern.
- Keine zusaetzliche Plattform-Lernkurve, kein zusaetzlicher RAM-Footprint (~0,5–1 GB, den Coolify auf einer CX22 belegt haette).
- ADR-Konsistenz mit ADR-009 (Settings-Vererbung): `devlive.py` erbt von `prod.py`, exakt derselbe Pattern, den Trager einsetzen.
- Multi-Stage-Pfad ist klein, transparent und in einem Standard-Linux-Ops-Skill-Set bedienbar.

**Negativ:**

- Eine neue Stage/Feature-Branch-Subdomain ist manuelle Caddy-File-Drop-Arbeit; keine Auto-PR-Preview wie bei Coolify.
- Keine Web-UI fuer Operator-Tasks (Logs, Env-Vars, Restart) — alles laeuft ueber SSH und Make-Targets.
- Bei >5 parallelen Feature-Branch-Subdomains wird das manuelle Pattern zaeh; dann waere ein Folge-ADR mit Re-Evaluation faellig.
- Wir verzichten bewusst auf Komfort-Features, um die Architektur-Konsistenz mit dem Selbsthosting-Versprechen zu wahren.

## Alternatives considered

- **Coolify Cloud (Empfehlung aus #320):** Verworfen — `docker compose up`-Versprechen aus §1009 wuerde nicht mehr im internen Live-Setup gelebt; Cloud-Lock-in entgegen §928.
- **Coolify Self-Hosted (Plan in #554):** Selbe Beisser zu §1009 + §163 (Persona-Lernkurve), zusaetzlich Coolify-RAM-Footprint und eigene Update/Patch-Verantwortung fuer den Coolify-Layer.
- **Plain Compose + Traefik** (Reverse-Proxy mit Auto-Discovery via Container-Labels): Caddy-Replacement waere ein paralleler Konzept-Wechsel ohne Mehrwert gegenueber Caddy-mit-`import sites.d/*.caddy`; nicht im Stack-Vokabular der bestehenden Doku ([`Caddyfile`](././Caddyfile), [`Caddyfile.staging`](././Caddyfile.staging), [`docs/ops-runbook.md`](./ops-runbook.md)).
- **Dokploy:** Junges Projekt (2023+), kleine Community, Compose-first — aber gleiche prinzipielle Beisser wie Coolify gegenueber dem Selbsthosting-Versprechen.
- **Dokku:** Single-Container-orientiert; Anlaufstelle ist Multi-Service (web + db + clamav + caddy) — schlechter Fit.

## References

- Fachkonzept: [`docs/fachkonzept-anlaufstelle.md`](./fachkonzept-anlaufstelle.md) §163, §247, §844, §928, §1009
- Issues: (Recherche) (Coolify-Pivot, geschlossen mit diesem ADR) (Plain-Compose-Pivot) (Plan-Issue dieses ADRs)
- Begleitende Doku: [`docs/dev-deployment.md`](./dev-deployment.md), [`docs/coolify-deployment.md`](./coolify-deployment.md) (alternativer Pfad), [`docs/ops-runbook.md`](./ops-runbook.md)
- Verwandte ADRs: [ADR-009](009-settings-inheritance.md) (Settings-Vererbung), [ADR-011](011-three-repo-release-pipeline.md) (3-Repo-Pipeline)

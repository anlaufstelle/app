# Governance

Diese Datei beschreibt, **wie** in Anlaufstelle Entscheidungen getroffen werden,
wer sie trifft, wie eskaliert wird und unter welchen Bedingungen weitere Maintainer
aufgenommen werden. Wer aktuell verantwortlich ist, steht in
[MAINTAINERS.md](MAINTAINERS.md); der Beitragsprozess in
[CONTRIBUTING.md](CONTRIBUTING.md).

## Entscheidungsmodell

Anlaufstelle wird derzeit von einer einzelnen Person als **Solo-Maintainer**
geführt (BDFL-Modell: „Benevolent Dictator For Life"). Der Lead-Maintainer
(siehe [MAINTAINERS.md](MAINTAINERS.md)) trifft die letztverbindliche
Entscheidung über Roadmap, Architektur, Releases und Aufnahme von Beiträgen.

Das ist eine ehrliche Beschreibung des Ist-Zustands, keine Zielarchitektur: mit
weiteren Maintainern (siehe unten) verschiebt sich das Modell in Richtung
Konsens- bzw. Lazy-Consensus-Entscheidungen; diese Datei wird dann fortgeschrieben.

**Leitplanken auch im Solo-Betrieb:**

- Entscheidungen orientieren sich am fachlichen Fundament (niedrigschwellige
 Soziale Arbeit) und am Datenschutz-/Sicherheitsanspruch des Projekts
 (Art. 9 DSGVO, siehe [SECURITY.md](SECURITY.md)).
- Wesentliche, schwer reversible oder querschnittliche Entscheidungen werden als
 **Architecture Decision Record (ADR)** festgehalten und begründet.

## ADR-Prozess

Architektur- und Plattformentscheidungen werden als ADR unter
[`docs/adr/`](docs/adr/) dokumentiert. Format, Kriterien („wann eine neue ADR")
und Nummerierung sind in [docs/adr/README.md](docs/adr/README.md) beschrieben
(schlankes MADR-Light, fortlaufend nummeriert, überholte ADRs werden auf
`Superseded` gesetzt statt gelöscht). Die nächste freie Nummer ergibt sich
fortlaufend aus dem Index in [docs/adr/README.md](docs/adr/README.md) — dort steht
der aktuelle Stand; eine hier fest eingetragene Nummer würde nur veralten.

Eine ADR ist der reguläre Weg, eine getroffene Grundsatzentscheidung
nachvollziehbar zu machen — auch im Solo-Betrieb, gerade damit spätere Maintainer
das **Warum** rekonstruieren können.

## Review & Self-Review

Der reguläre Weg für Änderungen ist der Pull-Request-Prozess aus
[CONTRIBUTING.md](CONTRIBUTING.md). Die Zuordnung von Pfaden zu Reviewern steht in
[.github/CODEOWNERS](.github/CODEOWNERS).

Bei aktuell **einer** aktiven Person ist der Code-Owner überall dieselbe Person.
**Self-Review (der Maintainer prüft und merged eigene Änderungen) ist damit die
dokumentierte Realität**, kein verstecktes Anti-Pattern. Kompensiert wird das,
soweit möglich, durch automatisierte Kontrollen statt durch ein zweites
Augenpaar: verpflichtende CI-Gates (`make ci`), Test-First-Pflicht (TDD),
Lint/Typecheck sowie die Sicherheits- und Drift-Guards des Repos. Mit einer
zweiten aktiven Person wird auf **Vier-Augen-Review für sicherheitskritische
Pfade** umgestellt.

## Eskalationspfad

1. **Fachliche/technische Diskussion:** öffentlich am jeweiligen
 [GitHub-Issue](https://github.com/anlaufstelle/app/issues) oder Pull Request.
2. **Uneinigkeit:** Der Lead-Maintainer entscheidet und begründet — bei
 Grundsatzfragen per ADR.
3. **Sicherheitslücken:** **nicht** öffentlich, sondern ausschließlich über die
 vertraulichen Kanäle in [SECURITY.md](SECURITY.md).
4. **Betriebs-/Vorfall-Eskalation** (inkl. DSGVO Art. 33/34 und der CRA-24/72-h-
 Fristen): folgt dem manuellen Prozess in
 [docs/ops-runbook.md § 6.5b](docs/ops-runbook.md) und setzt eine
 handlungsfähige **Vertretung** voraus, solange die Hauptperson ausfällt — der
 dafür noch offene Punkt ist in
 [MAINTAINERS.md § Vertretung](MAINTAINERS.md#vertretung-tbd--vor-2026-09-11-vom-maintainer-zu-benennen)
 ausgewiesen.

## Aufnahme weiterer Maintainer

Das Projekt ist ausdrücklich offen dafür, den Bus-Faktor zu erhöhen. Ein neuer
Maintainer wird vom Lead-Maintainer benannt; als Orientierung gelten:

- **Nachgewiesene Beiträge:** mehrere substanzielle, gemergte Beiträge über einen
 längeren Zeitraum, die Verständnis für Architektur, Datenschutz und Testkultur
 zeigen.
- **Übereinstimmung mit den Projektwerten:** [Code of Conduct](CODE_OF_CONDUCT.md),
 Datenschutz-/Sicherheitsanspruch, Inbound=Outbound-Lizenzierung (AGPL-3.0) und
 **DCO-Sign-off** wie in [CONTRIBUTING.md § Lizenz von Beiträgen](CONTRIBUTING.md#lizenz-von-beiträgen).
- **Verlässlichkeit:** Bereitschaft, Review-, Sicherheits- und ggf.
 Vertretungsaufgaben (siehe [MAINTAINERS.md](MAINTAINERS.md)) verbindlich zu
 übernehmen.

Es gibt hierfür bewusst **keinen Zeitplan und keine Zusage** — Aufnahme erfolgt
anlassbezogen. Ein aufgenommener Maintainer wird in [MAINTAINERS.md](MAINTAINERS.md)
und ggf. in [.github/CODEOWNERS](.github/CODEOWNERS) eingetragen; ausscheidende
Maintainer werden dort ebenfalls nachgeführt.

## Verweise

- [MAINTAINERS.md](MAINTAINERS.md) — wer aktuell verantwortlich ist, Bus-Faktor,
 offene Vertretungsfrage.
- [CONTRIBUTING.md](CONTRIBUTING.md) — Beitragsprozess, DCO, Lizenz.
- [SECURITY.md](SECURITY.md) — Sicherheitsmeldungen und SLA.
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) — Verhaltensregeln.
- [docs/adr/README.md](docs/adr/README.md) — ADR-Prozess und Index.

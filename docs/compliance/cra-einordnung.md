# CRA-Einordnung — Cyberresilienz-Verordnung (EU) 2024/2847

**Stand:** 2026-06-28 · **Quelle:** [Issue #1077](https://github.com/anlaufstelle/app/issues/1077) · **Ausgangsbefund:** [Repository-Ausblick #1074](https://github.com/anlaufstelle/app/issues/1074) (Horizont 1) · **Pflege:** Maintainer

> **Kein Rechtsrat.** Dieses Memo dokumentiert die interne Einschätzung des Projekts zur CRA. Es stellt — analog zum [README-Haftungsausschluss](../../README.md#haftungsausschluss) und zur [LICENSE](../../LICENSE) (§15–16) — **keine Rechtsberatung** dar. Maßgeblich sind der Verordnungstext, etwaige Durchführungsrechtsakte und die Leitlinien/FAQ der EU-Kommission und ENISA; bei einer Scope-Bejahung oder einem Betriebsmodell-Wechsel ist fachanwaltliche Prüfung angezeigt.

Die Cyberresilienz-Verordnung (Cyber Resilience Act, Verordnung (EU) 2024/2847) ist am **10.12.2024** in Kraft getreten und wird gestaffelt anwendbar:

- **11.09.2026** — Meldepflichten nach **Art. 14**: aktiv ausgenutzte Schwachstellen und schwerwiegende Sicherheitsvorfälle sind binnen **24 h** (Frühwarnung) bzw. **72 h** (Meldung) an das zuständige CSIRT bzw. ENISA über die zentrale Meldeplattform zu melden.
- **11.12.2027** — Hauptpflichten: grundlegende Anforderungen (**Anhang I**), Konformitätsbewertung, CE-Kennzeichnung, definierter Support-Zeitraum.

---

## 1. Scope-Einschätzung

### 1.1 Anwendungsbereich und FOSS-Ausnahme

Die CRA gilt für „Produkte mit digitalen Elementen", die **im Rahmen einer Geschäftstätigkeit auf dem Markt bereitgestellt** werden. Freie und quelloffene Software, die **außerhalb einer Geschäftstätigkeit** entwickelt oder bereitgestellt wird, ist ausgenommen (Erwägungsgründe **18–19**). Die Erwägungsgründe stellen ausdrücklich klar, dass das bloße Anbieten **kostenpflichtiger technischer Unterstützung rund um eine ansonsten kostenlose FOSS** für sich genommen **keine** Geschäftstätigkeit im Sinne der Verordnung begründet — entscheidend ist, ob das **Produkt selbst monetarisiert** wird (Preis für die Software, Bereitstellung als kostenpflichtiger Dienst/SaaS, oder Monetarisierung z. B. durch zweckfremde Verwertung personenbezogener Daten).

Daneben kennt die CRA das Light-Regime des **„Open-Source-Software-Verwalters" (Open-Source Steward, Art. 24)**: eine juristische Person, die die Entwicklung von für eine Geschäftstätigkeit bestimmter FOSS systematisch unterstützt. Verwalter trifft ein **reduzierter Pflichtenkatalog** (Cybersecurity-Policy, Zusammenarbeit mit den Marktaufsichtsbehörden, anteilige Meldepflichten nach Art. 14 für aktiv ausgenutzte Schwachstellen) — **nicht** die vollen Hersteller-Pflichten (Konformitätsbewertung, CE).

### 1.2 Befund für Anlaufstelle

| Indikator | Beobachtung | Quelle |
|---|---|---|
| Lizenz | AGPL-3.0, Quelloffen | [LICENSE](../../LICENSE), [README §Lizenz](../../README.md#lizenz) |
| Preis der Software | kostenlos nutzbar, kein Produktpreis | [README](../../README.md) |
| Kostenpflichtige Nebenleistungen | Beratung, Schulung, Anpassung/Einführungsbegleitung — Dienstleistungen **um** die Software herum | [README §Unterstützung bei der Einführung](../../README.md#unterstützung-bei-der-einführung) |
| Bereitstellung als Dienst (Hosting/SaaS) | derzeit **nein**; Demo-Instanz `demo.anlaufstelle.app` ist kostenlos, zeitlich begrenzt (stündlicher Reset), kein kommerzielles Angebot | #971, [ADR-028](../adr/028-demo-release-versioning.md) |

Der Graubereich ist die Abgrenzung **Dienstleistungen um die Software herum** (Beratung/Schulung/Konfiguration) gegenüber einer **Monetarisierung des Produkts**. Nach den Erwägungsgründen 18–19 fallen die heutigen kostenpflichtigen Nebenleistungen in die erste Kategorie und lösen die Geschäftstätigkeit **nicht** aus, solange die Software selbst kostenlos und nicht als kostenpflichtiger Dienst bereitgestellt wird.

### 1.3 Szenario-Betrachtung

| Szenario | Betriebsmodell | CRA-Folge |
|---|---|---|
| **Heute** | AGPL-FOSS, kostenlos; kostenpflichtige Beratung/Schulung/Anpassung; kostenlose, zeitlich begrenzte Demo | **außerhalb des Scope** (FOSS außerhalb Geschäftstätigkeit), allenfalls **Verwalter-Light-Regime (Art. 24)** mit anteiligen Meldepflichten |
| **Künftig denkbar** | kostenpflichtiges **Hosting/SaaS-Angebot**, Produktverkauf oder kommerzialisierte Demo | **volle Hersteller-Pflichten** (Anhang I, Konformitätsbewertung, CE, definierter Support-Zeitraum) **ab 11.12.2027** |

### 1.4 Wahrscheinliches Ergebnis

**„Außerhalb des Scope bzw. geringe Pflichten."** Begründung:

1. Die Software ist quelloffen (AGPL) und wird **kostenlos** bereitgestellt — kein Produktpreis, kein kostenpflichtiger Dienst.
2. Die beworbenen **kostenpflichtigen Nebenleistungen** (Beratung, Schulung, Anpassung) sind Dienstleistungen **um die Software herum**; die Erwägungsgründe 18–19 stellen klar, dass dies allein keine Geschäftstätigkeit im Sinne der CRA begründet.
3. Es besteht **kein Hosting-/SaaS-Angebot**; die Demo-Instanz (#971) ist kostenlos und zeitlich begrenzt und damit kein „Bereitstellen auf dem Markt im Rahmen einer Geschäftstätigkeit".

Greift dennoch das **Verwalter-Regime (Art. 24)**, beschränken sich die Pflichten im Wesentlichen auf eine Cybersecurity-Policy, die Behörden-Zusammenarbeit und die anteilige Meldung aktiv ausgenutzter Schwachstellen (Art. 14) — die Skizze in §3 deckt diesen Fall mit ab.

> Die finalen **Leitlinien/FAQ der EU-Kommission zu FOSS** sind einzuarbeiten, sobald sie vorliegen; sie können die Abgrenzung in §1.1 schärfen.

---

## 2. Gap-Check gegen Anhang I (Grundanforderungen)

Anhang I gliedert sich in **Teil I** (Sicherheitseigenschaften des Produkts) und **Teil II** (Umgang mit Schwachstellen). Der folgende Abgleich greift den Befund aus #1077 auf und leitet konkrete Folge-Maßnahmen ab.

| Anhang-I-Bezug | Baustein | Stand | Folge-Maßnahme (Issue-Vorschlag) |
|---|---|---|---|
| Teil I (sichere Voreinstellungen) | **Secure by Default** — Threat-Model, Settings-Guards, kein Default-Passwort (`create_super_admin` interaktiv) | ✅ vorhanden | — (dokumentiert in [threat-model.md](../threat-model.md), [security-notes.md](../security-notes.md)) |
| Teil II Nr. 1 (SBOM) | **SBOM** — CycloneDX via `pip-audit` | ⚠️ teilweise — nur CI-Artefakt (90 Tage), **nicht** im Release veröffentlicht | **SBOM als Release-Artefakt**: `sbom.json` (CycloneDX) an das GitHub-Release anhängen ([`release.yml`](../../.github/workflows/release.yml)) |
| Teil II Nr. 2 (Schwachstellen unverzüglich beheben) | **Security-Update-Prozess** — Dependabot (pip/npm/Actions), `pip-audit`, CodeQL, zügige Patch-Releases | ✅ vorhanden | — ([`dependabot.yml`](../../.github/dependabot.yml), [`test.yml`](../../.github/workflows/test.yml), [`codeql.yml`](../../.github/workflows/codeql.yml)) |
| Teil II Nr. 3 (regelmäßige Tests) | **Test-/Review-Pipeline** — CI, CodeQL, `pip-audit`, Mutation-/Perf-Nightly | ✅ vorhanden ||
| Teil II Nr. 4 (Sicherheitsupdates verbreiten, Info über behobene Lücken) | **Advisory-Veröffentlichung** — CHANGELOG/Hall-of-Fame; GitHub Security Advisories (GHSA) zur Veröffentlichung **ungenutzt** | ⚠️ teilweise | **GHSA aktivieren + interner Advisory-Prozess**: GHSA-Entwurf je Fix, CVE-Anforderung, dokumentierter Ablauf „wer meldet was wohin" |
| Teil II Nr. 5 (CVD-Policy) | **Coordinated Vulnerability Disclosure** | ✅ vorhanden | — ([SECURITY.md](../../SECURITY.md)) |
| Teil II Nr. 6 (Meldekontakt) | **Kontaktadresse** für Schwachstellenmeldungen | ✅ vorhanden | — ([SECURITY.md](../../SECURITY.md)) |
| Art. 13(8) (Support-Zeitraum) | **Definierter Support-Zeitraum pro Version** — SECURITY.md führt eine Versionstabelle, aber **keine** End-of-Support-Fristen | ❌ fehlt | **Support-Zeitraum-Policy**: pro Minor-Version Support-/EOL-Daten in [SECURITY.md](../../SECURITY.md) (post-1.0: aktuelle + vorherige Minor) |

**Abgeleitete Folge-Issues (mindestens):**

1. **Support-Zeitraum-Policy** — definierte Support-/EOL-Fristen je Minor-Version in `SECURITY.md`.
2. **SBOM als Release-Artefakt** — CycloneDX-SBOM an jedes GitHub-Release anhängen.
3. **GitHub Security Advisories aktivieren** — internen Melde-/Advisory-Prozess dokumentieren (GHSA-Entwurf je Fix, Verantwortlicher, Veröffentlichungs-Schonfrist analog SECURITY.md-SLA).

---

## 3. Meldeprozess-Skizze (für den Fall der Scope-Bejahung)

> **Vorhalt.** Diese Skizze wird erst scharf geschaltet, wenn §1 die Anwendbarkeit (voll oder als Verwalter, Art. 24) bejaht. Sie ergänzt die bestehende CVD/SLA in [SECURITY.md](../../SECURITY.md); der reguläre Triage-Takt (Eingangsbestätigung ≤ 5 Werktage etc.) bleibt, die behördlichen 24/72-h-Fristen treten **zusätzlich** hinzu.

**Auslöser:** eine **aktiv ausgenutzte Schwachstelle** in der Software **oder** ein **schwerwiegender Sicherheitsvorfall** mit Auswirkung auf deren Sicherheit (Art. 14).

**Fristen (ab Kenntnis):**

| Frist | Schritt | Inhalt |
|---|---|---|
| **24 h** | **Frühwarnung** (early warning) | erste Meldung ohne unangemessene Verzögerung, dass eine aktiv ausgenutzte Schwachstelle / ein schwerwiegender Vorfall vorliegt |
| **72 h** | **Meldung** (vulnerability/incident notification) | Details, Schweregrad, betroffene Komponenten, bereits ergriffene bzw. geplante Korrektur-/Minderungsmaßnahmen |
| **14 Tage / 1 Monat** | **Abschlussbericht** (final report) | bei Schwachstellen: sobald eine Korrektur-/Minderungsmaßnahme verfügbar ist (spätestens 14 Tage danach); bei schwerwiegenden Vorfällen: binnen eines Monats nach der Meldung |

**Meldeweg:** über die **zentrale Meldeplattform der ENISA** (Single Reporting Platform, Art. 16) an das als Koordinator benannte **CSIRT** (in Deutschland voraussichtlich das BSI/CERT-Bund) und an ENISA. Der genaue Plattform-Zugang und die nationale CSIRT-Benennung sind anhand der finalen Durchführungsrechtsakte/Leitlinien zu bestätigen.

**Verantwortlicher:** der Maintainer (derzeit Solo-Maintainer, vgl. [SECURITY.md §SLA](../../SECURITY.md)), Kontakt [kontakt@anlaufstelle.app](mailto:kontakt@anlaufstelle.app). **Vor dem 11.09.2026 ist eine Vertretung zu benennen**, damit die 24-h-Frist auch bei Ausfall der Hauptperson eingehalten werden kann. Die operative Einbettung (Eskalation, Logging des Meldevorgangs) gehört ins [ops-runbook.md](../ops-runbook.md).

---

## 4. Wiedervorlage-Trigger

| Termin / Anlass | Aktion |
|---|---|
| **11.09.2026** — Meldepflichten (Art. 14) anwendbar | Meldeprozess (§3) operativ scharf schalten; Verantwortlichen **und Vertretung** final benennen; CSIRT-Kontakt/Plattform-Zugang klären |
| **11.12.2027** — Hauptpflichten (Anhang I) anwendbar | Scope erneut prüfen; bei Scope-Bejahung Konformitätsbewertung, CE, definierter Support-Zeitraum und SBOM-/Advisory-Lücken (§2) schließen |
| **Betriebsmodell-Änderung** (jederzeit) | Neubewertung bei kostenpflichtigem **Hosting-/SaaS-Angebot**, Produktverkauf oder Kommerzialisierung der Demo-Instanz (#971) — dann volle Hersteller-Pflichten ab 11.12.2027 denkbar |
| **Kommissions-/ENISA-Guidance final** | FOSS-Leitlinien/FAQ in §1 einarbeiten; ggf. Einschätzung extern validieren (-/NGI-Kontext, Fachanwalt bei Bedarf) |

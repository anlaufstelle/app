# ADR-043: Suchraum der Volltextsuche — keine Such-Protokollierung, offener Fall-Rahmen

- **Status:** Accepted
- **Date:** 2026-08-22
- **Deciders:** Tobias Nix
- **Refs:** #1862 (RFC mit den Belegen am Quelltext), #1842 (Fall-Titel + Ereignis-Inhalte, offen), #1803 (Suchseite als Arbeitsplatz, Regeln L4/L6/L7), #918 (Klartext-Freitexte), #1751 (PII aus dem AuditLog entfernt), #1944 (`detail`-Allowlist), [ADR-016](016-search-postgres-only.md) (Suchbackend), [ADR-007](007-auditlog-append-only.md) (Append-Only), [ADR-019](019-custom-admin-site-sudo.md) (Admin-Gate), [ADR-026](026-rate-limiting.md) (Tarife)

## Context

[ADR-016](016-search-postgres-only.md) legt das **Backend** der Suche fest (PostgreSQL + `pg_trgm`) und die Invariante, dass der Sensitivity-Filter im Schreibpfad sitzt. Offen blieben zwei Fragen, die sich erst im Betrieb stellen und die #1862 am Code aufgearbeitet hat: **Wird die Suche protokolliert?** Und: **Gilt für den Fall-Rahmen dieselbe Sichtbarkeitsstufung wie für Ereignisse?**

### Was heute wo gesucht werden kann

Die Frage „was durchsucht die Suche" hat drei getrennte Antworten, die in der Diskussion regelmäßig zu einer verschmelzen:

| Fläche | Suchraum | Zugang |
|---|---|---|
| Globale Suche `/search/` ([`services/dashboard/search.py`](../../src/core/services/dashboard/search.py), [`views/search.py`](../../src/core/views/search.py)) | `Client.pseudonym` (Teilstring + Trigramm) und `Event` über `client__pseudonym` **oder** `Event.search_text` | Assistenz und höher, 30 Anfragen/Minute je Nutzer, ab zwei Zeichen |
| Listen-Filter `?q=` ([`mixins.FilteredPaginatedListMixin`](../../src/core/views/mixins.py)) | `/clients/` → `pseudonym` ([`views/clients.py`](../../src/core/views/clients.py) `search_fields`); `/cases/` → **`title`** ([`views/cases.py`](../../src/core/views/cases.py) `search_fields`) | Assistenz und höher bzw. Fachkraft und höher, kein eigenes Ratelimit, keine Mindestlänge |
| Django-Admin `/admin-mgmt/` | **elf** `search_fields`-Deklarationen, darunter `Case` → `title` + `client__pseudonym` ([`admin/clients.py`](../../src/core/admin/clients.py)), `WorkItem` → `title` ([`admin/workflow.py`](../../src/core/admin/workflow.py)), `Event` → `client__pseudonym`, `Attachment` → `storage_filename` (der UUID-Speichername, nicht der Klarname), `AuditLog` → u. a. `detail` | nur `super_admin`/`facility_admin`, **nur mit aktivem Sudo-Mode** ([ADR-019](019-custom-admin-site-sudo.md)); `facility_admin` auf die eigene Einrichtung gescopt. `SYSTEM_VIEW` schreibt ein `super_admin`-Read **nur dort, wo der [`AdminReadAuditMixin`](../../src/core/admin/mixins.py) hängt** — an `Client`, `Case`, `Event`, `EventHistory`, `WorkItem` und `Attachment`, also an **fünf** der elf Suchflächen. Die **sechs übrigen** bleiben unauditiert: ausgerechnet [`AuditLogAdmin`](../../src/core/admin/system.py) mit seiner `detail`-Suche, dazu `DocumentType`, `FieldTemplate`, `QuickTemplate`, `Organization` und `Facility`. Ebenso mixinlos lesen `Settings`, `StatisticsSnapshot`, `User`, `DeletionRequest` und `TimeFilter` (ohne Suchfeld). „Jeder `super_admin`-Read wird protokolliert" ist also **falsch** — die Aussage gilt nur für die sechs Domänen-Admins |

**Daraus folgt eine Formulierungsregel für alle Folgetickets:** Sätze wie „Fall-Titel sind nicht suchbar" oder „Aufgaben sind über keine Fläche auffindbar" sind falsch. Richtig ist: *In der globalen Suche* gibt es weder Fall-Titel noch Aufgaben-Titel; in der Fall-Liste gibt es einen Titel-Filter, und im Admin gibt es beide — sudo-gated, rollen-gegated und bei facility-übergreifendem Lesen auditiert.

### Was strukturell nie in die Suche kommt

- **Verschlüsselte Feldwerte und ELEVATED-/HIGH-Felder.** `compute_event_search_text` überspringt sie beim Schreiben ([`services/events/fields.py`](../../src/core/services/events/fields.py), `is_encrypted` bzw. `sensitivity in elevated`). Der Lesepfad deckelt zusätzlich über `document_type__sensitivity__in=allowed_sensitivities_for_user(user)`.
- **Klartext-Dateinamen.** `Attachment.original_filename_encrypted` ist Chiffrat ([`models/attachment.py`](../../src/core/models/attachment.py), Refs #622) — nicht durchsuchbar, weil nicht durchsuchbar *machbar*.
- **Personen im Papierkorb**, in beiden Zweigen und auch im Fuzzy-Zweig (Refs #1803).

Die Ergebnisseite sagt das in Nutzersprache und **konstant** — der Block „Was die Suche nicht findet" ist statischer Template-Text, damit seine bloße Anwesenheit kein Existenz-Orakel wird ([`search/partials/results.html`](../../src/templates/core/search/partials/results.html), Regel L4).

### Der rechtliche Rahmen

Die DSGVO verbietet keine Suche im eigenen Bestand: Sie ist keine eigene Verarbeitungstätigkeit, sondern eine Zugriffsform auf bereits rechtmäßig gespeicherte Daten und braucht deshalb zu Recht keinen eigenen VVT-Eintrag. Grenzen ziehen vier Normen:

- **Art. 5(1)(f) / Art. 32 — Vertraulichkeit.** Die Suche darf nie mehr zeigen als die Detailansicht. Das ist die ADR-016-Invariante und die eigentliche Leitplanke jeder Erweiterung.
- **Art. 5(1)(b) — Zweckbindung**, konkretisiert durch die eigene DSFA: Sie führt „Zweckentfremdung" als Risiko und nennt als Maßnahme „Audit-Log, Berechtigungen" ([`dsgvo_templates/dsfa.md`](../../src/core/dsgvo_templates/dsfa.md) § 3.1).
- **Sozialdatenrecht** (§ 35 SGB I, §§ 67–85a SGB X, §§ 61–65 SGB VIII, § 203 StGB): **Erforderlichkeitsprinzip** — Zugriff nur, soweit zur Aufgabenerfüllung nötig. Eine Volltextsuche über Beratungs-Freitexte macht aus einer Dokumentation eine Recherchefläche.
- **Art. 5(2) / Art. 30 — Rechenschaftspflicht.** Die Verarbeitungsdokumentation muss zutreffen. Sie tat es nicht (siehe Consequences).

## Decision

### 1. Die Volltextsuche wird nicht protokolliert — weder Klartext noch Hash noch Zähler

Es gibt **keine** `AuditLog.Action` für die Suche, und weder [`views/search.py`](../../src/core/views/search.py) noch [`services/dashboard/search.py`](../../src/core/services/dashboard/search.py) rufen `log_audit_event` auf. Das bleibt so. Begründung, in der Reihenfolge ihres Gewichts:

1. **Der Suchbegriff ist selbst personenbezogen.** Er ist typischerweise ein Pseudonym, im schlechten Fall ein versehentlich eingetippter Klarname. Das AuditLog ist append-only und HMAC-verkettet ([ADR-007](007-auditlog-append-only.md)) — es wird von der Anonymisierung einer Person **nicht** mitredigiert. Was dort landet, überlebt die Art.-17-Löschung. Genau deshalb hat #1751 Pseudonyme und Meilenstein-Titel aus dem `detail` wieder entfernt und #1944 eine begründungspflichtige Schlüssel-Allowlist eingezogen ([`services/audit/detail_policy.py`](../../src/core/services/audit/detail_policy.py)). Eine Query-Protokollierung liefe dieser Linie frontal zuwider.
2. **Ein Hash der Query löst das nicht, er verschiebt es.** Der Werteraum ist klein und rät sich durch: Wer die Pseudonymliste einer Einrichtung hat — und die hat jede Fachkraft — rechnet einen Hash per Wörterbuch in Sekunden zurück. Ein Hash im Audit-Log ist Pseudonymisierung, keine Anonymisierung; er bleibt personenbezogenes Datum, nur schlechter les- und löschbar.
3. **Zähler und Zeitpunkt ohne Begriff belegen nicht, was sie belegen sollen.** Der DSFA-Fall ist Zweckentfremdung, und dessen Beweisfrage lautet „**wonach** wurde gesucht", nicht „wie oft". Eine reine Häufigkeitsspur beantwortet sie nie, erzeugt aber bei 30 zulässigen Anfragen pro Minute und Nutzer eine feinkörnige **Verhaltensspur über Beschäftigte** — ein neues Datenschutzproblem als Preis für einen Nachweis, der keiner ist. Art. 5(1)(c) spricht dagegen.
4. **Die Suche fügt keine Sichtbarkeit hinzu, die es zu protokollieren lohnte.** Sie zeigt strukturell nichts, was die Detailansicht nicht zeigt: derselbe Facility-Scope (RLS), dieselbe Sensitivitätsgrenze, dieselbe Papierkorb-Grenze. Wer einen Treffer öffnet, löst genau die Protokollierung aus, die für diesen Datensatz vorgesehen ist — bei einer Person der Stufe „Mit Zusatzangaben" also `VIEW_QUALIFIED` ([`views/clients.py`](../../src/core/views/clients.py)).
5. **Dort, wo Daten die Anwendung wirklich verlassen, existiert die Spur längst:** `EXPORT` (Auskunft, Statistik, Sachbericht), `DOWNLOAD` (Datei aus dem Vault), `OFFLINE_BUNDLE_READ`/`OFFLINE_NOTE_EXPORT` (Mitnahme aufs Gerät), `SYSTEM_VIEW` (facility-übergreifend im `/system/`-Bereich und an den sechs Admin-Flächen mit `AdminReadAuditMixin` — siehe die Einschränkung in der Tabelle oben).

**Kompensation statt Protokoll.** Was die Suche gegen Missbrauch schützt, sind präventive Maßnahmen, keine nachträgliche Spur: Rollen-Gate (Assistenz und höher), Facility-Scoping + RLS, Ratelimit 30/min je Nutzer ([ADR-026](026-rate-limiting.md)), Mindestlänge zwei Zeichen, der Sensitivity-Filter im Schreibpfad und der Verzicht auf jede Freitext-Suche über `description`-Felder.

**Restrisiko, ausdrücklich benannt:** Eine Zweckentfremdung, die sich rein im Nachschlagen erschöpft — „hat Kollegin X nach ihrer Nachbarin gesucht?" — ist nachträglich **nicht** nachweisbar. Die DSFA-Maßnahme „Audit-Log, Berechtigungen" trägt für die Suche also nur ihre zweite Hälfte. Das ist eine bewusste Abwägung gegen die drei Punkte oben, keine Lücke aus Versehen.

### 2. `Case` bekommt kein `visible_to()` — der Fall-Rahmen bleibt bewusst offen

`visible_to()` existiert nur auf den Event-Managern ([`models/managers.py`](../../src/core/models/managers.py)); `Case.objects` ist ein reiner `FacilityScopedManager` ([`models/case.py`](../../src/core/models/case.py)). Folge: **Titel, Beschreibung, Status und Verantwortliche eines Falls sind für jede Fachkraft der Einrichtung sichtbar**, auch wenn der `document_type` des Falls HIGH ist. Gestuft sind nur die dynamischen Feldinhalte ([`services/dynamic_data.py`](../../src/core/services/dynamic_data.py), `user_can_see_field`). Der `?q=`-Titelfilter der Fall-Liste trägt diese Offenheit bereits.

Das bleibt so, und zwar als Entscheidung, nicht als Befund:

- **Fachlich gewollt:** Vertretung, Übergabe und Fallzuordnung setzen voraus, dass eine Fachkraft den Fall-*Rahmen* der eigenen Einrichtung sieht. Ein Fall, dessen Existenz für Kolleginnen unsichtbar ist, wird doppelt angelegt.
- **Kein akuter Anlass:** Der Fall-Titel steht **nicht** in der globalen Suche. Die Fläche, die ein Sensitivity-Gate schließen würde, ist heute die Fall-Liste — dieselbe Liste, in der der Fall ohnehin mit Titel steht.
- **Kosten und Regressionsfläche:** Ein `visible_to()` auf dem Case-Manager wirkt auf Liste, Detail, Zuordnungs-Dialoge, Auskunft, Statistik und die RLS-Tests. Der Aufwand ist zwei bis drei Tage plus Regressionsrisiko in genau den Pfaden, die die Datenauskunft tragen.

**Grenze der Entscheidung:** Sie gilt für den Fall-Rahmen *bei heutigem Suchraum*. Wandert `Case.title` in die globale Suche (#1842), ist **vorher** neu zu entscheiden — dann verbreitert die Suche die Offenheit von „wer die Fall-Liste öffnet" auf „wer irgendein Wort tippt".

### 3. Fünf Regeln für jede Erweiterung des Suchraums

1. **Nie mehr als die Detailansicht.** Verschlüsselte Werte, ELEVATED-/HIGH-Felder und Klarnamen von Dateien bleiben dauerhaft draußen.
2. **Kein Existenz-Orakel.** Keine rollenabhängig erscheinenden Hinweise, keine bedingten Gruppen oder Chips (Regeln L4/L6 aus #1803).
3. **Suchbar nur, wofür es eine Sichtbarkeits-Stellschraube gibt.** `Episode`, `OutcomeGoal`, `Milestone` und `WorkItem` liegen außerhalb des DocumentType-/FieldTemplate-Sensitivity-Modells — dort gibt es keinen Hebel, einen Treffer zu unterdrücken. Wer sie suchbar macht, baut den Hebel zuerst.
4. **Titel ja, Beschreibungen nein.** Solange die `description`-Felder den Art.-9-Warnhinweis tragen und #918 offen ist, wäre eine Volltextsuche darüber eine Recherchefläche über Beratungsinhalte.
5. **Jede neue Indexspalte erbt die Löschpflicht** von `Event.search_text`: Sie ist eine Klartext-Zweitkopie und muss beim Leeren in `update_fields` stehen ([`retention/enforcement.py`](../../src/core/retention/enforcement.py)), sonst bleibt Klartext liegen.

## Consequences

- **Das Verarbeitungsverzeichnis wird korrigiert.** [`services/compliance/vvt.py`](../../src/core/services/compliance/vvt.py) behauptete in zwei TOM-Einträgen „AuditLog auf Lese- und Schreibzugriffe" bzw. „AuditLog jeder Lese- und Schreibaktion". Das war **unzutreffend nach Art. 30 Abs. 1 lit. g DSGVO**. Von Lesezugriffen protokolliert sind nur `VIEW_QUALIFIED`, `EXPORT`, `DOWNLOAD`, `OFFLINE_*_READ` und `SYSTEM_VIEW`; Listen-, Detail- und Suchaufrufe erzeugen keinen Eintrag. Und auch die Schreibseite ist nicht lückenlos: **Bearbeitungen von Episoden und Wirkungszielen landen nirgends** — weder im AuditLog noch in einer `EventHistory` (die gibt es nur für Ereignisse) noch im Aktivitäts-Feed; die Aktionen `EPISODE_UPDATE`/`GOAL_UPDATE` existieren im Vokabular, ihr Schreibpfad ist offen (#1959). Ein **Fall** wiederum kann überhaupt nicht gelöscht werden ([`services/case/cases.py`](../../src/core/services/case/cases.py), ADR-039 §6) — eine `CASE_DELETE`-Aktion gibt es folgerichtig nicht. Beide Einträge sagen das jetzt so. Der Fehler bestand unabhängig von der Suchfrage und war unabhängig davon zu beheben.
- **Guards halten Entscheidung und Verzeichnis zusammen** ([`src/tests/test_vvt.py`](../../src/tests/test_vvt.py)): Keine TOM darf eine flächendeckende Lese-Protokollierung oder eine Fall-Löschung behaupten; eine TOM, die Episoden oder Wirkungsziele nennt, muss ausdrücklich „gar nicht protokolliert" sagen; über die echten Bearbeiten-Routen darf für beide Unterobjekte keine `AuditLog`-, `EventHistory`- oder `Activity`-Zeile entstehen; und es darf weder eine Such-`AuditLog.Action` noch einen `log_audit_event`-Aufruf in den beiden Suchmodulen geben. `VIEW_QUALIFIED` darf zudem nur aus [`views/clients.py`](../../src/core/views/clients.py) geschrieben werden — sonst sind die auf die Personen-Detailseite eingegrenzten Zusagen in Oberfläche, Handbuch, Glossar und Fachkonzept nachzuziehen ([`src/tests/test_contact_stage_explanations.py`](../../src/tests/test_contact_stage_explanations.py) deckt Badge, Legende und `help_text` ab). Wer die Suche künftig protokollieren will, muss diese ADR und den VVT im selben Schritt anfassen — das ist der Zweck.
- **Die Zusage „jeder Zugriff wird protokolliert" fällt überall.** Sie stand an sechs Stellen, zwei davon in der ausgelieferten Oberfläche: `Client.contact_stage.help_text`, [`_client_badge.html`](../../src/templates/components/_client_badge.html), [`_contact_stage_legend.html`](../../src/templates/components/_contact_stage_legend.html), [`user-guide.md`](../user-guide.md), [`glossar.md`](../glossar.md) und [`fachkonzept-anlaufstelle.md`](../fachkonzept-anlaufstelle.md) (samt englischen Fassungen). `VIEW_QUALIFIED` entsteht projektweit **einmal** — im GET der Personen-Detailseite ([`views/clients.py`](../../src/core/views/clients.py)). Das Bearbeiten-Formular derselben Person zeigt ihre Zusatzangaben entschlüsselt im Klartext und schreibt nichts.
- **`+`** Die Rechenschaftsdokumentation stimmt wieder mit dem Code überein; ein Auditor liest keine Zusage mehr, die die Anwendung nicht hält.
- **`+`** Kein neuer PII-Speicher, kein zusätzlicher Löschpfad, keine Beschäftigten-Verhaltensspur.
- **`−`** Zweckentfremdung über reines Nachschlagen bleibt nachträglich unbelegbar (siehe Restrisiko oben).
- **`−`** Sechs der elf Admin-Suchflächen schreiben keinen `SYSTEM_VIEW` — darunter die `detail`-Suche des `AuditLogAdmin`, über die ein `super_admin` einrichtungsübergreifend lesen kann. Der Zugang ist sudo-gated und rollen-gegated, aber nicht auditiert. Hier nur festgehalten, nicht behoben: die Ausweitung des `AdminReadAuditMixin` ist eine eigene Sicherheitsentscheidung (Wechselwirkung mit der `detail`-Allowlist aus #1944 und mit der Massen-Export-Heuristik).
- **`−`** Der Fall-Rahmen bleibt für alle Fachkräfte offen. Das ist dokumentiert statt verschwiegen — Träger, die es anders brauchen, sehen es hier und können es als Anforderung melden.
- **Unberührt bleiben** #1842 (Suchraum-Erweiterung; die fünf Regeln oben sind seine Leitplanken) und #918 (Klartext-Freitexte). Offen und in beiden Tickets zu berücksichtigen: `OutcomeGoal.description` ([`models/outcome.py`](../../src/core/models/outcome.py)) ist das einzige Freitextfeld **ohne** Art.-9-Warnhinweis und fehlt im Inventar in [`security-notes.md`](../security-notes.md); ebenso tragen die `title`-Felder von Fall, Episode, Aufgabe, Wirkungsziel und Meilenstein keinen Warnhinweis, obwohl die Anonymisierung sie als personenbezogen behandelt und überschreibt.

## Alternatives considered

- **Klartext-Query im AuditLog.** Verworfen: Der Suchbegriff ist PII, das Log ist append-only und überlebt jede Anonymisierung — die direkte Umkehr von #1751/#1944.
- **Gehashte Query.** Verworfen: über den kleinen Werteraum der Pseudonyme trivial rückrechenbar; bleibt personenbezogen und ist zusätzlich unlesbar für die Prüferin, der sie nützen soll.
- **Nur Zähler und Zeitpunkt.** Verworfen: beantwortet die Missbrauchsfrage nicht und erzeugt eine Verhaltensspur über Beschäftigte.
- **Protokollierung erst ab N Treffern / ab bestimmten Rollen.** Verworfen: bedingte Protokollierung ist die Log-Variante des Existenz-Orakels — aus „hier wurde geloggt" ließe sich auf die Trefferlage schließen, und die Regel wäre für Prüfer nicht mehr erklärbar.
- **`visible_to()` auf `Case` jetzt nachziehen.** Verworfen/vertagt: Aufwand und Regressionsfläche ohne heutigen Nutzen, weil der Fall-Titel nicht in der globalen Suche steht. Wiedervorlage ist #1842.
- **Nur den VVT korrigieren, ohne ADR.** Verworfen: Der VVT ist eine Zustandsbeschreibung, keine Begründung. Ohne ADR wäre die Offenheit des Fall-Rahmens weiterhin nirgends als *Entscheidung* dokumentiert — und die nächste Diskussion begänne wieder bei null.

## References

- [ADR-016: Volltextsuche bleibt in PostgreSQL](016-search-postgres-only.md) — Backend und Sensitivity-Invariante
- [ADR-007: AuditLog als Append-Only](007-auditlog-append-only.md) · [ADR-019: Custom AdminSite mit Sudo](019-custom-admin-site-sudo.md) · [ADR-026: Rate-Limiting](026-rate-limiting.md)
- [`src/core/services/dashboard/search.py`](../../src/core/services/dashboard/search.py) · [`src/core/views/search.py`](../../src/core/views/search.py)
- [`src/core/services/compliance/vvt.py`](../../src/core/services/compliance/vvt.py) · [`src/core/services/audit/detail_policy.py`](../../src/core/services/audit/detail_policy.py)
- [`docs/datenschutz.md`](../datenschutz.md) (Wegweiser) · [`docs/admin-guide.md` § 7.4](../admin-guide.md#74-audit-log) (was das Audit-Log protokolliert — und was nicht)

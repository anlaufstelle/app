# ADR-044: Fall-Löschpfad — Papierkorb, gestaffelter Antrag, Frist und Redaktion

- **Status:** Accepted
- **Date:** 2026-08-22
- **Deciders:** Tobias Nix
- **Refs:** #1818 (Umsetzung, Produktentscheide P1–P6), #1817 (Herkunft: „es gibt keinen Fall-Löschpfad", bestätigt), #1777 (Papierkorb-Grenze der Fall-Domäne), #1737 (Personen-Papierkorb als Blaupause), #1751 (PII aus AuditLog und Aktivitäts-Feed), #1756 (gemeinsamer Legal-Hold-Helfer statt Kopie), #1782 (Datei-Löschung ans Transaktionsende), [ADR-039](039-owner-generische-dateianhaenge.md) §6 (hiermit abgelöst) und §7 (Marker-Invariante I-M1), [ADR-021](021-retention-modell.md) (Fristen, Legal Hold)

## Context

Fälle waren bis hierher **unentfernbar**. Es gab `case_create`, `case_update`, `case_close`, `case_reopen` — aber keine Lösch-Route ([`urls/__init__.py`](../../src/core/urls/__init__.py)). Zwei Lücken folgten daraus:

**Fachlich.** Ein versehentlich angelegter Fall blieb dauerhaft in Liste, Statistik und Auskunft stehen. „Schließen" ist eine fachliche Aussage über den Hilfeverlauf, kein Korrekturmittel — ein geschlossener Irrläufer ist immer noch ein Irrläufer.

**DSGVO.** Art. 17 kannte genau zwei Granularitäten: einzelnes Ereignis oder ganze Person (`DeletionRequest.TargetType`). Wer genau einen Fall gelöscht haben wollte, zwang die Einrichtung zur Alles-oder-nichts-Entscheidung. Und fall-eigene Dateianhänge — seit Milestone 24 möglich ([ADR-039](039-owner-generische-dateianhaenge.md)) — überlebten bis zur Anonymisierung der **gesamten** Person, was Art. 5 Abs. 1 lit. e zuwiderläuft.

Der Zustand war kein Versehen, sondern verriegelt: `src/tests/test_cases_trash_invariant.py` pinnte in drei Guards, dass `Case` zwar `SoftDeletableModel` erbt, aber **kein** Produktionspfad `is_deleted` je setzt. Der Choke-Point `cases_with_live_client()` benannte in seinem eigenen Docstring, was zu tun wäre, wenn die Invariante fällt.

## Decision

Fälle bekommen denselben Löschweg wie Personen — Papierkorb, Wiederherstellung, Frist, endgültige Tilgung inklusive fall-eigener Anhänge, mit sechs bewussten Abweichungen (P1–P6 aus #1818).

### 1. Vier-Augen gestaffelt nach `Client.contact_stage` (P1c)

Nicht pauschal wie bei Personen und nicht pauschal direkt wie bei nicht-qualifizierten Ereignissen, sondern **wie `EventDeleteView.post`**: Steht die Person des Falls auf der Stufe „Mit Zusatzangaben" (`QUALIFIED`), braucht die Löschung einen Antrag und die Bestätigung einer zweiten Person mit dem Recht `can_confirm_deletion`; sonst löscht die Fachkraft direkt.

Begründung: Der Fall trägt kein eigenes Schutzniveau — er erbt es von der Person. Ein Vier-Augen-Verfahren für den Fall einer nur pseudonym geführten Person wäre strenger als für deren **Ereignisse**, obwohl die Ereignisse den Beratungsinhalt tragen. Die Staffelung hält die Löschschwelle in derselben Ordnung wie die Erfassungsschwelle.

Der Service ist Single Source of Truth: `soft_delete_case` weist einen qualifizierten Fall auch dann ab, wenn eine View die Staffelung falsch auswertet.

### 2. Keine eigene Frist — `client_trash_days` gilt mit (P2b)

Es gibt **keine** `case_trash_days` in `Settings`. Der Papierkorb eines Falls läuft nach derselben Frist ab wie der einer Person.

Begründung: Ein zweiter Fristbegriff müsste in Datenschutzerklärung und DSFA erklärt und von Betreibern gepflegt werden, ohne dass ihn irgendetwas fachlich trägt — die Frist beantwortet in beiden Fällen dieselbe Frage („wie lange ist eine Löschung noch umkehrbar"). Ein zweiter Wert erzeugte vor allem eine zweite Gelegenheit, ihn falsch zu setzen.

### 3. Nach Fristablauf wird redigiert, nicht gelöscht (P3a)

Die Fall-Zeile bleibt und wird inhaltlich geleert — Titel auf `[Anonymisiert <Datum>]`, Beschreibung und `data_json` leer, `document_type` auf `NULL`; Episoden, Wirkungsziele und Meilensteine ebenso. Exakt das Ergebnis, das die Personen-Anonymisierung erzeugt.

Begründung gegen den Hard-Delete: Er risse per CASCADE Episoden, Ziele und Meilensteine mit und machte die Statistik-Zeitreihen unstetig — eine Auswertung über zwölf Monate änderte sich rückwirkend, sobald jemand einen alten Fall löscht. Die Redaktion nimmt den Personenbezug und lässt die Zählbarkeit stehen.

**Eine** Implementierung für beide Wege: `redact_case_content` / `redact_case_goals` liegen in [`services/case/deletion.py`](../../src/core/services/case/deletion.py) und werden vom Personen-Pfad (`_redact_cases_and_episodes`) mitbenutzt. Zwei Kopien wären die Drift, die #1756 gekostet hat: der Rest darf nicht davon abhängen, ob eine ganze Person oder ein einzelner Fall gelöscht wurde.

Mitredigiert werden zwei Stellen, die den Falltitel als Freitext weitertragen und die Papierkorb-Frist deutlich überleben würden: die `Activity.summary`-Zeilen des Falls (`retention_activities_days`, Default 365 gegen 30 Tage — der Befund aus #1751) und der `reason` des Löschantrags. Die Antrags-Metadaten (wer, wann, welcher Status) bleiben als Vier-Augen-Nachweis erhalten.

### 4. Legal Hold: `Case` wird Hold-Ziel, und ein Ereignis-Hold sperrt den Fall (P4)

`LegalHold.target_type` ist ein freies `CharField` ohne `choices` — `"Case"` war immer speicherbar. Was fehlte, war der **Erzeugungsweg**: `create_legal_hold` setzte einen `RetentionProposal` voraus, und `RetentionProposal.TargetType` kennt nur `EVENT`.

Gelöst mit einem vorschlagsfreien Einstieg `create_hold_for_target(...)`; `create_legal_hold(proposal, …)` ist seither dessen Aufrufer und setzt zusätzlich den Vorschlag auf `HELD`. **Nicht** gelöst, indem `RetentionProposal` einen `CASE`-Zweig bekommt: ein Vorschlag trägt `deletion_due_at` und `retention_category` und entsteht aus den Fristen des Ereignis-Enforcements. Für Fälle gibt es keine solche Frist — ihr Löschweg ist der Antrag plus die Papierkorb-Frist. Ein Pseudo-Vorschlag nur als Träger des Holds hätte ein leerlaufendes Objekt in die Retention-Ansicht gespült.

Die Sperre (`exclude_cases_with_active_holds`, Einzelprüfung `case_has_active_hold`) hat **drei** Zweige, gebaut nach dem Muster `exclude_clients_with_active_holds`:

| Hold auf | sperrt, weil |
|---|---|
| dem Fall selbst | direkter Aufbewahrungsauftrag |
| einem **Ereignis des Falls** | die Tilgung löst die Zuordnung (§5); welcher Fall ein gehaltenes Ereignis einrahmte, ist selbst Beweismittel (Spoliations-Schutz analog #1066) |
| der **Person** des Falls | sonst wäre der Fall-Löschweg die Hintertür um genau die Sperre, die die Personen-Anonymisierung blockiert — sie redigiert dieselben Fall-Inhalte |

Bewusst **nicht** über `client_has_active_hold` gebaut: der Helfer schließt zusätzlich Personen aus, von denen *irgendein* Ereignis unter Hold steht — auch eines, das mit diesem Fall nichts zu tun hat. Für die Personen-Anonymisierung ist das richtig (sie fasst alle Ereignisse an), für die Fall-Redaktion wäre es Überdeckung: sie lässt Ereignisse unberührt.

Mitgezogen: `owner_has_active_hold` (der Anhang-Purge-Pfad aus #1874) kennt den Case-Hold jetzt zusätzlich zum Personen-Zweig — rein additiv, die bisherige, breitere Personen-Sperre bleibt.

### 5. Ereignisse überleben und verlieren nur die Zuordnung (P5)

`Event.case` ist `SET_NULL`; die Ereignisse bleiben vollständig in der Personenakte sichtbar. Zugeordnete Ereignisse **blockieren die Löschung nicht** — das weicht bewusst von der ursprünglichen Ticket-Empfehlung („blockieren") ab.

Begründung: Ein Ereignis dokumentiert einen realen Kontakt und gehört der Person, nicht dem Fall; der Fall ist nur der Rahmen. Ein Blockieren zwänge dazu, vor dem Löschen jedes Ereignis umzuhängen — bei einem versehentlich angelegten Fall ist das Arbeit ohne fachlichen Ertrag, und ein Umhängen „nur damit der Fall weggeht" verfälscht die Dokumentation eher, als dass es sie schützt.

Die Zuordnung fällt **erst bei der Tilgung**, nicht schon beim Verschieben in den Papierkorb — sonst wäre ein Wiederherstellen verlustbehaftet. Sie wird dort ausdrücklich gelöst: weil die Tilgung eine Redaktion ist (§3), bleibt die Fall-Zeile stehen und die `SET_NULL`-Kaskade feuert nie von selbst.

### 6. Auch offene Fälle sind löschbar (P6)

`Case.Status` spielt für den Löschweg keine Rolle. Der Anlassfall ist der Irrläufer, und ein irrtümlich angelegter Fall ist per Definition offen — eine Vorbedingung „nur geschlossene Fälle" zwänge dazu, ihn erst fachlich abzuschließen (eine falsche Aussage über den Hilfeverlauf), um ihn dann zu löschen.

### 7. Die Papierkorb-Grenze läuft über `cases_with_live_client()`

Der Helfer filtert seit diesem ADR zusätzlich `is_deleted=False` **am Fall**. Damit ziehen alle Lesepfade der Fall-Domäne mit (Detail, Bearbeiten, Partials, Episoden, Ziele, Fallliste, Fall-Select am Ereignisformular). Der Papierkorb-Zugang selbst fragt bewusst nicht über diesen Helfer ab — er ist der eine Ort, an dem ein gelöschter Fall sichtbar sein muss.

`test_cases_trash_invariant.py` ist nicht gelöscht, sondern **umgedreht**: dieselben Ableitungen aus dem Produktionscode, mit der neuen Soll-Menge (`TargetType` enthält `Case`; genau ein `case.soft_delete()`-Aufrufer, und der liegt im Lösch-Service). Ein Löschen der Datei hätte den Anlass mitgenommen, aus dem sie entstanden ist.

### 8. Audit-Vokabular

Drei eigene Aktionen `CASE_SOFT_DELETED` / `CASE_RESTORED` / `CASE_ANONYMIZED` — Spiegel der Personen-Trias, weil unter `/audit/` genau die Unterscheidung „hinein / zurück / endgültig" die Frage ist. Dazu die generischen `DELETION_REQUESTED` / `DELETION_APPROVED` / `DELETION_REJECTED` (`target_type="DeletionRequest"`) für die drei Antragsschritte; der Genehmigungs-Eintrag ist zugleich die Zeile, die `detect_mass_client_destruction` zählt.

Der Falltitel steht **nie** im `detail` (#1751, Allowlist #1944) — das Log ist append-only und überlebt jede spätere Anonymisierung; die Zuordnung läuft über `target_id` bzw. `target_case`.

## Consequences

- [ADR-039](039-owner-generische-dateianhaenge.md) §6 fällt (Update-Block dort). Fall-eigene Anhänge haben einen eigenen Löschpfad und warten nicht mehr auf die Anonymisierung der ganzen Person; die Prüftabelle in §7 bekommt ihre achte Zeile (Marker-Invariante I-M1: `delete_attachments_for(case)` und das Leeren von `case.data_json` liegen im selben `atomic`, die Datei-Löschung steht wie in #1782 ganz am Ende).
- Es gibt einen zweiten `soft_delete()`-Aufrufer im Produktionscode. Die Aussage „nur Personen kennen einen Papierkorb" ist in Doku und Tests überall nachzuziehen.
- Die Statistik zählt Papierkorb-Fälle nicht mehr mit, redigierte Fälle dagegen weiter — die Fallzahl sinkt beim Löschen und bleibt beim Fristablauf konstant.
- Betreiberseitig kommt **keine** neue Einstellung dazu (§2); der Retention-Lauf meldet eine zusätzliche Zeile (`Anonymized N trash-expired case(s)`).
- Ein Legal Hold auf einen Fall ist ab jetzt speicher- **und** erzeugbar. Der vorschlagsfreie Einstieg steht dem Service-Layer offen; die Oberfläche dazu ist noch nicht gebaut.
- Bekannte Grenze: der **direkte** Löschweg (nicht-qualifizierte Person, §1) schreibt keine `DELETION_APPROVED`-Zeile und läuft deshalb nicht in den Massen-Destruktions-Zähler der Breach-Erkennung.

## Alternatives considered

- **Hard-Delete nach Fristablauf** (P3b): reißt Episoden, Ziele und Meilensteine per CASCADE mit und macht Statistik-Zeitreihen unstetig — verworfen (§3).
- **Eigene `case_trash_days`** (P2a): zweiter Fristbegriff in Datenschutzerklärung und DSFA ohne fachlichen Träger — verworfen (§2).
- **Zugeordnete Ereignisse blockieren die Löschung** (P5, Ticket-Empfehlung): erzwingt Umhängen ohne fachlichen Ertrag und verfälscht die Dokumentation eher, als sie zu schützen — verworfen (§5).
- **Pauschales Vier-Augen für alle Fälle** (P1a): strenger als für die Ereignisse derselben Person, obwohl die Ereignisse den Beratungsinhalt tragen — verworfen (§1).
- **`RetentionProposal.TargetType.CASE`** als Träger des Fall-Legal-Holds: bräuchte `deletion_due_at`/`retention_category`, die es für Fälle nicht gibt, und spülte ein leerlaufendes Vorschlagsobjekt in die Retention-Ansicht — verworfen zugunsten des vorschlagsfreien Einstiegs (§4).
- **Eigene Redaktionslogik im Fall-Pfad** statt der geteilten Helfer: zwei Definitionen desselben Rests, mit genau dem Driftrisiko aus #1756 — verworfen (§3).

## References

- Umsetzungs-Issue: #1818
- Blaupause: [`services/client/main.py`](../../src/core/services/client/main.py) (`request_client_deletion` … `anonymize_eligible_soft_deleted_clients`), [`views/events.py`](../../src/core/views/events.py) (`EventDeleteView.post`, Staffelung)
- Grenze: [`services/case/cases.py`](../../src/core/services/case/cases.py) (`cases_with_live_client`), Guards in `src/tests/test_cases_trash_invariant.py`
- Abgelöst: [ADR-039](039-owner-generische-dateianhaenge.md) §6

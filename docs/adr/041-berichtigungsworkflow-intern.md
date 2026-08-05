# ADR-041: Interner Art.-16-Berichtigungsworkflow (App-gestützt, ohne Self-Service)

- **Status:** Accepted
- **Date:** 2026-08-05
- **Deciders:** Tobias Nix

## Context

[ADR-013](013-dsgvo-art16-no-selfservice.md) legt fest, dass Berichtigungen nach DSGVO Art. 16 **organisatorisch** über Mitarbeiter und Leitung laufen — ohne Klientel-Login und ohne Self-Service-Formular. Bisher endete die App-Unterstützung dieses Wegs beim direkten Editieren durch berechtigtes Personal plus AuditLog-Eintrag: Der *Antrag* selbst (wer hat wann welche Korrektur mit welcher Begründung angeregt, wer hat sie fachlich geprüft) existierte nur außerhalb der App — auf Papier, per Mail oder mündlich.

Das hat drei Schwächen:

- **Rechenschaftslücke (Art. 5 Abs. 2):** Der AuditLog zeigt *dass* geändert wurde, aber nicht den Prüfweg vom Berichtigungsverlangen bis zur Entscheidung. Bei einer Aufsichts-Anfrage muss die Einrichtung den Nachweis aus losen Notizen rekonstruieren.
- **Kein Vier-Augen-Prinzip:** Wer Schreibrecht auf die Akte hat, konnte Berichtigungen allein durchführen. Der Lösch-Workflow (Refs #932) hat für die schwerwiegendere Operation längst ein Antrags-/Genehmigungs-Modell — Berichtigungen blieben dahinter zurück.
- **Fachliche Kontextualisierung undokumentiert:** ADR-013 begründet den organisatorischen Weg gerade damit, dass „falsche" Einträge oft Wahrnehmungsfragen sind. Die fachliche Abwägung, warum eine Korrektur (nicht) erfolgt, wurde nirgends festgehalten.

Milestone M4 (#1060, app#37) fordert einen Berichtigungsworkflow als Teil der Datenportabilitäts-Werkzeuge.

## Decision

Die App erhält einen **internen Antrags-/Review-Workflow für Berichtigungen** (`RectificationRequest`). Er **ergänzt und präzisiert ADR-013 — er ersetzt es nicht**:

- **ADR-013 bleibt vollumfänglich in Kraft.** Es gibt weiterhin keinen Klientel-Login, kein Self-Service-Formular und keine erzwungene Identitätsprüfung gegenüber Klientel. Das Berichtigungsverlangen erreicht die Einrichtung wie bisher offline/organisatorisch. Insbesondere bleibt die in ADR-013 verworfene Hybrid-Variante („Self-Service-Anfrage, Mitarbeiter genehmigt") verworfen: Antragsteller ist hier **immer** eine Fachkraft, nie die betroffene Person.
- **Neu ist die App-Abbildung des internen Prüfwegs:** Eine Fachkraft (`staff`+) erfasst den Berichtigungsantrag (Zielfeld, alter/neuer Wert, fachliche Begründung); Leitung (`facility_lead`/`facility_admin`) prüft und genehmigt oder lehnt ab. Selbst-Review ist ausgeschlossen (Vier-Augen-Prinzip, Präzedenz Lösch-Workflow #932).
- **Werte verschlüsselt, Anwendung nur über die Service-Schicht:** `old_value`/`new_value` liegen feldverschlüsselt am Antrag; die Genehmigung wendet die Änderung ausschließlich über `update_client` an (RLS-Guards, Audit, optimistisches Locking inklusive). Berichtigungsfähig sind Stammdaten und `data_json`-Felder gemäß Sichtbarkeitsregeln; Datei-Felder nicht.
- **Audit je Workflow-Stufe:** `RECTIFICATION_REQUESTED/APPROVED/REJECTED` (analog `DELETION_*`), `detail` ohne Feldwerte.

## Consequences

- **+** Art.-5(2)-Nachweis des vollständigen Prüfwegs (Antrag → fachliche Prüfung → Entscheidung → Anwendung) in der App, revisionssicher im append-only AuditLog.
- **+** Vier-Augen-Prinzip für Datenkorrekturen, konsistent mit dem Lösch-Workflow.
- **+** Die fachliche Begründung (ADR-013: „abweichende Wahrnehmungen") wird strukturiert dokumentiert — auch bei Ablehnung.
- **−** Mehraufwand für triviale Korrekturen (Tippfehler): auch sie laufen über den Antrag, sobald der Workflow genutzt wird. Direktes Editieren durch berechtigtes Personal bleibt für den Alltag erlaubt; der Workflow ist der dokumentierte Weg für Art.-16-Verlangen.
- **−** Anonymisierung räumt offene/abgeschlossene Anträge nicht automatisch ab — Folge-Issue erforderlich (bekannter Residue, siehe `_residue_expectations.py`).

## Alternatives considered

- **ADR-013 supersede (Self-Service light):** Verworfen — die Gründe aus ADR-013 (Niedrigschwelligkeit, Identitätsbindung, fachliche Verantwortung) gelten unverändert.
- **Direktes Editieren + Freitext-Auditkommentar:** Verworfen — kein Vier-Augen-Prinzip, Prüfweg nicht strukturiert abfragbar, Begründung optional statt Pflicht.
- **Wiederverwendung des DeletionRequest-Modells (generischer „ChangeRequest"):** Verworfen — Löschung und Berichtigung haben unterschiedliche Zustandsräume, Berechtigungen und Payloads (verschlüsselte Wertpaare vs. Objektreferenz); eine Generalisierung ohne zweiten Nutzer wäre spekulativ.

## References

- [ADR-013: DSGVO Art. 16 ohne App-Self-Service](013-dsgvo-art16-no-selfservice.md)
- [ADR-007: AuditLog append-only](007-auditlog-append-only.md)
- [#1060 — M4 Datenportabilität](https://github.com/anlaufstelle/app/issues/1060)
- Lösch-Workflow als Vier-Augen-Präzedenz: Refs #932

# Datenschutz / DSGVO — Wegweiser

> **Wegweiser, kein Inhalts-Silo.** Diese Seite verlinkt je DSGVO-Thema die maßgebliche Quelle (ADR, Vorlage, Code, FAQ) — sie erklärt die Mechanik nicht erneut. **Bei Widerspruch gilt der Code** (bzw. ADR/Vorlage), nicht diese Übersicht.
>
> **Pflege:** Kommt eine neue datenschutzrelevante ADR hinzu, hier eine Zeile ergänzen.

**Audience-Tags:** **[Auditor]** Nachweis/Entscheidung · **[Dev]** Code/Architektur · **[Träger]** Bedienung/Betrieb. Tags stehen nur, wo sie real zutreffen.

## A. Grundsätze der Verarbeitung (Art. 5 DSGVO)

| Grundsatz | Verweis | Für |
|---|---|---|
| Art. 5(1)(b) Zweckbindung — Suchraum | [ADR-043](adr/043-suchraum-volltextsuche.md) — Suchanfragen werden bewusst **nicht** protokolliert (Begründung + Restrisiko), der Fall-Rahmen bleibt bewusst für alle Fachkräfte offen; fünf Regeln für jede Erweiterung des Suchraums | [Auditor] [Dev] |
| Art. 5(1)(c) Datenminimierung | [ADR-023](adr/023-k-anonymization-statistik.md) · [`external_report.py`](../src/core/services/dashboard/external_report.py) | [Auditor] [Dev] |
| Art. 5(1)(e) Speicherbegrenzung | [ADR-021](adr/021-retention-modell.md) · **Dateianhänge:** `DocumentType.retention_days` ist **event-only** — personen- und falleigene Dateien haben keine eigene Frist, sie folgen dem Owner-Lebenszyklus ([ADR-039](adr/039-owner-generische-dateianhaenge.md)) | [Auditor] [Dev] [Träger] |
| Art. 5(1)(f) Integrität & Vertraulichkeit | [ADR-006](adr/006-fernet-field-encryption.md) · [ADR-007](adr/007-auditlog-append-only.md) · [ADR-014](adr/014-encrypted-file-vault.md) · [security-notes.md](security-notes.md) | [Auditor] [Dev] |
| Art. 5(2) Rechenschaftspflicht | [ADR-007](adr/007-auditlog-append-only.md) · [VVT](../src/core/dsgvo_templates/verarbeitungsverzeichnis.md) · **was das Audit-Log nicht protokolliert** (gewöhnliche Listen-, Detail- und Suchaufrufe): [ADR-043](adr/043-suchraum-volltextsuche.md), [Admin-Guide § 7.4](admin-guide.md#74-audit-log) | [Auditor] |

## B. Betroffenenrechte (Art. 12–22 DSGVO)

| Recht | Verweis | Für |
|---|---|---|
| Art. 13/14 Informationspflichten | [Informationspflichten](../src/core/dsgvo_templates/informationspflichten.md) | [Träger] [Auditor] |
| Art. 15 Auskunft | Personen-Datenexport JSON/PDF ([`ClientDataExportJSONView`/`ClientDataExportPDFView`](../src/core/views/clients.py)) · **maßgeblich für Umfang und die bewussten Ausschlüsse: [Admin-Guide § 7.6](admin-guide.md#umfang-der-auskunft-art-15)** · enthält seit #1738 auch **Anhang-Metadaten** aller drei Owner (Dateiname, Größe, Typ, Versionskette; **keine** Binärinhalte) und seit #1796 auch **Berichtigungsanträge** (Art. 16, [ADR-041](adr/041-berichtigungsworkflow-intern.md)) in allen Status · [FAQ §C](faq.md#c-rollen--datenschutz) | [Träger] [Dev] |
| Art. 16 Berichtigung | [ADR-013](adr/013-dsgvo-art16-no-selfservice.md) — bewusst kein Self-Service | [Auditor] [Dev] |
| Art. 17 Löschung | [ADR-021](adr/021-retention-modell.md) (Soft-Delete-Strategien, realer Fristen-Zuschnitt, k-Anon-Retention inkl. Freitext-Kaskade #1094) · [`core/retention/`](../src/core/retention/) · **Dateianhänge:** **drei** physische Löschpfade — (1) Owner-Lebenszyklus: personen- und falleigene Dateien fallen mit der Klienten-Anonymisierung (Papierkorb-Frist `client_trash_days`), inkl. `.enc` auf **beiden** Anonymisierungspfaden (`anonymize_client` und K-Anon-Bridge, Paritäts-Guard); (2) Ereignis-Löschung/Retention (`delete_attachments_for`); (3) seit #1874 das **nutzer-getriggerte ersatzlose Löschen** einer einzelnen Versionskette durch Leitung/Anwendungsbetreuung (`purge_attachment_chain`, Zeilen + Marker + `.enc`, gesperrt bei aktivem Legal Hold) — der einzige Pfad, der eine einzelne irrtümlich hochgeladene Datei sofort beseitigt, ohne den ganzen Datensatz mitzunehmen. [ADR-039](adr/039-owner-generische-dateianhaenge.md) · **Restrisiken:** das append-only Audit-Log wird nicht mitredigiert — [security-notes.md § Audit-Log-Restdaten](security-notes.md#audit-log-restdaten-überleben-die-art-17-löschung-issue-1753); Medien-Backups überleben das ersatzlose Löschen — [security-notes.md § Medien-Backups](security-notes.md#medien-backups-überleben-das-ersatzlose-löschen-einer-datei-issue-1874) | [Auditor] [Dev] [Träger] |
| Art. 18 Einschränkung | **Sperrvermerk an der Person** (#2045): Flag `processing_restricted` + Rechtsgrund (Art. 18 Abs. 1 lit. a–d) + gesetzt durch/am · Write-Gate in [`services/client/restriction.py`](../src/core/services/client/restriction.py) als Spiegel von `resolve_writable_client` — Speicherung bleibt erlaubt (Art. 18 Abs. 2), inhaltliche Nutzung/Änderung ist gesperrt, **Ausnahme** der Berichtigungsworkflow ([ADR-041](adr/041-berichtigungsworkflow-intern.md)) · Aufhebung vier-äugig oder durch die Leitung, auditiert (`PROCESSING_RESTRICTED`/`PROCESSING_RESTRICTION_LIFTED`) · **Nicht zu verwechseln mit dem Legal Hold** ([`legal_holds.py`](../src/core/retention/legal_holds.py)): der blockiert die *Löschung* (Art. 17 Abs. 3), dieser Vermerk die *Nutzung*. Die Unterrichtungspflicht vor dem Aufheben (Art. 18 Abs. 3) bleibt organisatorisch. | [Auditor] [Dev] [Träger] |
| Art. 20 Datenübertragbarkeit | über den maschinenlesbaren Personen-JSON-Export — **identische Datenbasis wie Art. 15** (derselbe Export, gleiche Ausschlüsse, [Admin-Guide § 7.6](admin-guide.md#umfang-der-auskunft-art-15)); kein dedizierter Übertragbarkeits-Workflow | [Träger] |

## C. Technische & organisatorische Maßnahmen (Art. 25, 32 DSGVO)

| Maßnahme | Verweis | Für |
|---|---|---|
| Art. 25 Privacy by Design/Default | [ADR-005](adr/005-facility-scoping-and-rls.md) — Facility-Scoping + RLS | [Auditor] [Dev] |
| Art. 25 Datenminimierung beim Upload | **Metadaten-Bereinigung der Bild-Anhänge** (#2128): `strip_image_metadata` in [`services/file_vault/policy.py`](../src/core/services/file_vault/policy.py) re-encodiert JPEG/PNG/WebP/TIFF vor der Verschlüsselung und entfernt dabei EXIF (inkl. **GPS-Aufnahmeort**, Gerätekennung, Aufnahmezeit, eingebettetes Vorschaubild), PNG-Textchunks und XMP/IPTC — die Orientierung bleibt als Pixel-Rotation erhalten, das ICC-Profil bleibt. Maßgeblich ist der per Magic-Bytes erkannte Typ, nicht die Endung; ein als Bild erkannter Inhalt unter einer Nicht-Bild-Endung wird fail-closed abgewiesen (`mime_mismatch`), ebenso ein nicht dekodierbares Bild (`image_undecodable`). Immer aktiv, kein Schalter. **Grenzen:** (1) PDF-/OOXML-Dokumentmetadaten (`/Author`, `dc:creator` — Beschäftigten-Klarnamen) werden **nicht** bereinigt; (2) GIF und BMP bleiben byte-identisch — GIF89a-Comment-/XMP-Extensions und BMP-v5-ICC bleiben also erhalten (ein Re-Encode veränderte Palette und Animation; Empfehlung: `gif`/`bmp` nicht freischalten). | [Auditor] [Dev] [Träger] |
| Art. 32 Verschlüsselung at rest | [ADR-006](adr/006-fernet-field-encryption.md) (Felder) · [ADR-014](adr/014-encrypted-file-vault.md) (Datei-Vault, Fernet/AES-128) — **je Owner identisch**: Dateien an Ereignis, Person und Fall laufen durch dieselbe Pipeline (Virenscan → Verschlüsselung → Speicherpfad), [ADR-039](adr/039-owner-generische-dateianhaenge.md) | [Auditor] [Dev] |
| Art. 32 Zugriffskontrolle | [ADR-005](adr/005-facility-scoping-and-rls.md) (RLS) · [ADR-018](adr/018-rollenmodell-superadmin.md)/[ADR-020](adr/020-three-role-postgres-model.md) (Rollen) · [ADR-015](adr/015-mfa-totp.md) (MFA) | [Auditor] [Dev] |
| Art. 32 Integrität/Nachvollziehbarkeit | [ADR-007](adr/007-auditlog-append-only.md) — AuditLog append-only | [Auditor] [Dev] |
| Art. 32 Offline-Daten | [ADR-022](adr/022-offline-snapshot-keys.md) — Offline-Snapshot & -Keys, verschlüsselte IndexedDB; [DSFA-/TOM-Einordnung des Offline-Pfads](adr/022-offline-snapshot-keys.md#dsfa-tom-einordnung-des-offline-pfads-1343) | [Dev] [Auditor] |

## D. Auftragsverarbeitung & Nachweisdokumente (Art. 28, 30, 35 DSGVO)

| Dokument | Verweis | Für |
|---|---|---|
| Art. 28 Auftragsverarbeitungsvertrag | [AV-Vertrag](../src/core/dsgvo_templates/av-vertrag.md) | [Träger] |
| Art. 30 Verzeichnis von Verarbeitungstätigkeiten | [VVT](../src/core/dsgvo_templates/verarbeitungsverzeichnis.md) | [Träger] [Auditor] |
| Art. 35 Datenschutz-Folgenabschätzung | [DSFA](../src/core/dsgvo_templates/dsfa.md) | [Träger] [Auditor] |
| Art. 5(1)(e) / Art. 17 Löschkonzept | [Löschkonzept](../src/core/dsgvo_templates/loeschkonzept.md) | [Träger] [Auditor] |
| Art. 13/14 / § 26 BDSG Beschäftigten-Information | [Beschäftigten-Information](../src/core/dsgvo_templates/mitarbeiter_informationspflichten.md) | [Träger] |
| TOM (Gesamtübersicht) | [TOM](../src/core/dsgvo_templates/toms.md) | [Träger] [Auditor] |
| App-Feature „DSGVO-Paket" (Bündel-Download) | [`DSGVOPackageView`](../src/core/views/dsgvo.py) · [`generate_dsgvo_package`](../src/core/management/commands/generate_dsgvo_package.py) | [Träger] [Dev] |

## D2. Öffentliche Demo-Instanz — Web-Analyse & Sitzungsaufzeichnung (Refs #1604)

> Betrifft **ausschließlich** die öffentliche Demo `demo.anlaufstelle.app`, **nicht** das Self-Host-Produkt. Selbst gehostete Träger-Instanzen betreiben kein Umami/Tracking.

| Aspekt | Regelung | Für |
|---|---|---|
| Rechtsgrundlage | Art. 6(1)(a) — **Opt-in-Einwilligung**: Tracking/Recording/Heatmap starten technisch **erst nach Zustimmung** (kein stilles Tracking); Ablehnen ist möglich | [Auditor] [Träger] |
| Umfang | Umami-Web-Analyse **inkl. Session-Recording + Heatmap** (self-hosted; Endpoint per Deploy-Env konfiguriert, #1701, und in der Datenschutzerklärung der Demo benannt); **nur nach Login**, **nicht** auf der Anmeldeseite | [Auditor] [Dev] |
| Datenbestand | ausschließlich **synthetische Seed-Daten** (stündlicher Reset) — keine realen Betroffenendaten | [Auditor] |
| Rechtstexte | eigene Demo-Seiten `/impressum/` + `/datenschutzerklaerung/` (#1641; Inhalte als Deploy-Overlay des Betreibers, im Demo-Footer + Consent-Banner verlinkt) | [Träger] |
| Technik / CSP-Scoping | Consent-Gate [`umami-consent.js`](../src/static/js/umami-consent.js) · CSP-Freigabe eng gescoped [`DemoUmamiCSPRelaxMiddleware`](../src/core/middleware/demo_umami_csp_relax.py) · [security-notes.md](security-notes.md#eng-gescopte-ausnahme--umami-analytics-auf-der-demo-issue-1604) · [ADR-022 § (c)](adr/022-offline-snapshot-keys.md#dsfa-tom-einordnung-des-offline-pfads-1343) | [Dev] [Auditor] |

## E. Begriffe

Definitionen (PII, K-Anonymität, k-Anon-Retention, Pseudonymisierung, Legal Hold, Speicherbegrenzung, Soft-Delete-Strategien) → [glossar.md](glossar.md).

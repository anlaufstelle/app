
# Glossar — Anlaufstelle (Deutsch)

> **Deutsches Fachglossar.** Diese Datei führt die domänen-, sicherheits- und datenschutz-spezifischen Begriffe von Anlaufstelle alphabetisch auf und vertieft anschließend die **datenschutz- und compliance-relevanten** Begriffe (K-Anonymität, Retention, Pseudonymisierung, Legal Hold, Offline-Snapshot-Keys …). Die **bilinguale (DE↔EN)** Gesamtliste mit identischer Begriffsmenge steht in [`en/glossary.md`](en/glossary.md); das Domänen-Glossar als Teil des Konzepts in [Fachkonzept §14](fachkonzept-anlaufstelle.md#14-glossar). Zugehöriger DSGVO-Wegweiser: [datenschutz.md](datenschutz.md). Pflege-Audit: [Issue #1071](https://github.com/anlaufstelle/app/issues/1071).
>
> **Quelle:** [`docs/fachkonzept-anlaufstelle.md` §14](fachkonzept-anlaufstelle.md#14-glossar) (Domänenbegriffe) + [`en/glossary.md`](en/glossary.md) (Sicherheits-/Technikbegriffe). **Bei Widerspruch gilt der Code.**

---

## Begriffstabelle (A–Z)

| Begriff | Definition |
|---------|------------|
| **Account-Lockout** | Automatische Konto-Sperre nach 10 fehlgeschlagenen Login-Versuchen (seit v0.10.1). Die Sperre wird als `LOGIN_LOCKED`-AuditLog-Eintrag protokolliert; Admins entsperren betroffene Nutzer über die Profilansicht, was einen `LOGIN_UNLOCK`-Eintrag schreibt. Implementierung: [`src/core/services/login_lockout.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/login_lockout.py). |
| **AdminCSPRelaxMiddleware** | Per-Request-CSP-Lockerung, die `'unsafe-eval'` ausschließlich auf `/admin-mgmt/*`-Routen injiziert (seit v0.10.2). Nötig, weil das von django-unfold gebündelte Alpine `new AsyncFunction()` für die Cmd+K-Befehlspalette nutzt. Die globale CSP bleibt strikt; Admin-Routen sind zusätzlich durch MFA-Gate und die Rolle `admin` geschützt. Siehe [`src/core/middleware/admin_csp_relax.py`](https://github.com/anlaufstelle/app/blob/main/src/core/middleware/admin_csp_relax.py). |
| **Alterscluster** | Grobe Altersgruppe einer Person (z.B. U18, 18–26, 27+, unbekannt). Konfigurierbar pro Einrichtung. Dient der Statistik, ohne ein genaues Geburtsdatum zu erfordern. |
| **Anlaufstelle** | Name des Fachsystems. Auch umgangssprachlich für die Einrichtung selbst — der Ort, an dem Menschen vorbeikommen. |
| **Arbeitsinfo** | Sammelbegriff für Hinweise und Aufgaben — operative Einträge, die nicht zur fachlichen Dokumentation gehören. Siehe: WorkItem. |
| **Arbeitszentrale** | Rollenspezifische Startseite unter `/start/` (Refs #920): verdichtete Kacheln über bestehende Daten — variiert je Rolle (Fachkraft, Leitung, Einrichtungs-Admin, Super-Admin). Über die Seitenleiste erreichbar; die Chronik (`/`) bleibt die Standardansicht. |
| **Audit-Trail** | Unveränderliches Protokoll aller sicherheitsrelevanten Aktionen im System: Zugriffe, Änderungen, Löschungen, Login-Versuche. Dient der DSGVO-Compliance und der Nachvollziehbarkeit. Siehe: AuditLog. |
| **AuditLog** | Technischer Begriff für den Audit-Trail. Eine eigene Entität, getrennt von der fachlichen Dokumentation gespeichert. Unveränderlich (Append-Only). |
| **Backup-Codes** | Einmal nutzbare Wiederherstellungscodes für 2FA-Notfälle (seit v0.10.1). Nutzer erhalten beim Aktivieren von TOTP 10 Codes; jeder funktioniert genau einmal und kann am 2FA-Login-Prompt anstelle eines Authenticator-Codes eingegeben werden. Verbrauchte Codes werden invalidiert und via `MFA_BACKUP_CODE_USED` protokolliert. Eigenes Rate-Limit (5/min). Sind alle Codes aufgebraucht, ist ein Admin-Reset der Fallback. |
| **Benannter Zeitfilter** | Ein gespeicherter Arbeitszeitraum mit Label (z.B. „Nachtdienst 21:30–09:00"). Dient als Schnellfilter auf der Startseite und in der Statistik. Reine UI-Konfiguration, keine Datenstruktur. Siehe: TimeFilter. |
| **Case** | Fall — eine Klammer um zusammenhängende Arbeit mit einer Person. Enthält Episoden, Zuständigkeiten und optional Wirkungsziele. |
| **Chronik** | Der zeitliche Verlauf aller Ereignisse, die zu einer Person dokumentiert wurden. Die Chronik ist die primäre Sicht auf eine Person in Anlaufstelle. |
| **ClamAV** | Open-Source-Malware-Scanner, der als Sidecar-Dienst läuft. Alle Datei-Uploads werden vor Verschlüsselung und Speicherung gescannt. Fail-closed: Uploads werden abgelehnt, wenn ClamAV nicht erreichbar ist. |
| **Client** | Person/Klientel im System. Wird unter einem Pseudonym geführt. Hat eine Kontaktstufe, die den Lebenszyklus im System bestimmt. |
| **CSP (Content Security Policy)** | Browser-seitige Sicherheitsrichtlinie, die einschränkt, welche Quellen (Skripte, Styles, Bilder, Frames) die Seite laden darf. Anlaufstelle setzt die Richtlinie zentral in Django via [`django-csp`](https://django-csp.readthedocs.io/) ([`src/anlaufstelle/settings/base.py`](https://github.com/anlaufstelle/app/blob/main/src/anlaufstelle/settings/base.py)). Seit v0.10.2 trägt das globale `script-src` kein `'unsafe-eval'` mehr; einzige Ausnahme ist `/admin-mgmt/*` via `AdminCSPRelaxMiddleware`. |
| **Dexie.js** | Minimaler IndexedDB-Wrapper für den sicheren Offline-Store. Vendored unter `src/static/js/dexie.min.js` (Apache-2.0, v4.2.0). |
| **DocumentType** | Dokumentationstyp — eine konfigurierbare Kategorie von Ereignissen (z.B. „Kontakt", „Krisengespräch", „Spritzentausch"). Definiert Felder, Sensitivität und Löschfrist. |
| **DocumentTypeField** | Zuordnung einer Feldvorlage (FieldTemplate) zu einem Dokumentationstyp (DocumentType). Legt die Reihenfolge der Felder im Formular fest und ermöglicht die Wiederverwendung von Feldvorlagen in mehreren Typen. |
| **Domänenbibliothek** | Vorkonfigurierter Satz von Dokumentationstypen für einen bestimmten Einrichtungstyp (z.B. „Niedrigschwellige Suchthilfe"). Seed-Daten, die bei der Ersteinrichtung eingespielt werden. |
| **DSGVO** | Datenschutz-Grundverordnung — die europäische Verordnung zum Schutz personenbezogener Daten. Zusammen mit dem Sozialdatenschutz (SGB X) der zentrale rechtliche Rahmen für Anlaufstelle. |
| **Einrichtung** | Ein konkreter Standort. Die primäre Scope-Grenze für Mitarbeitende. Alle Entitäten haben einen FK auf Facility. Siehe: Facility. |
| **Episode** | Eine abgrenzbare Phase innerhalb eines Falls: z.B. eine Krisenphase, ein Vermittlungsprozess. |
| **Ereignis** | Der zentrale Baustein der Dokumentation. Ein zeitgestempelter Eintrag, der ein Vorkommnis festhält. Gehört zu einem Dokumentationstyp und optional zu einer Person. Siehe: Event. |
| **Event** | Technischer Begriff für ein Ereignis. Siehe: Ereignis. |
| **Externer Bericht** | Datenschutzfreundlicher Statistikbericht unter `/statistics/external/` (Refs #921): kein Pseudonym-Ranking, K-Anonymitäts-Unterdrückung kleiner Aggregate, Datenschutzprofil-Header; HTML oder JSON. Nur Leitung/Admin; jeder Aufruf wird als `EXPORT` audit-protokolliert. |
| **Facility** | Technischer Begriff für eine Einrichtung/einen Standort. Die primäre Scope-Grenze für Mitarbeitende. Alle Entitäten tragen einen FK auf Facility. Siehe: Einrichtung. |
| **FieldTemplate** | Feldvorlage — definiert ein Feld innerhalb eines Dokumentationstyps: Name, Datentyp, Pflichtfeld, Optionen, Verschlüsselung, Statistik-Zuordnung. |
| **File Vault** | Datei-Anhänge werden verschlüsselt im Ruhezustand gespeichert (AES-GCM). Dateien werden vor der Verschlüsselung von ClamAV gescannt. Downloads laufen über einen zentralen Safe-Download-Helfer mit RFC-5987-Content-Disposition. |
| **Fuzzy-Schwellwert** | Einrichtungsbezogene Ähnlichkeitsschwelle (`Settings.search_trigram_threshold`, 0.0–1.0, Default ~0.3). Niedriger = mehr Treffer, mehr Rauschen. |
| **Fuzzy-Suche** | Tippfehler-tolerante Suche über PostgreSQL-`pg_trgm`-Trigramm-Ähnlichkeit. Findet „Müller" bei der Suche nach „Muller". |
| **Hausverbot** | Betretungsverbot für eine Person in einer Einrichtung, mit Begründung, Gültigkeitszeitraum und Erteilender. In Anlaufstelle als Dokumentationstyp der Kategorie „Administration" abgebildet. |
| **Inbox** | Persönliche Übersicht aller offenen Hinweise und fälligen Aufgaben für die angemeldete Mitarbeitende. Teil der Operations-Ebene. |
| **IndexedDB** | Browser-native Datenbank, die der Offline-Store nutzt. Hält AES-GCM-verschlüsselte Ereignisse, Entwürfe und gecachte Clients. |
| **JSONB** | PostgreSQL-Datentyp für binäres JSON. Wird in Anlaufstelle verwendet, um die Feldwerte eines Ereignisses zusammen mit dem Ereignis zu speichern. Indexierbar (GIN-Index), abfragbar, performant. |
| **K-Anonymisierung** | Auf zwei Arten genutzt: (a) Bucket-Suppression kleiner Aggregate im externen Bericht; (b) der Retention-Pfad (`retention_use_k_anonymization`), der den Client-Record generalisiert statt hart zu löschen, sodass Statistik nach Ablauf der Frist möglich bleibt. Vertiefung unten. Siehe [ADR-023](adr/023-k-anonymization-statistik.md). |
| **k-Anon-Retention** | Der Retention-Löschpfad, der den Client-Record k-anonymisiert statt hart zu löschen. Seit #1094 tilgt der Retention-Bridge-Layer in diesem Pfad zusätzlich den Fall-/Episoden-/Aufgaben-Freitext (dieselben Helfer wie der Hard-Pfad); die Primitive `k_anonymize_client` selbst bleibt client-only. Vertiefung unten. |
| **Kontaktstufe** | Dreistufiges Modell, das den Identifizierungsgrad einer Person im System beschreibt: anonym (nur Zählung), identifiziert (Pseudonym), qualifiziert (Beratungsprozess). Bestimmt Zugriffsrechte, zulässige Dokumentationstypen und Löschfristen. |
| **Legal Hold** | Einfrier-Marker pro Entität, der einzelne Datensätze von der automatisierten Retention-Löschung ausnimmt, solange ein laufendes Verfahren die Daten bindet. Vertiefung unten. |
| **Materialized View** | Vor-aggregierte PostgreSQL-View (`core_statistics_event_flat`), die das Statistik-Dashboard speist, damit Berichte nicht bei jeder Anfrage die gesamte Ereignistabelle neu scannen (seit v0.10.0, #544). Wird periodisch per Management-Command aufgefrischt, idealerweise `CONCURRENTLY` (erfordert den Unique-Index aus der Migration). |
| **Milestone** | Meilenstein — ein konkreter Schritt auf dem Weg zu einem Wirkungsziel. |
| **Offline-Modus (M6A)** | Client-seitig verschlüsselte Offline-Erfassung für Streetwork. Nutzt AES-GCM-256 mit einem PBKDF2-abgeleiteten (600 000 Iterationen, SHA-256) non-extractable `CryptoKey` in einer eigenen IndexedDB (`anlaufstelle-crypto`); die rohen Schlüssel-Bytes sind nie exportierbar. Wird bei Logout und bei Session-Leerlauf gelöscht (Idle-Key-Wipe), nicht beim Schließen des Tabs. Lesen + Schreib-Queue sind der akzeptierte Produktiv-Umfang; der In-App-Offline-**Edit**-Einstieg ist deferred (#1111). Vertiefung unten. Siehe [ADR-022](adr/022-offline-snapshot-keys.md). |
| **Optimistic Locking** | Nebenläufigkeitskontrolle über ein `version`-Feld pro Datensatz. Verhindert stilles Überschreiben bei Client, Case, WorkItem, Settings und Event. Konfligierende Speichervorgänge liefern einen Fehler, der ein Neuladen durch den Nutzer erfordert. |
| **Organisation** | Träger — die übergeordnete Hülse, oberste Ebene der Hierarchie. In v1.0 existiert genau eine Organisation, automatisch angelegt und in der UI verborgen. Dient als vorbereiteter Scope für künftige Mehr-Träger-Unterstützung. Reine Branding-Hülse ([ADR-018](adr/018-rollenmodell-superadmin.md), Variante b1); keine Cross-Facility-Sichtbarkeit über die Organisation. |
| **Outcome** | Wirkung — das Ergebnis der Arbeit mit einer Person. Nicht die Aktivität („347 Kontakte"), sondern die Veränderung („stabile Wohnsituation erreicht"). |
| **OutcomeGoal** | Wirkungsziel — was durch die Arbeit erreicht werden soll. Zugeordnet zu einem Fall. |
| **Personenbezogene Daten (PII)** | Informationen, die eine natürliche Person direkt (Name, Adresse, Klient-ID) oder indirekt über *Quasi-Identifikatoren* (Alter, Region, Anliegen in Kombination) identifizierbar machen — Art. 4 Nr. 1 DSGVO. Gesundheits-/Sozialdaten sind *besondere Kategorien* (Art. 9). Höchstes Leak-Risiko: Freitext. Vertiefung unten. |
| **Pseudonym** | Vom Team vergebener Name für eine Person im System. Primärer Identifikator in Anlaufstelle. Die Zuordnung zum realen Namen existiert nur im Wissen der Mitarbeitenden, nicht im System. |
| **Pseudonymisierung** | Ersetzt direkte Identifikatoren durch ein Pseudonym; der Einzeldatensatz bleibt bestehen und ist mit Zusatzwissen re-identifizierbar. Schwächer als Anonymisierung. Abgrenzung zur K-Anonymität siehe Vertiefung unten. |
| **PWA (Progressive Web App)** | Installierbare Web-App für Smartphones und Desktops. Bietet Homescreen-Installation, eine app-artige Shell und Offline-Verhalten, koordiniert durch den Service Worker. In Anlaufstelle für den Streetwork-Schnellerfassungsfluss genutzt. |
| **Quasi-Identifikator** | Ein Merkmal, das einzeln nicht, in Kombination mit anderen aber identifiziert. Die Äquivalenzklasse der K-Anonymisierung ist `(facility, age_cluster, contact_stage)` ([ADR-023](adr/023-k-anonymization-statistik.md)). Vertiefung unten. |
| **Quick Template** | Vorausgefüllte Ereignisvorlage, gepflegt von Admins. Wird per Button-Klick auf „Neuer Kontakt" angewendet; füllt nur leere Felder; selbstheilend gegen deaktivierte Select-Optionen. |
| **Retention Proposal** | Einzelner Lösch-/Anonymisierungs-Vorschlag, der auf dem Retention-Dashboard erscheint. Kann gebündelt freigegeben, zurückgestellt oder abgelehnt werden. |
| **Role** | Rolle — bestimmt, welche Aktionen ein User ausführen darf. Fünf Rollen (Refs #867, [ADR-018](adr/018-rollenmodell-superadmin.md)): Super-Admin (`super_admin`, installationsweite Systemsteuerung, keine Einrichtung), Anwendungsbetreuung (`facility_admin`, Vollzugriff innerhalb einer Einrichtung), Leitung (`lead`, fachliche Leitung), Fachkraft (`staff`), Assistenz (`assistant`). Nur `super_admin` arbeitet facility-übergreifend. |
| **Row Level Security (RLS)** | PostgreSQL-Defense-in-Depth auf allen facility-gescopten Tabellen (23 Stand v0.10.2). Policies lesen die Session-Variable `app.current_facility_id` und schließen fail-closed, wenn sie NULL ist. Die Variable wird pro Request session-weit gesetzt (nicht via `SET LOCAL`) und für anonyme Requests explizit geleert. Siehe [`src/core/migrations/0047_postgres_rls_setup.py`](https://github.com/anlaufstelle/app/blob/main/src/core/migrations/0047_postgres_rls_setup.py) und [`src/tests/test_rls.py`](https://github.com/anlaufstelle/app/blob/main/src/tests/test_rls.py). |
| **Scope** | Sichtbarkeitsbereich. Bestimmt, welche Daten für einen User zugänglich sind — abhängig von Einrichtung, Rolle und Kontaktstufe. |
| **Sensitivität** | Einstufung eines Dokumentationstyps oder Feldes hinsichtlich des Schutzbedarfs. Steuert, welche Rollen auf Feldwerte zugreifen dürfen. Konfigurierbar pro Dokumentationstyp und pro Feld (`FieldTemplate.sensitivity`). Unabhängig von der Feldverschlüsselung (`is_encrypted`). |
| **Service Worker** | Browser-Skript, das Offline-Caching, Queue-Replay und PWA-Verhalten koordiniert. Siehe `src/static/js/sw.js`. |
| **Soft-Delete-Strategie** | Eine der vier Retention-Strategien statt Hard-Delete: `anonymous`, `identified`, `qualified`, `document_type` ([ADR-021](adr/021-retention-modell.md)). Vertiefung unten. |
| **Speicherbegrenzung** | Grundsatz aus Art. 5(1)(e) DSGVO: Personendaten nicht länger als für den Zweck erforderlich aufbewahren. Umgesetzt über das Retention-Modell ([ADR-021](adr/021-retention-modell.md)). Vertiefung unten. |
| **TimeFilter** | Technischer Begriff für einen benannten Zeitfilter. Gehört zu einer Einrichtung und definiert ein Zeitfenster (Startzeit, Endzeit) mit einem Label. |
| **Token-Einladung** | Token-basierter Einladungsfluss, der das frühere Klartext-Initialpasswort ablöst. Admin legt einen User an, der User erhält eine E-Mail mit einem Einmal-Link (7 Tage gültig) und setzt sein eigenes Passwort. |
| **TOTP / 2FA** | Zeitbasierte Einmalpasswörter via `django-otp`. Erzwingbar pro Nutzer (`User.mfa_required`) oder einrichtungsweit (`Settings.mfa_enforced_facility_wide`). Codes 30 s gültig. |
| **User** | Mitarbeitende — eine Person, die mit dem System arbeitet. Hat Zugangsdaten und eine Rollenzuweisung in einer Einrichtung. |
| **WorkItem** | Arbeitsinfo — ein operativer Eintrag (Hinweis oder Aufgabe) mit eigenem Lebenszyklus und optionaler Priorität. Getrennt von der fachlichen Dokumentation. |
| **Zeitstrom** | Der chronologische Fluss aller Ereignisse, ungefiltert oder gefiltert nach Zeitraum, Person oder Dokumentationstyp. Die Grundmetapher der Dokumentation in Anlaufstelle. |

---

## K-Anonymität im Detail

**K-Anonymität** ist ein etabliertes Datenschutzprinzip gegen Re-Identifikation in
Statistiken und Berichten. Eine Auswertung ist *k-anonym*, wenn sich jede Person in
Bezug auf die ausgewerteten Merkmale von mindestens **k − 1** anderen Personen nicht
unterscheiden lässt — jede Merkmalskombination kommt also **mindestens k-mal** vor.

**Alltagsbeispiel (k = 5):** Eine Statistik gruppiert Kontakte nach *Alterscluster*
und *Geschlecht*. Gibt es in einer Einrichtung nur **eine** Person der Kombination
„unter 18 / divers", würde diese Zeile faktisch eine einzelne Person offenlegen
obwohl die Statistik „anonym" wirkt. Bei k = 5 wird eine solche Gruppe mit weniger
als 5 Personen **unterdrückt** (kein Zahlenwert, sondern „—" bzw. `suppressed`),
sodass aus dem Aggregat niemand zurückverfolgt werden kann.

**In Anlaufstelle:**

- Der Schwellwert ist pro Einrichtung über das Setting
 [`k_anonymity_threshold`](https://github.com/anlaufstelle/app/blob/main/src/core/models/settings.py)
 konfigurierbar (**Default 5**).
- Datenschutzfreundliche externe Berichte
 ([`core/services/dashboard/external_report.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/dashboard/external_report.py))
 wenden die Schwelle auf alle Aggregate an und entfernen Pseudonym-Rankings vollständig.
- Optional ersetzt der Retention-Löschpfad den Hard-Delete durch
 K-Anonymisierung des Client-Records (Setting `retention_use_k_anonymization`).
 **Beide Pfade tilgen seit #1094 dieselbe Freitext-Kaskade** auf
 Fälle/Episoden/Aufgaben (im Retention-Bridge-Layer); die Primitive
 `k_anonymize_client` selbst bleibt client-only. Siehe **k-Anon-Retention** unten.

Abgrenzung: **Pseudonymisierung** ersetzt direkte Identifikatoren durch ein Pseudonym
(Einzeldatensatz bleibt bestehen); **K-Anonymität** schützt zusätzlich vor
Re-Identifikation über *Kombinationen* indirekter Merkmale in Aggregaten.

## Datenschutz-Begriffe

**Personenbezogene Daten / PII** — Informationen, die eine natürliche Person direkt
(Name, Adresse, Klient-ID) oder indirekt über *Quasi-Identifikatoren* (Alter, Region,
Anliegen in Kombination) identifizierbar machen (Art. 4 Nr. 1 DSGVO). Gesundheits-/
Sozialdaten sind *besondere Kategorien* (Art. 9). Höchstes Leak-Risiko: Freitext.

**Pseudonymisierung** — ersetzt direkte Identifikatoren durch ein Pseudonym; der
Einzeldatensatz bleibt bestehen und ist mit Zusatzwissen re-identifizierbar. Schwächer
als Anonymisierung. Abgrenzung zu **K-Anonymität** siehe oben.

**Speicherbegrenzung** — Grundsatz aus Art. 5(1)(e) DSGVO: Personendaten nicht länger
als für den Zweck erforderlich aufbewahren. In Anlaufstelle über das Retention-Modell ([ADR-021](adr/021-retention-modell.md))
umgesetzt.

**Soft-Delete-Strategien** — die vier Retention-Strategien statt Hard-Delete:
`anonymous` (sofort nach Frist), `identified` (Anonymisierung nach Frist),
`qualified` (Freigabe via RetentionProposal nötig), `document_type` (Frist pro
Dokumenttyp). Details: [ADR-021](adr/021-retention-modell.md).

**Legal Hold** — Einfrier-Marker pro Entität: hält jede Soft-Delete- und
Anonymisierungs-Pipeline an, solange ein laufendes Verfahren die Daten bindet
([ADR-021](adr/021-retention-modell.md)).

**k-Anon-Retention** — Spielart des Retention-Löschpfads: am Ende der Frist wird der
Client-Record **k-anonymisiert** (Setting `retention_use_k_anonymization`, Schwelle
`k_anonymity_threshold`) statt hart gelöscht — bleibt statistisch auswertbar, ist aber
nicht mehr re-identifizierbar. Seit #1094 tilgt der Retention-Bridge-Layer im
K-Anon-Zweig zusätzlich den Fall-/Episoden-/Aufgaben-Freitext (dieselben Helfer wie der
Hard-Pfad); die Primitive `k_anonymize_client` bleibt bewusst client-only. Abzugrenzen
von der **Bucket-Suppression** im externen Bericht. Details: [ADR-021](adr/021-retention-modell.md),
[ADR-023](adr/023-k-anonymization-statistik.md).

**Quasi-Identifikator / Äquivalenzklasse** — Merkmale, die einzeln nicht, in
Kombination aber identifizieren. Die Äquivalenzklasse der K-Anonymisierung ist
`(facility, age_cluster, contact_stage)` ([ADR-023](adr/023-k-anonymization-statistik.md)).

**Offline-Snapshot-Keys** — die client-seitige Verschlüsselung des Offline-Lesecaches
(Streetwork-Modus, § 16 Fachkonzept). Pro Gerät wird beim Login aus dem Nutzerpasswort via
PBKDF2 (600 000 Iterationen, SHA-256) ein **non-extractable** AES-GCM-256-`CryptoKey`
abgeleitet und in einer eigenen IndexedDB (`anlaufstelle-crypto`) gehalten; die rohen
Schlüssel-Bytes sind nicht exportierbar, ein **Idle-Key-Wipe** verwirft ihn bei
Session-Ablauf. Ein gestohlenes Gerät ohne aktive Session liefert nur Chiffretext.
Server-Bundles sind gedeckelt (50 Ereignisse, 90 Tage, TTL 48 h) und wenden Sichtbarkeit
vor der Serialisierung an. Details: [ADR-022](adr/022-offline-snapshot-keys.md).

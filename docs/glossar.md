
# Glossar — Anlaufstelle (Deutsch)

> **Deutsches Fachglossar.** Diese Datei führt die domänen-, sicherheits- und datenschutz-spezifischen Begriffe von Anlaufstelle alphabetisch auf und vertieft anschließend die **datenschutz- und compliance-relevanten** Begriffe (K-Anonymität, Retention, Pseudonymisierung, Legal Hold, Offline-Snapshot-Keys …). Die **bilinguale (DE↔EN)** Gesamtliste mit identischer Begriffsmenge steht in [`en/glossary.md`](en/glossary.md); das Domänen-Glossar als Teil des Konzepts in [Fachkonzept §14](fachkonzept-anlaufstelle.md#14-glossar). Zugehöriger DSGVO-Wegweiser: [datenschutz.md](datenschutz.md). Pflege-Audit: Issue #1071.
>
> **Quelle:** Datenmodelle, Views, Services und Browser-Code von Anlaufstelle; das [Fachkonzept §14](fachkonzept-anlaufstelle.md#14-glossar) ergänzt die Domänenbegriffe. Dieses deutsche Glossar ist die Quelle des englischen Spiegels. **Bei Widerspruch gilt der Code.**

---

## Begriffstabelle (A–Z)

| Begriff | Definition |
|---------|------------|
| **Account-Lockout** | Automatische Konto-Sperre nach 10 fehlgeschlagenen Login-Versuchen innerhalb eines gleitenden 15-Minuten-Fensters, gezählt je Paar aus Konto und Quell-IP-Adresse. Der Sperrzustand wird nicht separat gespeichert, sondern aus den `LOGIN_FAILED`-AuditLog-Einträgen abgeleitet; die Sperre endet daher von selbst, sobald weniger als 10 gezählte Fehlversuche im Fenster liegen — ohne neue Fehlversuche spätestens 15 Minuten nach dem letzten. Vom Ratelimit abgewiesene Anfragen (HTTP 429) werden nicht gezählt. Ein Anmeldeversuch mit richtigem Passwort während der Sperre wird als `LOGIN_FAILED` mit `reason: "locked"` protokolliert und als Formularfehler angezeigt. `super_admin` ist von dieser Kontosperre ausgenommen; die Ratelimits der Anmeldung (5/min je IP-Adresse, 10/h je Benutzername, darüber HTTP 429) und 2FA bleiben davon unberührt. Entsperren per Selbsthilfe auf der Login-Seite oder durch die Administration (Verwaltung → Zugänge, Systembereich → Sperrkonten, CLI `unlock`) schreibt einen `LOGIN_UNLOCK`-Eintrag; frühere `LOGIN_FAILED`-Einträge zählen danach nicht mehr mit. Implementierung: [`src/core/services/security/login_lockout.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/security/login_lockout.py). |
| **AdminCSPRelaxMiddleware** | CSP-Lockerung für HTML-Antworten unter `/admin-mgmt/`: ergänzt `'unsafe-eval'` in `script-src` für das von django-unfold gebündelte Alpine und `'unsafe-inline'` in `style-src` (ohne Hash-/Nonce-Tokens). Die globale CSP bleibt strikt. Der Admin-Zugang erfordert Systemadministration (`super_admin`) oder Anwendungsbetreuung (`facility_admin`) und bei aktiviertem Sudo-Modus eine bestätigte Sitzung; die Admin-Login-Seite ist anonym erreichbar. Siehe [`src/core/middleware/admin_csp_relax.py`](https://github.com/anlaufstelle/app/blob/main/src/core/middleware/admin_csp_relax.py). |
| **Altersgruppe** | Grobe Altersgruppe einer Person (Unter 18, 18–26, 27+ oder Unbekannt). Der Katalog ist systemweit fest und nicht pro Einrichtung konfigurierbar ([ADR-047](adr/047-altersgruppen-systemweit-fix.md)). Dient der Statistik, ohne ein genaues Geburtsdatum zu erfordern. Der Codename des Feldes bleibt `age_cluster` (Sprachleitlinie: Code außerhalb). |
| **Anlaufstelle** | Name des Fachsystems. Auch umgangssprachlich für die Einrichtung selbst — der Ort, an dem Menschen vorbeikommen. |
| **Arbeitsinfo** | Sammelbegriff für Hinweise und Aufgaben — operative Einträge, die nicht zur fachlichen Dokumentation gehören. Siehe: WorkItem. |
| **Arbeitszentrale** | Übersichtsseite der Leitung unter `/start/` mit Kacheln für Löschanträge, Löschvorschläge und Statistik. Der Sidebar-Link erscheint nur für die Leitung. Fachkraft und Assistenz werden zum Zeitstrom (`/`) weitergeleitet, Anwendungsbetreuung zur Verwaltung (`/verwaltung/`), Systemadministration zum Systembereich (`/system/`). Der Zeitstrom (`/`) bleibt die fachliche Standardansicht. |
| **Audit-Trail** | Unveränderliches Protokoll aller sicherheitsrelevanten Aktionen im System: Zugriffe, Änderungen, Löschungen, Login-Versuche. Dient der DSGVO-Compliance und der Nachvollziehbarkeit. Siehe: AuditLog. |
| **AuditLog** | Technischer Begriff für den Audit-Trail. Eine eigene Entität, getrennt von der fachlichen Dokumentation gespeichert. Unveränderlich (Append-Only). |
| **Backup-Codes** | Einmal nutzbare Wiederherstellungscodes für 2FA-Notfälle. Nutzer erhalten beim Aktivieren von TOTP 10 Codes; jeder funktioniert genau einmal und kann am 2FA-Login-Prompt anstelle eines Authenticator-Codes eingegeben werden. Verbrauchte Codes werden invalidiert und via `BACKUP_CODES_USED` protokolliert. Eigenes Rate-Limit (5/min). Gespeichert wird nur ein gekürzter SHA-256-Hash je Code. Sind alle Codes aufgebraucht, ist das Zurücksetzen des zweiten Faktors durch die Administration (`MFA_RESET_BY_ADMIN`) der Fallback. |
| **Benannter Zeitfilter** | Ein gespeicherter Arbeitszeitraum mit Bezeichnung (z. B. „Nachtdienst 21:30–09:00“), Start- und Endzeit. Das einrichtungsbezogene Modell `TimeFilter` dient als Schnellfilter im Zeitstrom und in der Statistik. |
| **Betreiber-Empfehlung** | Empfehlung für den Betrieb einer Einrichtung, die nicht paketfähig ist (z.B. Aufbewahrungsfristen, k-Schwelle, Zeitfenster, QuickTemplates, Rollen). Steht in der jeweiligen Paket-Doku (`docs/packages-<domain>.md`) statt im Paket selbst. |
| **Case** | Fall — eine Klammer um zusammenhängende Arbeit mit einer Person. Enthält Episoden, Zuständigkeiten und optional Wirkungsziele. |
| **Chronik** | Der zeitliche Verlauf aller Ereignisse, die zu einer Person dokumentiert wurden. Die Chronik ist die primäre Sicht auf eine Person in Anlaufstelle. |
| **ClamAV** | Open-Source-Malware-Scanner, der als Sidecar-Dienst läuft. Alle Datei-Uploads werden vor Verschlüsselung und Speicherung gescannt. Fail-closed: Uploads werden abgelehnt, wenn ClamAV nicht erreichbar ist. |
| **Client** | Technischer Begriff für eine Person im System. Wird unter einem Pseudonym geführt. Hat eine Kontaktstufe, die den Lebenszyklus im System bestimmt. Siehe: Person. |
| **CSP (Content Security Policy)** | Browser-seitige Sicherheitsrichtlinie, die einschränkt, welche Quellen (Skripte, Styles, Bilder, Frames) die Seite laden darf. Anlaufstelle setzt die Richtlinie zentral in Django via [`django-csp`](https://django-csp.readthedocs.io/) ([`src/anlaufstelle/settings/base.py`](https://github.com/anlaufstelle/app/blob/main/src/anlaufstelle/settings/base.py)). Das globale `script-src` trägt kein `'unsafe-eval'`; einzige Ausnahme ist `/admin-mgmt/*` via `AdminCSPRelaxMiddleware`. |
| **Dexie.js** | Minimaler IndexedDB-Wrapper für den sicheren Offline-Store. Vendored unter `src/static/js/dexie.min.js` (Apache-2.0, v4.4.6). |
| **DocumentType** | Dokumentationstyp — eine konfigurierbare Kategorie von Ereignissen (z.B. „Kontakt", „Krisengespräch", „Spritzentausch"). Definiert Felder, Sensitivität und Löschfrist. |
| **DocumentTypeField** | Zuordnung einer Feldvorlage (FieldTemplate) zu einem Dokumentationstyp (DocumentType). Legt die Reihenfolge der Felder im Formular fest und ermöglicht die Wiederverwendung von Feldvorlagen in mehreren Typen. |
| **Domänen-Paket** | Die technische Realisierung der Domänenbibliothek: ein versionierbarer YAML-Katalog vorkonfigurierter Dokumentationstypen und Felder für einen bestimmten Einrichtungstyp, eingespielt über `setup_facility --package <name>` bzw. `manage.py load_package`. Siehe: Domänenbibliothek, [docs/packages.md](packages.md). |
| **Domänenbibliothek** | Vorkonfigurierter Satz von Dokumentationstypen für einen bestimmten Einrichtungstyp (z.B. „Niedrigschwellige Suchthilfe"). Seed-Daten, die bei der Ersteinrichtung eingespielt werden. Technisch realisiert als Domänen-Paket. |
| **DSGVO** | Datenschutz-Grundverordnung — die europäische Verordnung zum Schutz personenbezogener Daten. Zusammen mit dem Sozialdatenschutz (SGB X) der zentrale rechtliche Rahmen für Anlaufstelle. |
| **Einrichtung** | Ein konkreter Standort. Die primäre Scope-Grenze für Mitarbeitende. Alle Entitäten haben einen FK auf Facility. Siehe: Facility. |
| **Episode** | Eine abgrenzbare Phase innerhalb eines Falls: z.B. eine Krisenphase, ein Vermittlungsprozess. |
| **Ereignis** | Der zentrale Baustein der Dokumentation. Ein zeitgestempelter Eintrag, der ein Vorkommnis festhält. Gehört zu einem Dokumentationstyp und optional zu einer Person. Siehe: Event. |
| **Event** | Technischer Begriff für ein Ereignis. Siehe: Ereignis. |
| **Externer Bericht** | Datenschutzfreundlicher Statistikbericht unter `/statistics/external/` (Refs #921): kein Pseudonym-Ranking, K-Anonymitäts-Unterdrückung kleiner Aggregate, Datenschutzprofil-Header; HTML oder JSON. Nur Leitung/Admin; jeder Aufruf wird als `EXPORT` audit-protokolliert. |
| **Facility** | Technischer Begriff für eine Einrichtung/einen Standort. Die primäre Scope-Grenze für Mitarbeitende. Alle Entitäten tragen einen FK auf Facility. Siehe: Einrichtung. |
| **FieldTemplate** | Feldvorlage — definiert ein Feld innerhalb eines Dokumentationstyps: Name, Datentyp, Pflichtfeld, Optionen, Verschlüsselung, Statistik-Zuordnung. |
| **File Vault** | Datei-Anhänge werden verschlüsselt im Ruhezustand gespeichert (Fernet/AES-128). Dateien werden vor der Verschlüsselung von ClamAV gescannt. Downloads laufen über einen zentralen Safe-Download-Helfer mit RFC-5987-Content-Disposition. |
| **Fuzzy-Schwellwert** | Einrichtungsbezogene Ähnlichkeitsschwelle (`Settings.search_trigram_threshold`, 0.0–1.0, Default ~0.3). Niedriger = mehr Treffer, mehr Rauschen. |
| **Fuzzy-Suche** | Tippfehler-tolerante Suche über PostgreSQL-`pg_trgm`-Trigramm-Ähnlichkeit. Findet „Müller" bei der Suche nach „Muller". |
| **Hausverbot** | Betretungsverbot für eine Person in einer Einrichtung, mit Begründung, Gültigkeitszeitraum und Erteilender. In Anlaufstelle als Dokumentationstyp mit Systemtyp `ban` abgebildet. |
| **Inbox** | Persönliche Übersicht aller offenen Hinweise und fälligen Aufgaben für die angemeldete Mitarbeitende. Teil der Operations-Ebene. |
| **IndexedDB** | Browser-native Datenbank, die der Offline-Store nutzt. Hält AES-GCM-verschlüsselte Ereignisse, Entwürfe und gecachte Clients. |
| **JSONB** | PostgreSQL-Datentyp für binäres JSON. Wird in Anlaufstelle verwendet, um die Feldwerte eines Ereignisses zusammen mit dem Ereignis zu speichern. Indexierbar (GIN-Index), abfragbar, performant. |
| **K-Anonymisierung** | Auf zwei Arten genutzt: (a) Bucket-Suppression kleiner Aggregate im externen Bericht; (b) der Retention-Pfad (`retention_use_k_anonymization`), der die Personenzeile bei ausreichender Gruppengröße generalisiert und die Altersgruppe für Statistik erhält. Vertiefung unten. Siehe [ADR-023](adr/023-k-anonymization-statistik.md). |
| **k-Anon-Retention** | Der Retention-Pfad, der die Personenzeile k-anonymisiert. Bei aktivierter Option und mindestens k Personen in der Äquivalenzklasse bleibt die Altersgruppe erhalten; andernfalls erfolgt die reguläre Anonymisierung. Beide Pfade redigieren die zugehörige Freitext-Kaskade, der K-Anon-Pfad im Bridge-Layer. Die Primitive `k_anonymize_client` selbst verändert nur die Personenzeile. Vertiefung unten. |
| **Kontaktstufe** | Wie viel über eine Person im System bekannt ist. Das Datenmodell kennt zwei Kontaktstufen (`Client.ContactStage`): **Nur Pseudonym** (Wert `identified` — nur das Pseudonym ist hinterlegt) und **Mit Zusatzangaben** (Wert `qualified` — zusätzliche persönliche Angaben, Sichtbarkeit nach Rolle und Sensitivität; protokolliert wird das Öffnen der Detailseite, nicht jeder Zugriff auf die Angaben — das Bearbeiten-Formular zeigt sie ohne Eintrag). Die Werte bleiben `identified`/`qualified` und tragen API, Export und CSV-Datenvertrag. Bestimmt zulässige Dokumentationstypen, Löschwege und Löschfristen; die Sichtbarkeit richtet sich nach Rolle und Sensitivität. Ein rein anonymer Kontakt (nur Zählung) ist keine Kontaktstufe, sondern ein personenloses Ereignis (`Event.is_anonymous`). |
| **Legal Hold** | Eigener Aufbewahrungssperren-Eintrag auf Ereignis, Person oder Fall, kein Flag am Datensatz. Nimmt das Ziel und bei Person/Fall auch seine Ereignisse von automatischer Retention-Löschung und Anonymisierung aus, solange die Sperre aktiv ist, etwa für ein laufendes Verfahren. Vertiefung unten. |
| **Materialized View** | Vor-aggregierte PostgreSQL-View (`core_statistics_event_flat`), die das Statistik-Dashboard speist, damit Berichte nicht bei jeder Anfrage die gesamte Ereignistabelle neu scannen (Refs #544). Wird periodisch per Management-Command aufgefrischt, idealerweise `CONCURRENTLY` (erfordert den Unique-Index aus der Migration). |
| **Milestone** | Meilenstein — ein konkreter Schritt auf dem Weg zu einem Wirkungsziel. |
| **Offline-Modus (M6A)** | Client-seitig verschlüsselte Offline-Erfassung für Streetwork. Nutzt AES-GCM-256 mit einem PBKDF2-abgeleiteten (600 000 Iterationen, SHA-256) non-extractable `CryptoKey` in einer eigenen IndexedDB (`anlaufstelle-crypto`); die rohen Schlüssel-Bytes sind nie exportierbar. Wird bei Logout und Passwortwechsel gelöscht. Nach Session-Leerlauf entzieht die App beim nächsten Ausführen den Schlüssel; ungesendete Arbeit bleibt verschlüsselt erhalten (Sperre bis zum erneuten Login), sonst wird auch der Offline-Speicher gelöscht. Rollenentzug und Deaktivierung rotieren den Salt serverseitig: Nach dem nächsten Login ist der alte Bestand nicht mehr entschlüsselbar. Schließen des Tabs und Geräteneustart löschen nichts. Innerhalb der Leerlaufzeit lässt sich die App offline ohne erneuten Login öffnen. Lesen, Schreib-Queue **und** In-App-Offline-**Edit** (inkl. Konflikt-/Dead-Letter-Ansicht unter `/offline/conflicts/`) sind der akzeptierte Produktiv-Umfang. Vertiefung unten. Siehe [ADR-022](adr/022-offline-snapshot-keys.md). |
| **Optimistic Locking** | Nebenläufigkeitskontrolle anhand von `updated_at`. Verhindert stilles Überschreiben bei Client, Case, WorkItem, Settings und Event. Ein veralteter Zeitstempel führt zu einem Konflikt; laden Sie den aktuellen Stand und tragen Sie Ihre Änderungen erneut ein. |
| **Organisation** | Träger — die übergeordnete Hülse, oberste Ebene der Hierarchie. Es existiert genau eine Organisation, automatisch angelegt und in der UI verborgen. Dient als vorbereiteter Scope für künftige Mehr-Träger-Unterstützung. Reine Branding-Hülse ([ADR-018](adr/018-rollenmodell-superadmin.md), Variante b1); keine Cross-Facility-Sichtbarkeit über die Organisation. |
| **Outcome** | Wirkung — das Ergebnis der Arbeit mit einer Person. Nicht die Aktivität („347 Kontakte"), sondern die Veränderung („stabile Wohnsituation erreicht"). |
| **OutcomeGoal** | Wirkungsziel — was durch die Arbeit erreicht werden soll. Zugeordnet zu einem Fall. |
| **Personenbezogene Daten (PII)** | Informationen, die eine natürliche Person direkt (Name, Adresse, Klient-ID) oder indirekt über *Quasi-Identifikatoren* (Alter, Region, Anliegen in Kombination) identifizierbar machen — Art. 4 Nr. 1 DSGVO. Gesundheits-/Sozialdaten sind *besondere Kategorien* (Art. 9). Höchstes Leak-Risiko: Freitext. Vertiefung unten. |
| **Pseudonym** | Vom Team vergebener Name für eine Person im System. Primärer Identifikator in Anlaufstelle. Vorgesehen ist, dass die Zuordnung zum realen Namen im Wissen der Mitarbeitenden bleibt; ein Pflichtfeld für Klarnamen gibt es nicht. Technisch verhindert das System Klarnamen nicht — Pseudonym, Notizen, Freitextfelder und Anhänge enthalten, was das Team einträgt. |
| **Pseudonymisierung** | Ersetzt direkte Identifikatoren durch ein Pseudonym; der Einzeldatensatz bleibt bestehen und ist mit Zusatzwissen re-identifizierbar. Schwächer als Anonymisierung. Abgrenzung zur K-Anonymität siehe Vertiefung unten. |
| **PWA (Progressive Web App)** | Installierbare Web-App für Smartphones und Desktops. Bietet Homescreen-Installation, eine app-artige Shell und Offline-Verhalten, koordiniert durch den Service Worker. In Anlaufstelle für den Streetwork-Schnellerfassungsfluss genutzt. |
| **Quasi-Identifikator** | Ein Merkmal, das einzeln nicht, in Kombination mit anderen aber identifiziert. Die Äquivalenzklasse der K-Anonymisierung ist `(facility, age_cluster, contact_stage)` ([ADR-023](adr/023-k-anonymization-statistik.md)). Vertiefung unten. |
| **Quick Template** | Vorausgefüllte Ereignisvorlage, gepflegt von Admins. Wird per Button-Klick auf „Kontakt dokumentieren" angewendet; füllt nur leere Felder; selbstheilend gegen deaktivierte Select-Optionen. |
| **Quick-Exit** | Schnell-verlassen-Aktion in der Kopfleiste jeder eingeloggten Seite (Knopf + Tastenkürzel `Strg+Umschalt+X` / `Umschalt+Esc`): verdeckt den Inhalt sofort, beendet die Sitzung und öffnet eine neutrale, von der Einrichtung konfigurierte Seite. Davon getrennt: der optionale Sichtschutz (Facility-Einstellung) — verbirgt den Inhalt bei Fenster- oder Tab-Wechsel hinter einer neutralen Fläche, bis eine Taste oder ein Klick sie freigibt. Kein Tarnmodus, keine Umbenennung der Oberfläche. Datenschutz-Begriff, siehe [datenschutz.md](datenschutz.md). |
| **Retention Proposal** | Einzelner Lösch-/Anonymisierungs-Vorschlag, der auf dem Retention-Dashboard erscheint. Kann gebündelt freigegeben, zurückgestellt oder abgelehnt werden. |
| **Role** | Rolle — bestimmt, welche Aktionen ein User ausführen darf. Fünf Rollen: Systemadministration (`super_admin`, installationsweite Systemsteuerung, keine Einrichtung), Anwendungsbetreuung (`facility_admin`, Vollzugriff innerhalb einer Einrichtung), Leitung (`lead`, fachliche Leitung), Fachkraft (`staff`), Assistenz (`assistant`). Nur die Systemadministration arbeitet einrichtungsübergreifend. |
| **Row Level Security (RLS)** | PostgreSQL-Schutz auf einrichtungsbezogenen Tabellen. Policies lesen `app.current_facility_id` und schließen ohne gesetzte Einrichtung für normale Nutzer fail-closed. Die Systemadministration wird über `app.is_super_admin` einrichtungsübergreifend zugelassen. Beide Variablen werden pro Request sitzungsweit gesetzt (nicht via `SET LOCAL`) und für anonyme Requests geleert. Siehe [`src/core/middleware/facility_scope.py`](https://github.com/anlaufstelle/app/blob/main/src/core/middleware/facility_scope.py) und [`src/tests/test_rls.py`](https://github.com/anlaufstelle/app/blob/main/src/tests/test_rls.py). |
| **Scope** | Sichtbarkeitsbereich. Welche Daten für einen User zugänglich sind, hängt von Einrichtung, Rolle und Sensitivität ab. Die Kontaktstufe beeinflusst zulässige Dokumentationstypen und Löschwege. |
| **Sensitivität** | Einstufung eines Dokumentationstyps oder Feldes hinsichtlich des Schutzbedarfs. Steuert, welche Rollen auf Feldwerte zugreifen dürfen. Konfigurierbar pro Dokumentationstyp und pro Feld (`FieldTemplate.sensitivity`). Unabhängig von der Feldverschlüsselung (`is_encrypted`). |
| **Service Worker** | Browser-Skript, das Offline-Caching, Queue-Replay und PWA-Verhalten koordiniert. Siehe `src/static/js/sw.js`. |
| **Soft-Delete-Strategie** | Eine der vier Strategien für automatische Aufbewahrung und Soft-Delete: `anonymous`, `identified`, `qualified`, `document_type` ([ADR-021](adr/021-retention-modell.md)). Vertiefung unten. |
| **Speicherbegrenzung** | Grundsatz aus Art. 5(1)(e) DSGVO: Personendaten nicht länger als für den Zweck erforderlich aufbewahren. Umgesetzt über das Retention-Modell ([ADR-021](adr/021-retention-modell.md)). Vertiefung unten. |
| **TimeFilter** | Technischer Begriff für einen benannten Zeitfilter. Gehört zu einer Einrichtung und definiert ein Zeitfenster (Startzeit, Endzeit) mit einem Label. |
| **Token-Einladung** | Token-basierter Einladungsfluss: Die Administration legt einen Zugang an, die eingeladene Person erhält eine E-Mail mit einem Einmal-Link und setzt ihr eigenes Passwort. Der Link ist standardmäßig 3 Tage gültig; konfigurierbar über `DJANGO_INVITE_TOKEN_TIMEOUT` (Sekunden). |
| **TOTP / 2FA** | Zeitbasierte Einmalpasswörter via `django-otp`, ergänzt um Passkeys und Backup-Codes. In Produktion per Rollen-Default für alle fünf Rollen verpflichtend: sofort für `super_admin` und `facility_admin` (`MFA_ENFORCED_ROLES_IMMEDIATE`), nach 30 Tagen Übergangsfrist ab der ersten Anmeldung für `lead`, `staff` und `assistant` (`MFA_ENFORCED_ROLES_GRACE`). Zusätzlich sofort erzwingbar pro Nutzer (`User.mfa_required`) oder einrichtungsweit (`Settings.mfa_enforced_facility_wide`). Codes 30 s gültig. |
| **User** | Mitarbeitende — eine Person, die mit dem System arbeitet. Hat Zugangsdaten und eine Rollenzuweisung in einer Einrichtung. |
| **WorkItem** | Arbeitsinfo — ein operativer Eintrag (Hinweis oder Aufgabe) mit eigenem Lebenszyklus und optionaler Priorität. Getrennt von der fachlichen Dokumentation. |
| **Zeitfenster: kalendarisch vs. rollierend** | Zwei Arten von Zeitraum-Angaben, die nie dasselbe Wort teilen dürfen ([Sprachleitlinie](sprachleitlinie.md#zeitwörter-kalendarisch-vs-rollierend), Refs #1804). *Kalendarisch* = an den Kalender gebundene Einheit (ISO-Woche Mo–So, Kalendermonat, Kalenderjahr); Kalenderwörter sind dafür reserviert. *Rollierend* = ein Fenster relativer Länge, das mit „heute" wandert; es heißt nach seiner Länge (*Letzte 30 Tage*, *Nächste 7 Tage*). Beide Enden sind inklusiv, die Länge entspricht dem Namen: *Letzte 30 Tage* = `heute−29 … heute`. |
| **Zeitstrom** | Der chronologische Fluss aller Ereignisse, ungefiltert oder gefiltert nach Zeitraum, Person oder Dokumentationstyp. Die Grundmetapher der Dokumentation in Anlaufstelle. |

---

## K-Anonymität im Detail

**K-Anonymität** ist ein etabliertes Datenschutzprinzip gegen Re-Identifikation in
Statistiken und Berichten. Eine Auswertung ist *k-anonym*, wenn sich jede Person in
Bezug auf die ausgewerteten Merkmale von mindestens **k − 1** anderen Personen nicht
unterscheiden lässt — jede Merkmalskombination kommt also **mindestens k-mal** vor.

**Alltagsbeispiel (k = 5):** Eine Statistik gruppiert Kontakte nach *Altersgruppe*
und *Geschlecht*. Gibt es in einer Einrichtung nur **eine** Person der Kombination
„unter 18 / divers", würde diese Zeile faktisch eine einzelne Person offenlegen —
obwohl die Statistik „anonym" wirkt. Bei k = 5 wird eine solche Gruppe mit weniger
als 5 Personen **unterdrückt** (kein Zahlenwert, sondern „—" bzw. `suppressed`),
um das Risiko einer Zuordnung zu einzelnen Personen zu verringern.

**In Anlaufstelle:**

- Der Schwellwert ist pro Einrichtung über das Setting
  [`k_anonymity_threshold`](https://github.com/anlaufstelle/app/blob/main/src/core/models/settings.py)
  konfigurierbar (**Default 5**).
- Datenschutzfreundliche externe Berichte
  ([`core/services/dashboard/external_report.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/dashboard/external_report.py))
  wenden die Schwelle auf alle Aggregate an und entfernen Pseudonym-Rankings vollständig.
- Optional k-anonymisiert der Retention-Pfad die Personenzeile (Setting
  `retention_use_k_anonymization`), wenn ihre Äquivalenzklasse mindestens k Personen
  umfasst; sonst gilt die reguläre Anonymisierung.
  **Beide Pfade tilgen dieselbe Freitext-Kaskade** auf
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

**Soft-Delete-Strategien** — die vier Strategien für automatische Aufbewahrung und Soft-Delete, je
Ereignis: `anonymous` und `identified` (Frist ab dem Zeitpunkt des Ereignisses),
`qualified` (Frist ab Abschluss des verknüpften Falls), `document_type` (zusätzliche
Frist pro Dokumenttyp; es gilt die zuerst ablaufende). Der tägliche Lauf löscht
ohne Freigabeschritt; Löschvorschläge (`RetentionProposal`) entstehen nur in einem
gesonderten Vorschlagslauf. Eine Person mit mindestens einem Ereignis wird
anonymisiert, sobald alle ihre Ereignisse gelöscht sind und kein aktiver Legal
Hold auf ihr, einem ihrer Fälle oder Ereignisse liegt. Details:
[ADR-021](adr/021-retention-modell.md).

**Legal Hold** — Eigener Aufbewahrungssperren-Eintrag auf Ereignis, Person oder
Fall, kein Flag am Datensatz. Nimmt das Ziel und bei Person/Fall auch seine
Ereignisse von automatischer Retention-Löschung und Anonymisierung aus, solange
die Sperre aktiv ist, etwa für ein laufendes Verfahren
([ADR-021](adr/021-retention-modell.md)).

**k-Anon-Retention** — Spielart des Retention-Pfads: Sobald alle Ereignisse einer
Person soft-gelöscht sind und keine aktive Aufbewahrungssperre besteht, kann die
Personenzeile k-anonymisiert werden (Setting `retention_use_k_anonymization`,
Schwelle `k_anonymity_threshold`). Dafür muss die Äquivalenzklasse mindestens k
Personen umfassen; andernfalls gilt die reguläre Anonymisierung. Im K-Anon-Pfad
bleiben Altersgruppe und Kontaktstufe statistisch auswertbar; Pseudonym, Notizen
und dynamische Angaben werden redigiert. Der Bridge-Layer tilgt zusätzlich die
zugehörige Freitext-Kaskade, Anhänge und Offline-Bundles wie im regulären Pfad;
die Primitive `k_anonymize_client` verändert nur die Personenzeile. Dies ist von
der **Bucket-Suppression** im externen Bericht zu unterscheiden. Details:
[ADR-021](adr/021-retention-modell.md), [ADR-023](adr/023-k-anonymization-statistik.md).

**Quasi-Identifikator / Äquivalenzklasse** — Merkmale, die einzeln nicht, in
Kombination aber identifizieren. Die Äquivalenzklasse der K-Anonymisierung ist
`(facility, age_cluster, contact_stage)` ([ADR-023](adr/023-k-anonymization-statistik.md)).

**Offline-Snapshot-Keys** — die client-seitige Verschlüsselung des Offline-Lesecaches
(Streetwork-Modus, § 16 Fachkonzept). Pro Gerät wird beim Login aus dem Nutzerpasswort via
PBKDF2 (600 000 Iterationen, SHA-256) ein **non-extractable** AES-GCM-256-`CryptoKey`
abgeleitet und in einer eigenen IndexedDB (`anlaufstelle-crypto`) gehalten; die rohen
Schlüssel-Bytes sind nicht exportierbar, der Schlüssel selbst bleibt im Browser
aber nutzbar. Nach Ablauf der Leerlaufzeit entzieht die App ihn beim nächsten
Ausführen (bei ungesendeter Arbeit nur den Schlüssel, sonst auch den Bestand).
Ein gestohlenes Gerät liefert nur Chiffretext, sobald der Schlüssel entzogen ist;
innerhalb der Leerlaufzeit öffnet die App offline ohne erneuten Login. Schließen
des Tabs und Geräteneustart löschen nichts.
Server-Bundles sind gedeckelt (50 Ereignisse, 90 Tage, TTL 48 h) und wenden Sichtbarkeit
vor der Serialisierung an. Details: [ADR-022](adr/022-offline-snapshot-keys.md).

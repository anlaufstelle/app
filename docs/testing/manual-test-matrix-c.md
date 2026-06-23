# Manuelle Test-Matrix — Sektion C

> Teil der [Manual-Test-Matrix](manual-test-matrix.md) (Refs #1071 Block B). Setup-Block, Status-Legende, TC-ID-Schema, Browser-/Mobile-Konventionen und die Anhänge stehen im [Hub](manual-test-matrix.md); die Gesamtübersicht in [`test-matrix-index.md`](test-matrix-index.md).

---

## SEKTION C — Auditor-DSGVO/Security

> **Zielgruppe:** Externe:r DSGVO-Auditor:in oder interne Compliance-Prüfung. Maximale Tiefe mit Verweis auf konkrete DSGVO-Artikel, Migrationen, Services, Settings.
>
> **Konvention:** Jeder Case enthält zusätzlich:
> - **DSGVO-Artikel-Zitat** (kurz, im Klartext)
> - **Code-Referenz** (Datei + Funktion/Zeilen-Bereich)
> - **Migrations-Referenz** (falls RLS/Schema-relevant)
> - **Erwarteter Audit-Eintrag** (Action-Type + Felder)

### DSGVO Art. 5 — Grundsätze

#### AUD-DSGVO-Art5-01 — Zweckbindung des Audit-Logs

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Compliance | admin | C/F/S || `src/tests/test_audit_signals.py`, `src/tests/test_audit_view.py` |

**DSGVO-Artikel-Zitat:** *Art. 5 Abs. 1 lit. b — Personenbezogene Daten müssen für festgelegte, eindeutige und legitime Zwecke erhoben werden.*

**Code-Referenz:**
- `src/core/models/audit.py` (`AuditLog` — append-only, 30+ Action-Types)
- `src/core/services/audit.py` (Logging-Helfer)


**Schritte:**
1. Mit `admin` einloggen, `/audit/` öffnen.
2. Filter nach `Action`-Typen anwenden — bestätigen, dass jeder Eintrag einen klar definierten Zweck hat (LOGIN, EXPORT, DELETE, …).
3. Im Quellcode `src/core/models/audit.py` die `Action`-Choices prüfen — alle dokumentieren den Verarbeitungszweck.
4. Stichprobe: 5 zufällige Einträge aus `/audit/` öffnen → jeder Eintrag dokumentiert wer/was/wann/warum.

**Erwartetes Ergebnis:**
- Jeder Audit-Eintrag hat einen sprechenden Action-Typ und ein Zielobjekt.
- Keine generischen Logs ohne Zweck (z.B. „debug" oder „misc").

**Erwarteter Audit-Eintrag:** dieser Test selbst erzeugt nur LOGIN/VIEW-Einträge.

**Status:** ☐ Offen

---

#### AUD-DSGVO-Art5-02 — Datenminimierung im Anonym-Modus

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Compliance | fachkraft | C/F/S || `src/tests/test_min_contact_stage_anonymous.py`, `src/tests/test_k_anonymization.py` |

**DSGVO-Artikel-Zitat:** *Art. 5 Abs. 1 lit. c — Daten müssen dem Zweck angemessen, erheblich und auf das notwendige Maß beschränkt sein.*

**Code-Referenz:**
- `src/core/services/k_anonymization.py` (k=5 Default)
- `src/core/models/client.py` (`Client.stage` mit `anonymous`/`identified`/`qualified`)

**Voraussetzung:** SMK-A-VORM-02

**Schritte:**
1. Anonyme:n Klient:in über VORM-02 anlegen (kein Pseudonym).
2. Detail-View prüfen: keine direkt identifizierenden Felder (Name, Geb-Datum, Adresse) sichtbar/setzbar.
3. JSON-Export der Klient:in (über Lead+ Sudo): nur Alters-Cluster, Stage, ID — keine Klarnamen.
4. Im Quellcode `services/k_anonymization.py` prüfen: k=5 Default für Anonymisierung.

**Erwartetes Ergebnis:**
- Im Anonym-Modus werden nur Alters-Cluster (z.B. „25-34") statt exakter Geburtsdaten gespeichert.
- Keine Klarnamen-Felder vorhanden.

**Status:** ☐ Offen

---

### DSGVO Art. 7 — Einwilligung

#### AUD-DSGVO-Art7-01 — Einwilligungs-Template (organisatorisch)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Compliance | admin | C/F/S |||

**DSGVO-Artikel-Zitat:** *Art. 7 — Bedingungen für die Einwilligung. Nachweis und Widerruflichkeit müssen sichergestellt sein.*

**Code-Referenz:**
- `src/core/services/dsgvo_package.py`
- `src/core/management/commands/generate_dsgvo_package.py`

**Hinweis:** Self-Service-Einwilligung in der App ist **nicht implementiert** (Memory: organisatorisch über Mitarbeiter:in/Leitung).

**Voraussetzung:** Sudo-Mode aktiv (`/sudo/`)

**Schritte:**
1. Mit `admin` einloggen, Sudo-Mode betreten.
2. `/dsgvo/` öffnen — DSGVO-Paket-View.
3. Einwilligungs-Template (Information für Klient:innen, Art. 13/14) als Markdown herunterladen.
4. Inhalt prüfen: Hinweise auf Zweck, Speicherdauer, Widerruflichkeit, Empfänger:innen.

**Erwartetes Ergebnis:**
- Template ist verfügbar, Facility-spezifische Platzhalter sind ersetzt.
- Verteilung an Klient:innen erfolgt **organisatorisch** (Mitarbeiter:in händigt aus, dokumentiert in Akte).

**Erwarteter Audit-Eintrag:** `EXPORT` mit Slug `informationspflichten`.

**Status:** ☐ Offen

---

### DSGVO Art. 15 — Auskunftsrecht

#### AUD-DSGVO-Art15-01 — Datenauskunft als JSON-Export

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Compliance | leitung | C/F/S || `test_client_export.py`, `src/tests/test_client_export.py` |

**DSGVO-Artikel-Zitat:** *Art. 15 — Betroffene Person hat Recht auf Auskunft über alle gespeicherten personenbezogenen Daten.*

**Code-Referenz:**
- `src/core/views/clients.py` (`ClientDataExportJSONView`)
- `src/core/services/client_export.py`

**Voraussetzung:** Sudo-Mode aktiv, eine identifizierte Klient:in mit Events.

**Schritte:**
1. Mit `leitung` einloggen, Sudo-Mode betreten.
2. Klient:in aus Liste wählen, Detail öffnen.
3. Auf **„Datenauskunft (JSON)"** klicken → Download startet.
4. JSON öffnen und prüfen:
 - Alle Klient:innen-Stammdaten enthalten.
 - Alle Events mit Inhalten enthalten.
 - Alle Cases / Episoden / Goals enthalten.
 - Alle Anhänge (Metadaten, nicht Binary) enthalten.
5. AuditLog `/audit/` filtern auf `Action=EXPORT` → Eintrag für diesen Export sichtbar.

**Erwartetes Ergebnis:**
- JSON-Datei ist maschinenlesbar, vollständig (Schema-Probe: keine `null`-Felder bei vorhandenen Daten).
- AuditLog-Eintrag mit User=leitung, Target=Client-UUID, Action=EXPORT.

**Erwarteter Audit-Eintrag:** `EXPORT` mit `target_id=<client_uuid>`, `format=json`.

**Status:** ☐ Offen

---

#### AUD-DSGVO-Art15-02 — Datenauskunft als PDF

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Compliance | leitung | C/F/S || `test_client_export.py` |

**DSGVO-Artikel-Zitat:** *Art. 15 — Auskunft in lesbarer Form.*

**Code-Referenz:** `src/core/views/clients.py` (`ClientDataExportPDFView`)

**Voraussetzung:** Sudo-Mode aktiv.

**Schritte:**
1. Wie AUD-DSGVO-Art15-01, aber **„PDF"** statt JSON.
2. PDF öffnen → Inhalt enthält Klient:innen-Stammdaten + Events + Cases.
3. Layout: Kopfzeile mit Facility-Name, Fußzeile mit Datum + „Generiert am".

**Erwartetes Ergebnis:**
- PDF ist menschenlesbar, alle Klient:innen-Daten enthalten.
- AuditLog: `EXPORT` mit `format=pdf`.

**Status:** ☐ Offen

---

### DSGVO Art. 16 — Berichtigung

#### AUD-DSGVO-Art16-01 — Berichtigung über Mitarbeiter:in (organisatorisch)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Compliance | fachkraft | C/F/S || `test_client_edit.py` |

**DSGVO-Artikel-Zitat:** *Art. 16 — Recht auf Berichtigung unrichtiger personenbezogener Daten.*

**Hinweis:** Self-Service-Korrektur ist **nicht implementiert** (Memory: organisatorisch). Korrektur erfolgt durch Mitarbeiter:in/Leitung mit Audit-Spur.

**Code-Referenz:**
- `src/core/views/clients.py` (`ClientUpdateView`)
- `src/core/services/audit_signals.py` (Mutations-Logging)

**Voraussetzung:** Klient:in mit Pseudonym vorhanden.

**Schritte:**
1. Mit `fachkraft` einloggen, Klient:in öffnen.
2. **„Bearbeiten"** klicken, Pseudonym ändern (z.B. Tippfehler korrigieren).
3. Speichern.
4. AuditLog filtern auf `Action=CLIENT_UPDATED` → Eintrag mit altem + neuem Wert.

**Erwartetes Ergebnis:**
- Berichtigung erfolgreich.
- Audit-Spur dokumentiert vorher/nachher.

**Erwarteter Audit-Eintrag:** `CLIENT_UPDATED` mit `changed_fields=['pseudonym']`, `before=…`, `after=…`.

**Status:** ☐ Offen

---

### DSGVO Art. 17 — Löschung

#### AUD-DSGVO-Art17-01 — 4-Augen-Lösch-Antrag

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Compliance | fachkraft + leitung | C/F/S || `test_client_deletion_workflow.py` |

**DSGVO-Artikel-Zitat:** *Art. 17 — Recht auf Löschung („Recht auf Vergessenwerden").*

**Code-Referenz:**
- `src/core/views/clients.py` (`ClientDeleteRequestView`)
- `src/core/services/clients.py` (request_deletion)
- `src/core/views/deletion_requests.py` (`DeletionRequestReviewView`)

**Voraussetzung:** eine Klient:in vorhanden, beide Rollen verfügbar.

**Schritte (in zwei Profilen):**
1. **Profil A — `fachkraft`:** Klient:in öffnen → **„Löschung beantragen"** → Begründung eintragen → Absenden.
2. AuditLog: `DELETION_REQUESTED` mit `requested_by=fachkraft`.
3. **Profil B — `leitung`:** `/deletion-requests/` öffnen → Eintrag sichtbar.
4. Auf **„Genehmigen"** klicken → Soft-Delete erfolgt.
5. AuditLog: `DELETION_APPROVED` mit `approved_by=leitung`, `approved_by != requested_by`.
6. **Profil A — `fachkraft`:** Versuchen, eigenen Antrag zu genehmigen → 403, AuditLog `FORBIDDEN`.

**Erwartetes Ergebnis:**
- Vier-Augen-Prinzip wird erzwungen.
- Klient:in landet im Trash (`/clients/trash/`).

**Erwarteter Audit-Eintrag:** `DELETION_REQUESTED`, `DELETION_APPROVED`.

**Status:** ☐ Offen

---

#### AUD-DSGVO-Art17-02 — Trash-Frist und Wiederherstellung

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Compliance | admin | C/F/S || `test_client_deletion_workflow.py` |

**DSGVO-Artikel-Zitat:** *Art. 17 — Löschung muss erfolgen, sofern keine Aufbewahrungspflicht entgegensteht.*

**Code-Referenz:**
- `src/core/models/settings.py` (`client_trash_days = 30`)
- `src/core/views/clients.py` (`ClientTrashView`, `ClientRestoreView`)

**Voraussetzung:** AUD-DSGVO-Art17-01 (Klient:in im Trash).

**Schritte:**
1. Mit `admin` einloggen.
2. `/clients/trash/` öffnen → soft-gelöschte Klient:in sichtbar.
3. Stichprobe: Klient:in **wiederherstellen** vor Ablauf (innerhalb 30 Tage).
4. AuditLog: `CLIENT_RESTORED` mit `restored_by=admin`.

**Erwartetes Ergebnis:**
- Wiederherstellung möglich innerhalb 30 Tagen.
- Nach 30 Tagen: automatische Anonymisierung (siehe AUD-DSGVO-Art17-03).

**Erwarteter Audit-Eintrag:** `CLIENT_RESTORED`.

**Status:** ☐ Offen

---

### DSGVO Art. 18 — Einschränkung

> Alle Tests zu „DSGVO Art. 18 — Einschränkung“ sind in [SEKTION D](manual-test-matrix-d.md#sektion-d--entwickler-probes-lokalssh) gelistet (LOKAL/SSH-Probes).

---

### DSGVO Art. 20 — Datenübertragbarkeit

#### AUD-DSGVO-Art20-01 — Maschinenlesbarer Export

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Compliance | leitung | C/F/S || `test_client_export.py` |

**DSGVO-Artikel-Zitat:** *Art. 20 — Daten in einem strukturierten, gängigen und maschinenlesbaren Format erhalten.*

**Code-Referenz:** `src/core/services/client_export.py`

**Voraussetzung:** AUD-DSGVO-Art15-01 (JSON-Export liegt vor).

**Schritte:**
1. Heruntergeladenes JSON in einem JSON-Schema-Validator (z.B. `jq` oder Online-Tool) öffnen.
2. Schema-Probe: Top-Level-Felder `client`, `events[]`, `cases[]`, `attachments[]`.
3. Test-Skript: `jq '.events | length' export.json` → Anzahl entspricht Events der Klient:in.
4. Test-Skript: `jq '.client.pseudonym, .client.stage' export.json` → Werte aus UI.

**Erwartetes Ergebnis:**
- JSON ist syntaktisch korrekt und schema-konform.
- Inhalte sind wiederverwertbar (Re-Import in Test-Tool funktioniert).

**Status:** ☐ Offen

---

### DSGVO Art. 25 — Privacy by Design


---

#### AUD-DSGVO-Art25-02 — MFA-Pflicht für privilegierte Rollen

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Compliance | admin | C/F/S || `src/tests/test_mfa_login.py` |

**DSGVO-Artikel-Zitat:** *Art. 25 — Voreinstellungen so wählen, dass nur erforderliche Daten verarbeitet werden.*

**Code-Referenz:** `src/core/middleware/mfa.py` (`MFAEnforcementMiddleware`), `src/core/models/user.py` (`mfa_required`)

**Voraussetzung:** Admin-User ohne aktiviertes MFA.

**Schritte:**
1. Settings: `mfa_required=True` für Admin in DB setzen.
2. Mit Admin einloggen → Redirect zu `/mfa/setup/` (Pflicht-Setup).
3. Versuch, vor MFA-Setup auf andere URL zu navigieren → Redirect zurück zu Setup.
4. Nach MFA-Setup: alle Views erreichbar.
5. Logout + erneuter Login → MFA-Verify-Schritt zwingend.

**Erwartetes Ergebnis:**
- Privilegierte Rollen können keine Aktionen ohne MFA durchführen.

**Status:** ☐ Offen

---

### DSGVO Art. 30 — Verarbeitungsverzeichnis

> Alle Tests zu „DSGVO Art. 30 — Verarbeitungsverzeichnis“ sind in [SEKTION D](manual-test-matrix-d.md#sektion-d--entwickler-probes-lokalssh) gelistet (LOKAL/SSH-Probes).

---

### DSGVO Art. 32 — Sicherheit der Verarbeitung

#### AUD-DSGVO-Art32-01 — Sicherheits-HTTP-Header

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Compliance || C || `src/tests/test_security_hardening.py`, `src/tests/test_csp_report.py` |

**DSGVO-Artikel-Zitat:** *Art. 32 — Geeignete TOMs zur Gewährleistung der Sicherheit (Verschlüsselung, Vertraulichkeit, Integrität, Verfügbarkeit, Belastbarkeit).*

**Code-Referenz:**
- `src/anlaufstelle/settings/prod.py` (HSTS, CSRF, X-Frame, …)
- `src/anlaufstelle/settings/base.py` (CSP)

**Voraussetzung:** Prod-ähnliche Konfiguration (`make runserver-prod` oder via Caddy).

**Schritte:**
1. `curl -I https://localhost:8443/login/` (oder vergleichbar gegen Prod-Mirror).
2. Header prüfen:
 - `Strict-Transport-Security: max-age=31536000; includeSubDomains; preload`
 - `Content-Security-Policy: default-src 'self'; …` (kein `unsafe-eval`)
 - `X-Frame-Options: DENY`
 - `X-Content-Type-Options: nosniff`
 - `Referrer-Policy: strict-origin-when-cross-origin`
 - Cookies: `Secure; HttpOnly; SameSite=Strict` (CSRF) bzw. `SameSite=Lax` (Session)

**Erwartetes Ergebnis:** Alle Header gesetzt, Werte korrekt.

**Status:** ☐ Offen

---

#### AUD-DSGVO-Art32-03 — TLS-in-Transit (Caddy)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Compliance || C |||

**DSGVO-Artikel-Zitat:** *Art. 32 — Vertraulichkeit der Übertragung.*

**Code-Referenz:** `deploy/Caddyfile`, Settings `SECURE_PROXY_SSL_HEADER`

**Voraussetzung:** Prod-ähnliche Konfiguration mit Caddy als Reverse-Proxy.

**Schritte:**
1. `curl -v https://anlaufstelle-prod-mirror.example/` → TLS 1.3, gültiges Zertifikat (Let's Encrypt).
2. `curl -v http://anlaufstelle-prod-mirror.example/` → 301 Redirect auf HTTPS.
3. Test mit `testssl.sh` (extern): keine kritischen Findings, Cipher-Suites mindestens TLS_AES_128_GCM_SHA256 + TLS_AES_256_GCM_SHA384.

**Erwartetes Ergebnis:**
- HTTPS erzwungen, TLS ≥ 1.2, A-Rating bei testssl.sh / SSL-Labs.

**Status:** ☐ Offen

---

### DSGVO Art. 33-34 — Meldepflichten bei Datenpannen


---

#### AUD-DSGVO-Art33-34-02 — Notification-Trigger

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Compliance | admin | C/F/S |||

**DSGVO-Artikel-Zitat:** *Art. 34 — Benachrichtigung der betroffenen Person bei hohem Risiko.*

**Code-Referenz:** `src/core/services/breach_detection.py` (Notify-Hooks)

**Hinweis:** Tatsächliche E-Mail-Versand-Konfiguration muss organisatorisch geprüft werden (DSB benachrichtigt Aufsichtsbehörde, nicht die App).

**Schritte:**
1. Breach-Detection auslösen (siehe AUD-DSGVO-Art33-34-01).
2. Mail-Backend prüfen: Console-Mail oder MailHog enthält Admin-Benachrichtigung.
3. Inhalt: Vorfall-Beschreibung, betroffene User-Anzahl, Empfehlung.

**Erwartetes Ergebnis:**
- Admin-Benachrichtigung wird ausgelöst.
- Organisatorischer Folge-Workflow (DSB → Aufsicht) ist außerhalb der App.

**Status:** ☐ Offen

---

### DSGVO Art. 35 — DSFA

#### AUD-DSGVO-Art35-01 — DSFA-Template-Download

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Compliance | admin | C/F/S || `test_dsgvo_package.py` |

**DSGVO-Artikel-Zitat:** *Art. 35 — Datenschutz-Folgenabschätzung bei voraussichtlich hohem Risiko.*

**Code-Referenz:** `src/core/services/dsgvo_package.py` (DSFA-Template)

**Voraussetzung:** Sudo-Mode.

**Schritte:**
1. `/dsgvo/` öffnen, **„DSFA"** herunterladen.
2. Template prüfen:
 - Beschreibung der Verarbeitung.
 - Notwendigkeit + Verhältnismäßigkeit.
 - Risiken für Betroffene (Re-Identifikation, Stigmatisierung).
 - Geplante Abhilfemaßnahmen (k-Anonymität, Sensitivity-Filter, RLS).
 - Vorab-Konsultation der Aufsichtsbehörde, falls Risiko nicht reduzierbar.

**Erwartetes Ergebnis:**
- DSFA-Template ist vollständig, Facility-spezifische Werte eingesetzt.

**Erwarteter Audit-Eintrag:** `EXPORT` mit `slug=dsfa`.

**Status:** ☐ Offen

---

### Security: RLS-Penetration

#### AUD-SEC-RLS-01 — Cross-Facility Klient:innen-Probe

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Security | admin + admin_2 | C || `src/tests/test_rls.py`, `src/tests/test_scope.py` |

**Code-Referenz:**
- `src/core/middleware/facility_scope.py`
- `src/core/migrations/0047_postgres_rls_setup.py`

**Voraussetzung:** zwei Browser-Profile.

**Schritte:**
1. **Profil A — `admin`:** Eine Klient:in aufrufen, UUID aus URL kopieren.
2. **Profil B — `admin_2`:** URL `/clients/<uuid>/` mit der UUID aus Profil A öffnen.
3. Erwartung: 404 (Klient:in „nicht gefunden") — nicht 403.
4. AuditLog: kein Audit-Eintrag (Datensatz existiert für Profil B nicht — RLS).

**Erwartetes Ergebnis:**
- 404 ohne Datenleak.
- Konsistent über alle Modelle: Client, Case, Event, WorkItem.

**Status:** ☐ Offen

---

#### AUD-SEC-RLS-02 — Cross-Facility Case-Probe

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Security | admin + admin_2 | C || `src/tests/test_rls.py` |

**Schritte:** Wie AUD-SEC-RLS-01, aber mit Case statt Client.

**Erwartetes Ergebnis:** 404, kein Datenleak.

**Status:** ☐ Offen

---

#### AUD-SEC-RLS-03 — Cross-Facility Event-Probe

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Security | admin + admin_2 | C || `src/tests/test_rls.py` |

**Schritte:** Wie AUD-SEC-RLS-01, aber mit Event.

**Status:** ☐ Offen

---

#### AUD-SEC-RLS-04 — Cross-Facility WorkItem-Probe

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Security | admin + admin_2 | C || `src/tests/test_rls.py` |

**Schritte:** Wie AUD-SEC-RLS-01, aber mit WorkItem.

**Status:** ☐ Offen

---

#### AUD-SEC-RLS-05 — Cross-Facility Audit-Log-Scope

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Security | admin + admin_1 | C || `src/tests/test_rls.py` |

**Schritte:**
1. Als `admin` (Hauptstelle) `/audit/` öffnen → nur eigene-Facility-Einträge.
2. UUID eines Hauptstelle-Audit-Eintrags merken, als `admin_1` (Zweigstelle Nord) `/audit/<uuid>/` aufrufen.

**Erwartetes Ergebnis:**
- Jede:r facility_admin sieht in `/audit/` nur die eigene Facility (Live-Probe: beide `admin` und `admin_1` → 200 auf eigener Liste).
- Detailzugriff auf fremde Facility → **404** (Live verifiziert: `admin_1` → 404 auf Hauptstelle-Audit-Detail). Keine Cross-Facility-Sichtbarkeit.

**Status:** ☐ Offen

---

#### AUD-SEC-RLS-07 — Cross-Facility Retention- & Lösch-Antrag-Isolation

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Security | emma + emma_1 | C || `src/tests/test_rls.py` |

**Schritte:**
1. Als `emma` (lead, Hauptstelle) Lösch-Antrag-/Retention-Detail-UUID merken.
2. Als `emma_1` (lead, Zweigstelle Nord) dieselbe `/deletion-requests/<uuid>/review/` aufrufen.

**Erwartetes Ergebnis:**
- Fremde Facility → **404** (Live verifiziert: `emma_1` → 404 auf `deletion_review` der Hauptstelle). Retention-Dashboard zeigt nur eigene-Facility-Proposals.

**Status:** ☐ Offen

---

#### AUD-SEC-RLS-08 — Cross-Facility Attachment-, Statistik- & Suche-Isolation

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Security | miriam + miriam_1 | C || `src/tests/test_rls.py` |

**Schritte:**
1. Als `miriam` (Hauptstelle) Event-Attachment-Download-URL + Personen-Pseudonym merken.
2. Als `miriam_1` (Zweigstelle Nord) Attachment-URL aufrufen und nach dem Fremd-Pseudonym suchen.

**Erwartetes Ergebnis:**
- Attachment fremder Facility → 404; `/search/` liefert keine Fremd-Facility-Treffer; Statistik aggregiert nur eigene Facility (`emma`/`admin` 200, scope-gebunden).

**Status:** ☐ Offen

---

#### AUD-SEC-RLS-06 — SQL-Injection-Probe gegen RLS

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Security | fachkraft | C || `src/tests/test_security_hardening.py` |

**Schritte:**
1. Suche-Query mit SQL-Pattern: `'; DROP TABLE core_client;--`.
2. Erwartung: Django ORM parametrisiert → keine SQL-Injection möglich.
3. AuditLog: keine ungewöhnlichen Aktionen.
4. Pen-Test: `sqlmap` gegen `/search/?q=…` (außerhalb dieser Matrix, separates Audit).

**Status:** ☐ Offen

---

### Security: MFA-Härtung

#### AUD-SEC-MFA-01 — Backup-Code-Reuse-Verbot

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Security | fachkraft | C/F/S || `test_mfa_backup_codes.py` |

**Code-Referenz:** `src/core/services/mfa.py` (`StaticToken`, One-Time-Use)

**Voraussetzung:** Aktiviertes MFA, Backup-Codes generiert.

**Schritte:**
1. Login → MFA-Verify → einen Backup-Code eingeben (statt TOTP).
2. Login erfolgt.
3. Logout, erneuter Login → denselben Backup-Code erneut versuchen.
4. Erwartung: Fehler „Code bereits verwendet".
5. AuditLog: `BACKUP_CODE_USED` für 1. Versuch, `BACKUP_CODE_REUSE_DENIED` für 2. Versuch (oder MFA_FAILED).

**Erwartetes Ergebnis:**
- Backup-Codes sind One-Time-Use.

**Status:** ☐ Offen

---

#### AUD-SEC-MFA-02 — MFA-Lockout

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Security | fachkraft | C/F/S || `test_mfa_lockout.py` |

**Schritte:**
1. Aktiviertes MFA, Login mit korrektem Pwd.
2. MFA-Verify: 5× falscher Code in Folge.
3. Erwartung: MFA-Lockout (zusätzlich zu Login-Lockout).
4. Audit: `MFA_FAILED` × 5, dann ggf. `MFA_LOCKED`.

**Erwartetes Ergebnis:**
- MFA-Lockout schützt vor Brute-Force-Angriffen auf den 6-stelligen Code.

**Status:** ☐ Offen

---

### Security: Audit-Log-Integrität


---

#### AUD-SEC-AUDIT-03 — HMAC-Email-Hash statt Klartext

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Security | admin | C/F/S || `src/tests/test_audit_view.py` |

**Code-Referenz:** `src/core/views/auth.py` (`RateLimitedPasswordResetView`)

**Schritte:**
1. Pwd-Reset auslösen mit unbekannter E-Mail (z.B. `unbekannt@example.com`).
2. AuditLog filtern auf `Action=PASSWORD_RESET_REQUESTED`.
3. Eintrag prüfen: `email_hash` (HMAC-SHA-256) statt Klartext-Email.
4. Vergleich: Klartext-Email ist nirgends im Audit-Log gespeichert.

**Erwartetes Ergebnis:**
- HMAC-Hash sichtbar, Klartext-Email nicht.
- Audit-Log darf bei Datenleck keine Re-Identifikation ermöglichen.

**Status:** ☐ Offen

---

#### AUD-SEC-AUDIT-04 — Settings-Audit-Diff vollständig (jedes auditpflichtige Feld)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Security | facility_admin | C || `src/tests/test_audit_coverage.py::TestSettingsChangeAudit::test_compliance_fields_are_audited`, `src/tests/test_architecture.py::TestSettingsAuditCompletenessGuard` |

**Code-Referenz:** [src/core/services/settings.py](https://github.com/anlaufstelle/app/blob/main/src/core/services/settings.py) (`_AUDIT_FIELDS`, `_AUDIT_EXEMPT`)

**Schritte:**
1. Als `facility_admin` in `/admin-mgmt/` einloggen.
2. Settings öffnen (`/admin-mgmt/core/settings/<facility-pk>/`).
3. Pro auditpflichtigem Feld (siehe Liste unten) ändern und speichern.
4. Aus AuditLog (`/audit/`) den `SETTINGS_CHANGE`-Eintrag holen.
5. `detail.changed_fields` muss exakt das geänderte Feld enthalten.

**Felder zum Durchspielen:**

Stammdaten, Session/MFA, Retention-Basis, Retention-Workflow, K-Anonymisierung, Suche, Datei-Policy — Referenzliste in `_AUDIT_FIELDS`. Refs #893.

**Erwartetes Ergebnis:**
- Jedes Feld erzeugt genau einen Audit-Diff mit Feldnamen.
- Bewusst ausgeschlossen sind nur `facility` (PK) und `updated_at` (auto_now); diese stehen in `_AUDIT_EXEMPT`.

**DSGVO/Security-Note:** Art. 5 Abs. 1 lit. f (Integrität) — nicht-auditiert geänderte Settings sind ein Nachvollziehbarkeitsmangel.

**Status:** ☐ Offen

---

#### AUD-SEC-AUDIT-05 — AuditLog-Payload enthält keine PII

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Security | facility_admin | C/F || `src/tests/test_audit_coverage.py::TestSettingsChangeAudit::test_settings_audit_does_not_leak_values` |

**Code-Referenz:** Helper-Familie in [src/core/services/audit.py](https://github.com/anlaufstelle/app/blob/main/src/core/services/audit.py) (Refs #901)

**Schritte:**
1. Klient bearbeiten: `notes`-Feld auf einen kompletten Satz „Frau Mustermann, Tel. 0123-456789" setzen, speichern.
2. AuditLog filtern auf `Action=CLIENT_UPDATE` für diesen Klient.
3. `detail`-Feld prüfen: nur `{"changed_fields": ["notes"]}`, **kein** Wert.
4. Settings ändern: Stadtname in `facility_full_name` auf einen „echten" Namen setzen.
5. SETTINGS_CHANGE-Eintrag prüfen: keine Werte im Detail.
6. Stichprobe in `SECURITY_VIOLATION` (Virus-Treffer, Extension-Block): nur `reason`, `filename`, keine User-Klartext-Daten.

**Erwartetes Ergebnis:**
- AuditLog-`detail`-JSON enthält Feldnamen, Kategorien, technische Werte — niemals PII (Namen, Adressen, Notizen, Telefonnummern).

**DSGVO/Security-Note:** Art. 5 Abs. 1 lit. c (Datenminimierung) auf Meta-Ebene — AuditLog soll Vorgänge belegen, nicht Inhalte protokollieren.

**Status:** ☐ Offen

---

#### AUD-SEC-AUDIT-06 — Super-Admin-Systembereich loggt jeden Zugriff

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Security | super_admin | C || `src/tests/test_system_views.py` |

**Code-Referenz:** [src/core/views/system.py](https://github.com/anlaufstelle/app/blob/main/src/core/views/system.py) `SystemAuditMixin.dispatch`; nutzt `audit_system_view` aus [src/core/services/audit.py](https://github.com/anlaufstelle/app/blob/main/src/core/services/audit.py) (Refs #901 S6).

**Schritte:**
1. Als `super_admin` einloggen.
2. Nacheinander aufrufen: `/system/`, `/system/audit/`, `/system/lockouts/`, `/system/retention/`, `/system/legal-holds/`, `/system/vvt/`, `/system/maintenance/`.
3. Im System-Audit (`/system/audit/`) auf `Action=SYSTEM_VIEW` filtern.
4. Pro Aufruf muss ein Eintrag erscheinen mit `target_type=<View-Klassenname>` und `ip_address` aus dem Request.

**Erwartetes Ergebnis:**
- Jeder System-View-Zugriff ist auditiert.
- `facility=NULL` (system-wide), `user=super_admin`.

**DSGVO/Security-Note:** Art. 32 (TOM) + interne Compliance — privilegierte Operationen brauchen lückenlose Spur.

**Status:** ☐ Offen

---

### Security: Sudo/Session-Invalidation

#### AUD-SEC-AUTH-03 — Sudo-Mode-Invalidierung nach Passwort-/MFA-Änderung

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Security | facility_admin | C || `src/tests/test_sudo_mode.py` |

**Code-Referenz:** [src/core/services/sudo_mode.py](https://github.com/anlaufstelle/app/blob/main/src/core/services/sudo_mode.py), [src/core/views/sudo_mode.py](https://github.com/anlaufstelle/app/blob/main/src/core/views/sudo_mode.py)

**Schritte:**
1. Als `facility_admin` einloggen.
2. Sudo-Mode betreten (per Re-Auth), `Action=SUDO_MODE_ENTERED` im AuditLog erscheint.
3. **Variante A — Passwort-Wechsel:** Im selben Tab unter Account-Settings das eigene Passwort ändern.
4. Eine Sudo-pflichtige View aufrufen (z.B. MFA deaktivieren) → erwartet Redirect nach `/sudo/`.
5. **Variante B — MFA-Wechsel:** Frischer Re-Auth + Sudo, dann MFA-Setting wechseln (aktivieren/deaktivieren).
6. Sudo-pflichtige View → erwartet erneuter Re-Auth-Prompt.

**Erwartetes Ergebnis:**
- Sicherheitskritische Account-Änderungen invalidieren Sudo-Mode sofort, nicht erst nach Timeout.
- Falls aktuell nicht durchgesetzt: Issue eröffnen (`security` + `sudo`).

**Status:** ☐ Offen

---

#### AUD-SEC-AUTH-04 — User-Deaktivierung beendet bestehende Sessions

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Security | facility_admin + zweiter User | C (2 Browser-Profile) || `test_auth_session_invalidation.py` |

**Schritte:**
1. User A: als Fachkraft im Profil 1 (z.B. Chrome) einloggen.
2. User B: als `facility_admin` im Profil 2 (Chrome Incognito oder Firefox).
3. User B: in `/admin-mgmt/` den User A deaktivieren (`is_active=False`).
4. User A: Seite neu laden, beliebige geschützte URL aufrufen.

**Erwartetes Ergebnis:**
- User A wird sofort ausgeloggt und sieht den Login-Screen.
- Existierende Session bleibt nicht „sticky" bis zum Cookie-Timeout.

**Hinweis:** Django prüft `is_active` bei jedem Request über `AuthenticationMiddleware`+`SessionAuthenticationMiddleware`. Verifiziert die Standardverhalten unserer Custom-User.

**Status:** ☐ Offen

---

### Security: Export-/Bericht-Sicherheit

#### AUD-SEC-EXPORT-01 — Export-Service filtert Zeilen und Felder nach Rolle

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Security | assistenz vs. leitung | C || `test_client_export_rbac.py` |

**Schritte:**
1. Als `assistenz` einloggen, einen Klient/Case mit `HIGH`-Sensitivity-Events anlegen oder laden.
2. CSV-Export via `/clients/<uuid>/export.csv` oder Statistik-Export.
3. Datei prüfen: keine `HIGH`-Felder enthalten.
4. Als `leitung` selber Export erneut → `HIGH`-Felder sichtbar.
5. Stichprobe Audit-Export: `assistenz` darf System-Audit-Export gar nicht aufrufen.

**Erwartetes Ergebnis:**
- Export-Inhalte respektieren `user_can_see_field` / Sensitivity-Regeln.
- Zeilen anderer Facilities sind nie enthalten (RLS).

**DSGVO/Security-Note:** Art. 25 (Privacy-by-Default) — Export ist die häufigste Side-Channel-Quelle.

**Status:** ☐ Offen

---

#### AUD-SEC-EXPORT-02 — Externe Berichte ohne Pseudonyme

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Security | leitung | C || `test_export_external_no_pseudonyms.py` |

**Forward-looking:** Feature kommt mit #921 (datenschutzfreundliche externe Berichte). Case-Skelett:

- Vor #921: aktueller CSV/PDF-Statistik-Export darf keine Pseudonym-Spalten in „externer" Variante exportieren (zur Zeit gibt es nur intern → manuell Stichprobe).
- Nach #921: Vorlagen mit Datenschutzprofil, kleine Gruppen unterdrücken, K-Anonymity-Schwelle aktiv.

**Status:** ☐ Offen

---

#### AUD-SEC-EXPORT-03 — CSV/PDF-Exports gegen Formula Injection und Dateinamen-Leak abgesichert

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Security | leitung | C || `src/tests/test_export_statistics.py` |

**Schritte:**
1. Klient mit Pseudonym `=cmd|' /C calc'!A0` anlegen (Excel-Formula-Injection-Payload).
2. CSV-Statistik exportieren.
3. Datei in LibreOffice/Excel öffnen → Zelle muss als String anzeigen, **nicht** als Formel ausgeführt.
4. Dateinamen prüfen: enthält keinen Klient-Pseudonym oder andere PII (z.B. nur `auditlog-20260516-103000.csv`).
5. PDF-Export prüfen: Pseudonym wird escaped, kein Markup-Injection.

**Erwartetes Ergebnis:**
- Werte mit `=`, `+`, `-`, `@` werden mit führendem `'` (Apostroph) escaped oder als Text-Zelle markiert.
- Dateinamen enthalten keine personenbezogenen Daten.

**DSGVO/Security-Note:** OWASP CSV-Injection (CWE-1236). Bei Klient-Daten besonders kritisch.

**Status:** ☐ Offen

---

### Security: Verschlüsselung und Key-Rotation

> Alle Tests zu „Security: Verschlüsselung und Key-Rotation“ sind in [SEKTION D](manual-test-matrix-d.md#sektion-d--entwickler-probes-lokalssh) gelistet (LOKAL/SSH-Probes).

---

### Security: HTTP-Header

> Header-Smoke gegen Prod-Mirror ist in [SEKTION D](manual-test-matrix-d.md#sektion-d--entwickler-probes-lokalssh) (`DEV-SEC-HEAD-01`, LOKAL/SSH).

#### AUD-SEC-HEAD-02 — CSP-Report-Endpoint Payload-Limit

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Security | unauthentifiziert | C || `src/tests/test_security_hardening.py` |

**Code-Referenz:** `core.views.csp_report` (oder Pendant), CSP-Setting `CONTENT_SECURITY_POLICY_REPORT_ONLY` in `anlaufstelle.settings.base`.

**Schritte:**
1. Manuell oder per `curl` einen kleinen, validen CSP-Report posten (`Content-Type: application/csp-report`, kleiner JSON-Body).
2. Endpoint nimmt 204 zurück.
3. Großen Payload simulieren (>1 MB random JSON).
4. Endpoint lehnt mit 4xx (413 Payload Too Large) ab, **nicht** 5xx.
5. Logs prüfen: kein Memory-Spike, keine Disk-Files.

**Erwartetes Ergebnis:**
- Endpoint limitiert Body-Size (z.B. via Caddy oder Django `DATA_UPLOAD_MAX_MEMORY_SIZE`).
- Kein DoS-Vektor.

**Status:** ☐ Offen

---

### Security: Ops-/Self-Hosting-Härtung

> Alle Tests zu „Security: Ops-/Self-Hosting-Härtung“ — DB-Rollen, Backup, Restore, Media-Volume, Migrations-Drift, Retention-Cron, Healthcheck — sind in [SEKTION D](manual-test-matrix-d.md#sektion-d--entwickler-probes-lokalssh) unter `D.OPS` gelistet. DSGVO-Bezug: Art. 5 Abs. 1 lit. e (Speicherbegrenzung) für `DEV-OPS-07`, Art. 32 (TOM) für `DEV-OPS-01`/`DEV-OPS-03`. Tracking-Issue: #903.

# ADR-015: MFA-Verfahren — TOTP + HMAC-gespeicherte Backup-Codes

- **Status:** Accepted
- **Date:** 2026-05-01
- **Deciders:** Tobias Nix

## Context

Sozialarbeit verarbeitet besonders schutzbedürftige Personendaten (DSGVO Art. 9). Ein bloßer Passwort-Login ist für privilegierte Rollen (Admin, Leitung) zu schwach — Phishing und Credential-Stuffing müssen einen zweiten Faktor sehen. Anforderungen:

- **Offline-fähig** — Streetwork-Geräte sind nicht immer im Netz, ein zweiter Faktor darf nicht jedes Login blockieren.
- **Self-Service-Setup ohne Admin-Hop** — eine neue Mitarbeiterin soll TOTP ohne Helpdesk einrichten können.
- **Recovery ohne Klartext-Speicher** — verlorene Authenticator-App darf nicht zur Datenfreigabe an Admins führen.
- **Per-Facility-Pflicht aktivierbar** — kleine Träger ohne 2FA-Erfahrung sollen mit Opt-in starten, große Träger pflicht-aktivieren können.

## Decision

- **TOTP (RFC 6238)** als primärer zweiter Faktor — Standard auf jeder Authenticator-App (Google Authenticator, Authy, FreeOTP, Bitwarden), kein Vendor-Lock-in. Implementierung über `django-otp` mit TOTPDevice — Setup in [`src/core/views/mfa.py`](../../src/core/views/mfa.py). Das TOTP-Secret liegt **at rest Fernet-verschlüsselt** vor ([ADR-031](031-totp-secret-at-rest.md), Refs #1362).
- **Backup-Codes** als sekundärer Faktor: zehn zufällige 128-Bit-Codes (22 Base64url-Zeichen), gespeichert als zweckgebundene 96-Bit-HMAC-Präfixe mit aus `SECRET_KEY` abgeleitetem Schlüssel. Verbrauchte Codes werden gelöscht; alte SHA-Digests und das schwache Altformat sind ungültig (#2529).
- **Rollenbasierte Pflicht:** `Admin` und `Lead` haben `is_mfa_enforced=True` per Rolle. Pro Facility lässt sich das auf alle Rollen ausweiten (`Settings.mfa_enforced_facility_wide=True`). Mitarbeitende erhalten die `setup`-Aufforderung beim ersten Login nach Aktivierung.
- **Sudo-Mode für sensible Operationen** verlangt eine erneute Bestätigung mit Passwort und bei bestätigtem TOTP-Gerät zusätzlich TOTP oder Backup-Code; das Fenster gilt standardmäßig 15 Minuten.
- **Recovery-Flow:** Ein Backup-Code bestätigt den zweiten Faktor, ersetzt aber kein Gerät. „Neues Gerät einrichten“ verlangt Bestands-MFA und Sudo; erst die Quittung der neuen Backup-Codes ersetzt bestätigte TOTP-Geräte, Passkeys bleiben.

## Consequences

- **+** TOTP funktioniert offline; das Streetwork-Gerät kann ohne Netz authentisieren.
- **+** Ein Datenbank-Digest allein ist ohne den getrennten Serverschlüssel kein Offline-Prüforakel; 128 Bit Eingabezufall und 96 Bit HMAC-Präfix sind unterschiedliche Größen.
- **+** Sudo-Mode entkoppelt den langen App-Session-Lebenslauf von der schmalen Berechtigung für DSGVO-Aktionen.
- **+** `django-otp` ist eine etablierte Library — keine eigene Krypto, kein eigenes Replay-Window.
- **−** Keine Hardware-Token-Unterstützung im Default. WebAuthn/FIDO2 wäre stärker (phishing-resistent), aber für die Zielnutzergruppen aktuell zu hohe Onboarding-Hürde.
- **−** Backup-Codes müssen ausgedruckt oder im Passwortmanager gespeichert werden — Mitarbeitende, die das nicht tun, verlieren beim Geräte-Reset den Zugang. Mitigation: Onboarding-Checkliste in [`docs/admin-guide.md`](../admin-guide.md).
- **−** Per-Rollen-Pflicht macht den ersten Login nach Rollenwechsel zu einem Setup-Schritt; UX-Cost wird in Kauf genommen.

## Alternatives considered

- **WebAuthn/FIDO2 als Default:** Verworfen für jetzt — verlangt einen passenden Browser/Authenticator und ist auf älteren Streetwork-Smartphones unzuverlässig. Bleibt eine Option für eine spätere ADR mit optionaler Aktivierung.
- **SMS-OTP:** Verworfen — DSGVO-fragwürdig (Rufnummer als Identifier), SIM-Swap-Anfällig, Kosten pro SMS.
- **E-Mail-OTP:** Verworfen als alleiniger Faktor — die Mailbox ist oft mit demselben Passwort geschützt; effektiv kein zweiter Faktor.
- **Passkeys (WebAuthn-Profil):** Wie WebAuthn — interessant, aber nicht für den Default. Spätere ADR.

## Nachtrag 2026-10-06: Backup-Codes, Recovery und Sudo im geltenden Vertrag (Refs #2422)

Die Entscheidung oben bleibt als historischer Stand erhalten. Mehrere ihrer Aussagen beschreiben den implementierten Vertrag nicht mehr zutreffend; maßgeblich ist der folgende Ist-Stand.

**Backup-Codes (ersetzt den zweiten Punkt unter „Decision" und die Hash-Aussage unter „Consequences"):**

- **Format:** Ein Code ist `secrets.token_urlsafe(16)` — 22 Zeichen aus dem URL-sicheren Base64-Alphabet, keine Base32-Kodierung ([`src/core/services/security/mfa.py`](../../src/core/services/security/mfa.py), `_generate_code`). Das ältere Format `xxxx-xxxx` wird abgewiesen.
- **Entropie und Speicherung:** 128 Bit Eingabezufall; gespeichert wird `base64url(HMAC-SHA256(server_key, code))[:16]` (96 Bit). Der zweckgebundene Schlüssel wird aus `SECRET_KEY` abgeleitet. Frühere Codes mit SHA-Digest und das Altformat sind ungültig; auch eine `SECRET_KEY`-Rotation erfordert neue Codes. Ohne nutzbaren Faktor bleibt der Admin-Reset. Keine Altbestands-Migration (#2529).
- **Verbrauch:** Ein verwendeter Code wird gelöscht. Ab drei verbleibenden Codes weisen die 2FA-Einstellungen auf das Neuerzeugen hin.

**Backup-Code-Anmeldung und Gerätewechsel (ersetzt den Punkt „Recovery-Flow"):**

- Ein Backup-Code am 2FA-Prompt verbraucht den Code, erneuert die Sitzungskennung und meldet mit dem **bestehenden** TOTP-Gerät an ([`src/core/views/mfa.py`](../../src/core/views/mfa.py), `MFAVerifyView`; Audit `BACKUP_CODES_USED`). Er invalidiert kein Gerät und startet keine Neueinrichtung.
- Der erreichbare Wechsel startet über „Neues Gerät einrichten“ unter `/mfa/settings/` (`/mfa/change-device/`), auch bei 2FA-Pflicht. Er verlangt eine MFA-verifizierte Sitzung und Sudo mit Passwort plus TOTP oder Backup-Code. Erst die Quittung der neuen Backup-Codes ersetzt die bisher bestätigten TOTP-Geräte; Passkeys bleiben, Audit `MFA_ENABLED` zählt die ersetzten Geräte. Die freiwillige Deaktivierung führt nach Sudo auf eine Bestätigungsseite statt HTTP 405; deren GET entfernt nichts, der geschützte POST deaktiviert. Gerätewechsel und freiwillige Deaktivierung beenden andere angemeldete Sitzungen des Kontos nicht; bei Verlust oder Diebstahl eines Geräts mit möglicherweise offener Sitzung den Admin-Reset verwenden (Dienstanweisung der Einrichtung). [Verhaltensbeleg](../../src/tests/test_mfa_device_change_flow.py).
- Ein Backup-Code hebt außerdem eine Konto-Sperre auf (`/account/recovery/backup-code/`, `LOGIN_UNLOCK` mit Trigger `backup_code`). Anonyme Fehlversuche erhöhen die Gerätedrossel nicht; 5/min je IP und 10/h je normalisiertem Benutzernamen begrenzen den Pfad. Die authentifizierte Gerätedrossel bleibt bestehen.

**Recovery durch die Administration (ergänzt die Anforderung „Recovery ohne Klartext-Speicher"):** Sind alle Backup-Codes verbraucht oder verloren, setzt die Anwendungsbetreuung (`facility_admin`, nur Konten der eigenen Einrichtung, keine `super_admin`-Konten) oder die Systemadministration (`super_admin`) den zweiten Faktor zurück — nie für das eigene Konto und immer im Sudo-Modus ([`src/core/services/user_access.py`](../../src/core/services/user_access.py), `reset_user_mfa`). Der Reset entfernt alle TOTP-Geräte, Passkeys und Backup-Codes, beendet alle Sitzungen des Kontos und schreibt einen Audit-Eintrag `MFA_RESET_BY_ADMIN` ohne Schlüsselmaterial. Ist 2FA für das Konto verpflichtend, folgt die Neueinrichtung bei der nächsten Anmeldung. Die Administration sieht dabei weder Secret noch Codes.

**Rollenbasierte Pflicht (ersetzt den Punkt „Rollenbasierte Pflicht"):** In Produktion ist 2FA per Rollen-Default für alle fünf Rollen verpflichtend: sofort für `super_admin` und `facility_admin` (`MFA_ENFORCED_ROLES_IMMEDIATE`), nach einer Übergangsfrist von 30 Tagen ab der ersten Anmeldung für `lead`, `staff` und `assistant` (`MFA_ENFORCED_ROLES_GRACE`). Beide Listen sind Umgebungsvariablen; der übergeordnete Schalter `MFA_ENFORCE_PRIVILEGED_ROLES` ist ein Wert des Settings-Moduls. Pro Nutzer (`User.mfa_required`) und einrichtungsweit (`Settings.mfa_enforced_facility_wide`) lässt sich die Pflicht zusätzlich sofort erzwingen. Betrieb und Einstellung: [Admin-Handbuch § 2.7](../admin-guide.md#27-zwei-faktor-authentifizierung-2fa).

**Sudo-Modus (ersetzt den Punkt „Sudo-Mode"):** Sudo ist eine erneute Anmeldung, keine „MFA-Verifizierung in den letzten 15 Minuten". Verlangt werden das Passwort und, wenn das handelnde Konto ein bestätigtes TOTP-Gerät hat, zusätzlich ein aktueller TOTP- oder Backup-Code ([`src/core/views/sudo_mode.py`](../../src/core/views/sudo_mode.py)). Das Fenster gilt `SUDO_MODE_TTL_SECONDS` (Default 900 Sekunden). Sudo schützt unter anderem das Deaktivieren und die erneute Einrichtung der 2FA bei bestehendem Faktor, das Hinzufügen und Entfernen von Passkeys, die Kontoverwaltung einschließlich Entsperren und Zurücksetzen des zweiten Faktors, DSGVO-Exporte sowie die Django-Adminoberfläche.

**Passkeys (ersetzt die Aussage „Keine Hardware-Token-Unterstützung"):** Passkeys und FIDO2-Sicherheitsschlüssel sind als zusätzlicher zweiter Faktor neben TOTP umgesetzt ([ADR-032](032-webauthn-passkeys-2fa.md)); TOTP bleibt der primäre Faktor, eine passwortlose Anmeldung gibt es nicht.

## References

- [`src/core/services/security/mfa.py`](../../src/core/services/security/mfa.py)
- [`src/core/views/mfa.py`](../../src/core/views/mfa.py)
- [`src/core/services/security/sudo_mode.py`](../../src/core/services/security/sudo_mode.py)
- [`docs/admin-guide.md`](../admin-guide.md) (MFA-Setup-Anleitung)
- Refs #521 (TOTP-Einführung), #612 (Account-Lockout-Kontext), #790 (Hash-Backup-Codes)

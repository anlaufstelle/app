# ADR-015: MFA-Verfahren — TOTP + Hash-gespeicherte Backup-Codes

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
- **Backup-Codes** als sekundärer Faktor: 10 zufällige 128-Bit-Codes (Base32-kodiert), serverseitig **nur als Hash** (`bcrypt`-äquivalent über `StaticDevice.token`) gespeichert. Verbrauchte Codes werden gelöscht; nach 3 verbleibenden Codes nervt das UI bis zum Re-Generate (Refs #790).
- **Rollenbasierte Pflicht:** `Admin` und `Lead` haben `is_mfa_enforced=True` per Rolle. Pro Facility lässt sich das auf alle Rollen ausweiten (`Settings.mfa_enforced_facility_wide=True`). Mitarbeitende erhalten die `setup`-Aufforderung beim ersten Login nach Aktivierung.
- **Sudo-Mode für sensible Operationen** (DSGVO-Pakete, Löschungen) verlangt MFA-Verifizierung in den letzten 15 Minuten — ein langlebiger Session-Cookie reicht für die DSGVO-Wirkungs­operationen nicht.
- **Recovery-Flow** läuft über Backup-Codes: ein verifiziertes Backup-Code-Login reaktiviert den Setup-Pfad, alte TOTP-Devices werden invalidiert.

## Consequences

- **+** TOTP funktioniert offline; das Streetwork-Gerät kann ohne Netz authentisieren.
- **+** Hash-only-Backup-Codes machen einen DB-Dump für den Angreifer wertlos — er müsste 128 Bit Brute-Force gegen den Hash gewinnen.
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

- **Format:** Ein Code ist `secrets.token_urlsafe(16)` — 22 Zeichen aus dem URL-sicheren Base64-Alphabet, keine Base32-Kodierung ([`src/core/services/security/mfa.py`](../../src/core/services/security/mfa.py), `_generate_code`). Codes im älteren Format `xxxx-xxxx` werden weiter angenommen.
- **Entropie und Speicherung:** Der Code trägt 128 Bit Zufall. Gespeichert wird nur `sha256(code).hexdigest()[:16]` in `StaticToken.token`, ungesalzen und ohne Arbeitsfaktor (`_hash_code`); eine bcrypt-äquivalente Speicherung gibt es nicht. Der gespeicherte Wert ist ein 64-Bit-Digest: Wer einen Datenbank-Dump besitzt, muss gegen den gespeicherten Wert rund 2^64 Versuche aufwenden, nicht 2^128. Für einmalig nutzbare, bei Verdacht neu erzeugbare Codes ist das weiterhin nicht praktikabel; die Aussage „128 Bit Brute-Force gegen den Hash" gilt aber nicht.
- **Verbrauch:** Ein verwendeter Code wird gelöscht. Ab drei verbleibenden Codes weisen die 2FA-Einstellungen auf das Neuerzeugen hin.

**Backup-Code-Anmeldung und Gerätewechsel (ersetzt den Punkt „Recovery-Flow"):**

- Ein Backup-Code am 2FA-Prompt verbraucht den Code, erneuert die Sitzungskennung und meldet mit dem **bestehenden** TOTP-Gerät an ([`src/core/views/mfa.py`](../../src/core/views/mfa.py), `MFAVerifyView`; Audit `BACKUP_CODES_USED`). Er invalidiert kein Gerät und startet keine Neueinrichtung.
- Serverseitig nimmt `MFASetupView` bei bestehendem Faktor nach dem Sudo-Modus ein Absenden (POST) an, legt ein neues TOTP-Gerät an und ersetzt den Backup-Code-Satz; nach der Quittung der neuen Codes gilt nur noch das neue Gerät, Passkeys bleiben erhalten ([`src/core/views/mfa.py`](../../src/core/views/mfa.py), `MFASetupView`). Über die Oberfläche ist dieser Pfad bei bestehendem TOTP-Gerät nicht erreichbar: Ein Aufruf (GET) von `/mfa/setup/` leitet auf `/mfa/settings/` um, und die Einstellungsseite bietet „2FA einrichten“ nur ohne Gerät an. Einen Gerätewechsel gibt es deshalb nur als Deaktivieren und Neueinrichten (ohne 2FA-Pflicht, Sudo-geschützt) oder als Reset durch die Administration (mit 2FA-Pflicht, siehe unten). Beim Deaktivieren ohne laufendes Sudo-Fenster leitet der Sudo-Schritt per GET auf `/mfa/disable/` zurück; `MFADisableView` nimmt nur POST an und antwortet mit 405, der Faktor bleibt. Erst ein zweiter Klick auf „2FA deaktivieren“ im laufenden Sudo-Fenster entfernt ihn. Die Bedienschritte stehen im Admin-Handbuch, Abschnitt „Wechsel des TOTP-Geräts“; Verhaltensbeleg [`src/tests/test_mfa_recovery_and_revocation_contract.py`](../../src/tests/test_mfa_recovery_and_revocation_contract.py).
- Ein Backup-Code hebt außerdem eine Konto-Sperre auf (`/account/recovery/backup-code/`, `LOGIN_UNLOCK` mit Trigger `backup_code`).

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

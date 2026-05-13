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

- **TOTP (RFC 6238)** als primärer zweiter Faktor — Standard auf jeder Authenticator-App (Google Authenticator, Authy, FreeOTP, Bitwarden), kein Vendor-Lock-in. Implementierung über `django-otp` mit TOTPDevice — Setup in [`src/core/views/mfa.py`](././src/core/views/mfa.py).
- **Backup-Codes** als sekundärer Faktor: 10 zufällige 128-Bit-Codes (Base32-kodiert), serverseitig **nur als Hash** (`bcrypt`-äquivalent über `StaticDevice.token`) gespeichert. Verbrauchte Codes werden gelöscht; nach 3 verbleibenden Codes nervt das UI bis zum Re-Generate.
- **Rollenbasierte Pflicht:** `Admin` und `Lead` haben `is_mfa_enforced=True` per Rolle. Pro Facility lässt sich das auf alle Rollen ausweiten (`Settings.mfa_enforced_facility_wide=True`). Mitarbeitende erhalten die `setup`-Aufforderung beim ersten Login nach Aktivierung.
- **Sudo-Mode für sensible Operationen** (DSGVO-Pakete, Löschungen) verlangt MFA-Verifizierung in den letzten 15 Minuten — ein langlebiger Session-Cookie reicht für die DSGVO-Wirkungs­operationen nicht.
- **Recovery-Flow** läuft über Backup-Codes: ein verifiziertes Backup-Code-Login reaktiviert den Setup-Pfad, alte TOTP-Devices werden invalidiert.

## Consequences

- **+** TOTP funktioniert offline; das Streetwork-Gerät kann ohne Netz authentisieren.
- **+** Hash-only-Backup-Codes machen einen DB-Dump für den Angreifer wertlos — er müsste 128 Bit Brute-Force gegen den Hash gewinnen.
- **+** Sudo-Mode entkoppelt den langen App-Session-Lebenslauf von der schmalen Berechtigung für DSGVO-Aktionen.
- **+** `django-otp` ist eine etablierte Library — keine eigene Krypto, kein eigenes Replay-Window.
- **−** Keine Hardware-Token-Unterstützung im Default. WebAuthn/FIDO2 wäre stärker (phishing-resistent), aber für die Zielnutzergruppen aktuell zu hohe Onboarding-Hürde.
- **−** Backup-Codes müssen ausgedruckt oder im Passwortmanager gespeichert werden — Mitarbeitende, die das nicht tun, verlieren beim Geräte-Reset den Zugang. Mitigation: Onboarding-Checkliste in [`docs/admin-guide.md`](./admin-guide.md).
- **−** Per-Rollen-Pflicht macht den ersten Login nach Rollenwechsel zu einem Setup-Schritt; UX-Cost wird in Kauf genommen.

## Alternatives considered

- **WebAuthn/FIDO2 als Default:** Verworfen für jetzt — verlangt einen passenden Browser/Authenticator und ist auf älteren Streetwork-Smartphones unzuverlässig. Bleibt eine Option für eine spätere ADR mit optionaler Aktivierung.
- **SMS-OTP:** Verworfen — DSGVO-fragwürdig (Rufnummer als Identifier), SIM-Swap-Anfällig, Kosten pro SMS.
- **E-Mail-OTP:** Verworfen als alleiniger Faktor — die Mailbox ist oft mit demselben Passwort geschützt; effektiv kein zweiter Faktor.
- **Passkeys (WebAuthn-Profil):** Wie WebAuthn — interessant, aber nicht für den Default. Spätere ADR.

## References

- [`src/core/services/mfa.py`](././src/core/services/mfa.py)
- [`src/core/views/mfa.py`](././src/core/views/mfa.py)
- [`src/core/services/sudo_mode.py`](././src/core/services/sudo_mode.py)
- [`docs/admin-guide.md`](./admin-guide.md) (MFA-Setup-Anleitung)
- (TOTP-Einführung) (Account-Lockout-Kontext) (Hash-Backup-Codes)

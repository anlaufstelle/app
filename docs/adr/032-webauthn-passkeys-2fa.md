# ADR-032: Passkeys/WebAuthn als zusätzlicher zweiter Faktor

- **Status:** Accepted
- **Date:** 2026-07-14
- **Deciders:** Tobias Nix

## Context

[ADR-015](015-mfa-totp.md) hat TOTP + gehashte Backup-Codes als zweiten Faktor etabliert und WebAuthn/FIDO2 bewusst auf eine spätere ADR vertagt („interessant, aber nicht für den Default"). [Issue #1492](https://github.com/anlaufstelle/app/issues/1492) holt das nach — aber eng zugeschnitten. Die Maintainer-Entscheidung (Kommentare 2026-07-10/11) legt **nur Etappe A** fest:

- WebAuthn/FIDO2 (Passkeys, Plattform-Authenticatoren, Security-Keys) als **zusätzliche** zweite MFA-Methode **neben** TOTP — kein Ersatz.
- Ein GitHub-artiger **Methoden-Chooser** auf `/mfa/verify/`, wenn mehrere Methoden bestätigt sind.
- **Kein** passwordless Login (Etappe B, vertagt), **keine** org-/facility-weite Policy (Etappe C, eigenes Issue), Push- und E-Mail-Login abgelehnt.

Harte Zwänge des Projekts:

- **CSP `script-src 'self'`** ohne `'unsafe-inline'`/`'unsafe-eval'` (Grund für die `@alpinejs/csp`-Migration). Jede eingebundene JS-Schicht muss ohne `eval`/Inline auskommen.
- **Backup-Codes bleiben der Recovery-Anker** (ADR-015) — niemand darf ohne Wiederherstellungspfad ausgesperrt werden (Streetwork-Geräte, Offline-Fähigkeit).
- **Downgrade-Schutz** (öffentlich zugesagt): ein Passkey-Nutzer darf nicht still auf einen schwächeren Pfad herabgestuft werden.
- Django 6 / Python 3.14, `django-otp` (TOTP + Static) ist bereits im Stack, `OTPMiddleware` aktiv.

Eine vorgeschaltete Etappe 0 hat `django-otp-webauthn` (Stormbase) gegen die reine Server-Bibliothek `py_webauthn` (duo-labs, PyPI `webauthn`) evaluiert.

## Decision

**Wir setzen `django-otp-webauthn` (Pin `>=0.10.0,<0.11`) als zusätzliches django-otp-Device neben TOTP ein — ohne `WebAuthnBackend`, ohne passwordless Login.**

Konkret:

- **Bibliothek:** `django-otp-webauthn` kapselt `py_webauthn` (`webauthn>=3,<4`) als Krypto-/Verifikations-Kern und bringt Device-Modell, Ceremony-State, API-Views und ein **vorgebautes, CSP-striktes JS-Bundle** (Basis `@simplewebauthn/browser`, kein `eval`, keine Inline-Scripts, self-hosted über `collectstatic`) mit. Beide Pakete BSD-3-Clause, EUPL-1.2-kompatibel. Bundle im Pilot auditiert: `otp_webauthn_auth.js` ~15 KB, `otp_webauthn_register.js` ~13 KB, `grep` auf `eval(`/`new Function` liefert 0 Treffer. Die npm-Vendoring-Kette (`scripts/sync_vendor_js.py`) bleibt unberührt (nur für die 4 npm-Libs).
- **Nur zweiter Faktor:** `OTP_WEBAUTHN_ALLOW_PASSWORDLESS_LOGIN = False`, der `WebAuthnBackend` wird **nicht** in `AUTHENTICATION_BACKENDS` registriert. Die Ceremonies laufen ausschließlich in einer bereits per Passwort authentifizierten Session.
- **Passkey nur NEBEN TOTP:** Ein Passkey ist strikt **additiv** — Voraussetzung für die Registrierung ist ein bestätigtes TOTP-Gerät (`WebAuthnRegistrationBeginView`/`…CompleteView.check_can_register`). Damit existiert der Backup-Code-Recovery-Anker (an der TOTP-Einrichtung provisioniert) immer, und es kann kein „passkey-only"-Konto ohne Wiederherstellung entstehen. Das ist die direkte Umsetzung von „zusätzliche zweite Methode **neben** TOTP".
- **Predikat-Verallgemeinerung:** `User.has_confirmed_totp_device` bleibt unangetastet; neu ist `has_confirmed_mfa_device` (TOTP ∨ bestätigter Passkey). MFA-Enforcement-Middleware und `/mfa/verify/` nutzen das allgemeine Prädikat.
- **`mfa_verified`-Glue (sicherheitskritisch):** Unser Enforcement hängt am Session-Flag `mfa_verified`; die Bibliothek markiert nur über django-otps `otp_login`. Dünne Subklassen (`core/views/mfa_webauthn.py`) setzen das Flag **nur** bei erfolgreicher Assertion/Registrierung (`complete_auth`/`post`-2xx) — ein Fehlschlag lässt es unberührt (kein Verify-Bypass). Die URL-Namen bleiben im Namespace `otp_webauthn`, damit das mitgelieferte JS unverändert funktioniert.
- **Methoden-Chooser:** `/mfa/verify/` bietet den Passkey-Button (Bibliotheks-Bundle) und darunter das TOTP-/Backup-Code-Formular an — kein fester Vorrang.
- **Audit:** neue Actions `WEBAUTHN_REGISTERED`, `WEBAUTHN_REMOVED`, `WEBAUTHN_FAILED` (analog `MFA_*`). Erfolgreiche Verifikation wird — wie beim TOTP-Verify — nicht eigens auditiert.
- **Entfernen sudo-pflichtig:** Ein Passkey wird über `MFAPasskeyDeleteView` (`RequireSudoModeMixin`, analog `MFADisableView`) entfernt. Da ein Passkey nur neben TOTP existiert, bleiben nach dem Entfernen stets TOTP + Backup-Codes — kein Downgrade-Risiko. `MFADisableView` löscht beim vollständigen Deaktivieren beide Faktoren.
- **RP-ID/Origin pro Umgebung:** RP-ID = Registrable Domain (Host ohne Schema/Port), Origins = volle URLs. dev/test/e2e → `localhost`; prod/devlive/demo → aus `ALLOWED_HOSTS` abgeleitet. Passkeys sind domaingebunden; dev/demo/prod-Credentials sind bewusst nicht übertragbar.

## Consequences

- **+** Phishing-resistenter zweiter Faktor (WebAuthn-Origin-Bindung) für Nutzer, die einen Plattform-Authenticator/Security-Key haben — ohne TOTP abzuschaffen.
- **+** Krypto liegt in `py_webauthn` (breit eingesetzt, Duo/Cisco-Herkunft); wir schreiben keine eigene WebAuthn-Verifikation.
- **+** Das mitgelieferte JS ist CSP-kompatibel und self-hosted — keine Aufweichung der `script-src 'self'`-Policy, keine Änderung der npm-Vendoring-Kette.
- **+** Backup-Codes bleiben der einheitliche Recovery-Anker; durch die TOTP-Voraussetzung ist ein Lockout durch Passkey-Verlust ausgeschlossen.
- **−** **Bus-Faktor ≈ 1** bei `django-otp-webauthn` (Einzelmaintainer, ~33 Stars). Mitigation: Exit-Strategie ist die direkte Nutzung von `py_webauthn` mit eigenen Views; das Datenmodell (`WebAuthnCredential`) wäre migrierbar. Blast-Radius klein, weil nur die 2FA-Zusatzmethode betroffen wäre — Login (TOTP+Backup) bleibt.
- **−** Vier neue transitive Deps (`webauthn`, `cbor2`, `pyOpenSSL`, `pyasn1[-modules]`) vergrößern die Dependabot-Fläche; `pyOpenSSL` ist upstream langfristig Deprecation-gefährdet.
- **−** 0.x-Versionsstand → API-Bruchrisiko zwischen Minors; daher enger Pin `<0.11`, Bumps mit Changelog-Review.
- **−** Der `mfa_verified`-Glue ist ein sicherheitskritischer Sonderpfad, der bei künftigen Bibliotheks-Updates mitgeprüft werden muss (Positiv- **und** Negativtest vorhanden).

## Alternatives considered

- **`py_webauthn` direkt (Eigenbau der Views/Modelle/JS):** Verworfen für Etappe A — zusätzlich ~1.000–1.500 LOC Server + eigenes Vanilla-JS und die gesamte Negativ-Matrix (Origin-Mismatch, Challenge-Replay, Sign-Count) in Eigenverantwortung, ohne fachlichen Mehrwert gegenüber der geprüften Fertig-Schicht. Bleibt als dokumentierte **Exit-Strategie**.
- **Passkey als eigenständige Primär-Enrollment-Methode (mit eigener Backup-Code-Quittung für passkey-only-Nutzer):** Verworfen für diesen PR — die Bibliothek registriert Credentials sofort `confirmed`, was ein sauberes Zwei-Schritt-Backup-Gate im JS-getriebenen Async-Flow verkompliziert. Die TOTP-Voraussetzung liefert denselben Recovery-Schutz mit deutlich kleinerer Angriffs-/Fehlerfläche.
- **Sudo-Mode-Re-Auth per Passkey:** Als Folge-Issue vertagt (hält den PR klein); Sudo bleibt Passwort + TOTP/Backup-Code.
- **`WebAuthnBackend` / passwordless:** Bewusst nicht aktiviert (Etappe B).
- **Org-/facility-weite Passkey-Policy:** Etappe C, eigenes Issue.

## References

- [Issue #1492](https://github.com/anlaufstelle/app/issues/1492) — Passkey/WebAuthn-Support (Etappe A)
- [ADR-015](015-mfa-totp.md) — TOTP + Backup-Codes (referenziert, nicht superseded)
- [ADR-031](031-totp-secret-at-rest.md) — TOTP-Secret at rest
- Code: [`src/core/views/mfa_webauthn.py`](../../src/core/views/mfa_webauthn.py), [`src/core/webauthn_urls.py`](../../src/core/webauthn_urls.py), [`src/core/middleware/mfa.py`](../../src/core/middleware/mfa.py), [`src/core/models/user.py`](../../src/core/models/user.py)
- Bibliotheken: `django-otp-webauthn` (BSD-3-Clause), `py_webauthn`/`webauthn` (BSD-3-Clause), `@simplewebauthn/browser` (MIT)

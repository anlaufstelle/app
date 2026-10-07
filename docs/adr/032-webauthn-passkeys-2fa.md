# ADR-032: Passkeys/WebAuthn als zusätzlicher zweiter Faktor

- **Status:** Accepted
- **Date:** 2026-07-14
- **Deciders:** Tobias Nix

## Context

[ADR-015](015-mfa-totp.md) hat TOTP + gehashte Backup-Codes als zweiten Faktor etabliert und WebAuthn/FIDO2 bewusst auf eine spätere ADR vertagt („interessant, aber nicht für den Default"). Issue #1492 holt das nach — aber eng zugeschnitten. Die Maintainer-Entscheidung (Kommentare 2026-07-10/11) legt **nur Etappe A** fest:

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
- **Passkey nur NEBEN TOTP, nur in verifizierter Session:** Ein Passkey ist strikt **additiv**. `check_can_register` (`WebAuthnRegistrationBeginView`/`…CompleteView`) gated die Registrierung an zwei Bedingungen: (1) eine bereits 2FA-verifizierte Session (`mfa_verified`, sonst `403 mfa_verification_required`) und (2) ein bestätigtes TOTP-Gerät (sonst `400 totp_required`). (1) ist die Sicherheitsgrenze: die `/webauthn/`-Endpunkte sind von der MFA-Enforcement ausgenommen (die Assertion muss vor gesetztem `mfa_verified` erreichbar sein), also darf die **Registrierung** nicht ihrerseits einen neuen Authenticator über eine nur per Passwort authentifizierte, unverifizierte Session einschleusen — sonst genügte das Passwort des Opfers, um sich per selbst-registriertem Passkey zu verifizieren. (2) sorgt dafür, dass der Backup-Code-Recovery-Anker (an der TOTP-Einrichtung provisioniert) immer existiert und kein „passkey-only"-Konto ohne Wiederherstellung entsteht. Das ist die direkte Umsetzung von „zusätzliche zweite Methode **neben** TOTP".
- **Predikat-Verallgemeinerung:** `User.has_confirmed_totp_device` bleibt unangetastet; neu ist `has_confirmed_mfa_device` (TOTP ∨ bestätigter Passkey). MFA-Enforcement-Middleware und `/mfa/verify/` nutzen das allgemeine Prädikat.
- **`mfa_verified`-Glue (sicherheitskritisch):** Unser Enforcement hängt am Session-Flag `mfa_verified`; die Bibliothek markiert nur über django-otps `otp_login`. Dünne Subklassen (`core/views/mfa_webauthn.py`) leiten das Flag **ausschließlich aus einer erfolgreichen Assertion** ab (`WebAuthnAuthenticationCompleteView.complete_auth` — Besitznachweis eines bestehenden Faktors) — ein Fehlschlag lässt es unberührt (kein Verify-Bypass). Die **Registrierung** setzt das Flag bewusst **nicht** (`WebAuthnRegistrationCompleteView.post` auditiert nur): sie ist kein Besitznachweis des zweiten Faktors und läuft ohnehin nur in einer bereits verifizierten Session (siehe „Passkey nur NEBEN TOTP, nur in verifizierter Session"). Die URL-Namen bleiben im Namespace `otp_webauthn`, damit das mitgelieferte JS unverändert funktioniert.
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

- Issue #1492 — Passkey/WebAuthn-Support (Etappe A)
- [ADR-015](015-mfa-totp.md) — TOTP + Backup-Codes (referenziert, nicht superseded)
- [ADR-031](031-totp-secret-at-rest.md) — TOTP-Secret at rest
- Code: [`src/core/views/mfa_webauthn.py`](../../src/core/views/mfa_webauthn.py), [`src/core/webauthn_urls.py`](../../src/core/webauthn_urls.py), [`src/core/middleware/mfa.py`](../../src/core/middleware/mfa.py), [`src/core/models/user.py`](../../src/core/models/user.py)
- Bibliotheken: `django-otp-webauthn` (BSD-3-Clause), `py_webauthn`/`webauthn` (BSD-3-Clause), `@simplewebauthn/browser` (MIT)

## Nachtrag 2026-10-07: Sudo-Bedingung, Ort des Sitzungs-Guards und Versions-Pin (Refs #2422)

Die Decision oben bleibt als historischer Stand erhalten. In drei Punkten beschreibt sie den implementierten Vertrag nicht mehr zutreffend; maßgeblich ist der folgende Ist-Stand.

- **Drei Bedingungen für die Passkey-Registrierung, nicht zwei.** Begin- und Complete-Endpunkt der Registrierung prüfen in dieser Reihenfolge: (1) eine per zweitem Faktor bestätigte Sitzung (`mfa_verified`, sonst `403 mfa_verification_required`); (2) bei bereits bestätigtem Faktor (`has_confirmed_mfa_device`) ein laufendes Sudo-Fenster (sonst `403 sudo_required`, #2328); (3) ein bestätigtes TOTP-Gerät (sonst `400 totp_required`). Ohne bestätigten Faktor und bei `SUDO_MODE_ENABLED = False` entfällt (2). Belege: `src/tests/test_mfa_passkey_registration_sudo_guard.py` (`test_begin_refused`, `test_complete_refused_before_any_ceremony`, `test_begin_returns_options`, `test_complete_passes_the_sudo_barrier`, `test_unverified_session_sees_the_mfa_barrier_first`, `test_passkey_only_account_needs_sudo_before_the_totp_rule`, `test_sudo_switched_off_needs_no_window`).
- **Ort der Prüfungen.** (1) und (2) sitzen nicht in `check_can_register`, sondern in `_RequireVerifiedSessionAndTOTPMixin.initial()` ([`src/core/views/mfa_webauthn.py`](../../src/core/views/mfa_webauthn.py)) und greifen vor dem Vorcheck der Bibliothek in `super().initial()` (#2237). In `check_can_register` liegt nur noch (3); die Methode ist ausschließlich hinter dem Guard erreichbar. `src/tests/test_mfa_guard_reachability.py` hält diese Reihenfolge fest (`test_app_guard_in_initial_greift_vor_der_bibliothek`, `test_app_guard_laeuft_im_mro_vor_der_bibliotheks_zeremonie`, `test_check_can_register_sieht_ausschliesslich_verifizierte_sitzungen`).
- **Pin.** [`requirements.in`](../../requirements.in) pinnt `django-otp-webauthn>=0.10.3,<0.11`, nicht `>=0.10.0`. Ab 0.10.3 prüft die Bibliothek in der Registrierung zusätzlich `user.is_verified()`, allerdings nur für Konten mit bestätigtem Gerät; ohne bestätigtes Gerät bleibt der Guard in `initial()` die einzige Schranke vor der Registrierung.
- **Tests des `mfa_verified`-Glues.** In `src/tests/test_mfa_webauthn.py` belegt `test_authentication_complete_marks_session_verified` den Positivfall; `test_registration_complete_audits_without_setting_flag` und `test_registration_complete_does_not_set_flag_on_error` belegen, dass die Registrierung das Flag nicht setzt, `test_registration_begin_rejected_in_unverified_session` und `test_registration_complete_rejected_in_unverified_session` die Sitzungsschranke. Den Sudo-Pfad decken die oben genannten Tests positiv und negativ ab. Einen Test, der eine fehlgeschlagene Passkey-Prüfung gegen das Flag prüft, enthält die Datei nicht.

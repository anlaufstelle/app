# ADR-015 — backup codes and authenticator replacement

This excerpt translates the current contract in [the German ADR-015](../../adr/015-mfa-totp.md).

Ten codes carry 128 bits of input randomness each (22 Base64url characters). Store only `base64url(HMAC-SHA256(server_key, code))[:16]`, a 96-bit prefix whose purpose-separated key derives from `SECRET_KEY`. A database digest alone is no offline verification oracle without this server key. Earlier SHA digests and the old input format are rejected; no legacy-data migration is provided. Changing `SECRET_KEY` also invalidates issued codes. Generate fresh codes while TOTP works; otherwise use the admin MFA reset.

A backup-code sign-in confirms the existing factor and does not change devices. **Neues Gerät einrichten** (set up a new device) uses `/mfa/change-device/`, also with mandatory 2FA, after verified existing MFA and sudo with password plus TOTP or backup code. Only acknowledgement of new backup codes removes previously confirmed TOTP devices; passkeys remain. `MFA_ENABLED` records the number replaced. Voluntary deactivation returns from sudo to a confirmation page whose GET removes nothing and whose protected POST disables the factor. Device change and voluntary deactivation do not end other signed-in sessions of the account; if a device with a possibly open session is lost or stolen, use the admin reset (the facility's service instruction).

Anonymous backup-code unlocking uses 5/min per IP and 10/h per normalized username without increasing device throttling; authenticated device throttling remains. Admin MFA reset is independent of the bounded session scan. A user-to-session index remains a post-1.0 idea.

<!-- translation-source: docs/adr/015-mfa-totp.md -->
<!-- translation-version: v0.99.7 -->
<!-- translation-date: 2026-10-10 -->
<!-- source-hash: e3560d9d1fdfc51f1b5dff075d0dcc0bd6e3806c -->

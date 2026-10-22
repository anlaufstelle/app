# FAQ — two-factor recovery

This excerpt translates the recovery instructions in [the German FAQ](../faq.md#4-ich-habe-mein-handy-verloren--wie-komme-ich-wieder-rein).

Use an unused valid backup code at the 2FA sign-in. Then choose **Neues Gerät einrichten** (set up a new device) at `/mfa/settings/`, also available with mandatory 2FA. Sudo requires your password and a current TOTP or another unused backup code. Scan the new QR code, confirm its TOTP code, store and acknowledge the new backup codes. Only acknowledgement replaces previously confirmed TOTP devices; passkeys remain. A backup-code sign-in alone does not replace the device.

Earlier codes with SHA digests and the old input format are invalid; changing `SECRET_KEY` also invalidates codes. Generate new codes while your authenticator app works. If no usable factor or valid backup code remains, ask application support for an admin MFA reset. A voluntary 2FA deactivation now returns from sudo to a confirmation page; only its POST disables the factor.

<!-- translation-source: docs/faq.md -->
<!-- translation-version: v0.99.8 -->
<!-- translation-date: 2026-10-14 -->
<!-- source-hash: edc705222b96dfa6ea4f70f1f2e353348b3916a7 -->

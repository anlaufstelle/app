# ADR-006: Fernet/MultiFernet für Feldverschlüsselung

- **Status:** Accepted
- **Date:** 2026-03-20
- **Deciders:** Tobias Nix

## Context

Sensitive Felder (verschlüsselbare Anteile von Klientel- und Ereignisdaten, File-Vault-Anhänge) müssen at-rest verschlüsselt sein, damit ein DB-Dump allein nicht ausreicht, um persönliche Daten zu rekonstruieren. Anforderungen:

- Authentisierte Verschlüsselung (Modifikation muss erkennbar sein).
- Key-Rotation ohne Re-Encrypt-Großaktion (alte Keys lesen, neuer Key schreibt).
- **Fail-closed**: ein fehlender oder unleserlicher Key führt zu Fehler, niemals zu Klartext-Rückgabe.
- Kompatibel mit Django-/PostgreSQL-Toolchain ohne externe KMS-Abhängigkeit (HSM, Cloud-KMS) — die Zielinstallationen sind oft on-premises bei kleinen Trägern.

## Decision

- **Cryptography-Library `Fernet`** (AES-128-CBC + HMAC-SHA256 + URL-safe Base64) als Verschlüsselungsprimitive.
- **`MultiFernet`** für Key-Rotation: `ENCRYPTION_KEYS` nimmt eine kommaseparierte Liste; der **erste** Key verschlüsselt, alle Keys können entschlüsseln. Single-Key-`ENCRYPTION_KEY` bleibt als Backward-Compat erhalten.
- **Fail-closed**: fehlt der Key, wirft [`get_fernet()`](../../src/core/services/encryption.py) `EncryptionKeyMissing`. Beschädigte Tokens werfen `InvalidToken` — kein Stillschweigen.
- **`lru_cache`** auf `get_fernet()` für Bulk-Decrypt-Pfade (Datenexport, Retention).
- Architektur-Test stellt sicher, dass Models verschlüsselte Felder nicht über `bulk_create` an der Encryption vorbeispeichern können.

## Consequences

- **+** Key-Rotation ist eine Konfigurationsänderung, nicht ein Migrations­skript.
- **+** Fail-closed-Semantik verhindert das gefährlichste Anti-Pattern (verschlüsselte Felder, die bei Fehlern als Klartext zurückkommen).
- **+** Keine externe KMS-Abhängigkeit — passt zum On-Prem-Zielumfeld.
- **−** AES-128 (nicht -256). Für Bedrohungsmodell ausreichend, aber Fernet ist als Schema fixiert; eine Umstellung auf AES-GCM wäre eine separate ADR und ein Daten-Migrations­schritt.
- **−** Key-Material liegt in Settings/Env — Verlust = Datenverlust für betroffene Felder. Backup-Pflicht und Onboarding-Hinweis im Ops-Runbook.

## Alternatives considered

- **`pgcrypto` (DB-seitig):** Verworfen — Key müsste der DB anvertraut werden; ein DB-Dump enthielte den Key oder den Klartext beim Decrypt-Pfad.
- **AES-GCM direkt (z.B. via `cryptography.AEAD`):** Stärker, aber Eigenbau-Schema ohne Library-Standard. Risiko, eigene Fehler einzubauen, höher als der Gewinn.
- **Externes KMS (AWS/GCP/Vault):** Verworfen für Default-Setup; bleibt eine Option für künftige ADR, falls Zielumfeld es verlangt.

## References

- [`src/core/services/encryption.py`](../../src/core/services/encryption.py)
- [`docs/admin-guide.md`](../admin-guide.md) (AES-GCM/Fernet-Hinweis)
- Commits: `a4a323e` (Einführung), `b568691` (fail-closed), `61e3200` (lru_cache)

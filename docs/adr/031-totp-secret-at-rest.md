# ADR-031: TOTP-Secret at rest verschlüsseln (MultiFernet)

- **Status:** Accepted
- **Date:** 2026-07-11
- **Deciders:** Tobias Nix

## Context

Das Projekt härtet bewusst gegen DB-/Backup-Leaks: Backup-Codes liegen nur als
Hash vor ([ADR-015](015-mfa-totp.md), #790),
besonders schützenswerte PII ist Fernet-verschlüsselt ([ADR-014](014-encrypted-file-vault.md)).
Der **primäre** zweite Faktor — das TOTP-Secret — lag dagegen im **Klartext**:
`django-otp` speichert `TOTPDevice.key` als 40-stelligen Hex-String
(`otp_totp_totpdevice.key`, Default `random_hex(20)`).

Ein Leser eines DB-Dumps oder einer Replica konnte damit jeden Authenticator
rekonstruieren und gültige TOTP-Codes erzeugen — auch für die MFA-**pflichtigen**
Rollen `super_admin`/`facility_admin`. Das widersprach direkt dem #790-Ziel
(„DB-Leak ≠ MFA-Kompromittierung"). Befund #1362
(Sicherheitsreview 2026-07-02, v0.16.0); Maintainer-Entscheid: **verschlüsseln**
(Variante a), nicht bloß als Restrisiko dokumentieren.

Zwänge:

- **Vorhandene Krypto nutzen** — MultiFernet-Setup (`ENCRYPTION_KEYS`,
 [`file_vault/encryption.py`](../../src/core/services/file_vault/encryption.py))
 inkl. Key-Rotation; keine neue Schlüsselverwaltung.
- **`django-otp` bleibt** die erprobte TOTP-Engine (Verify/Throttle/Provisioning);
 keine Eigen-Krypto für den zweiten Faktor.
- **Keine Fremd-App-Migrationshistorie kapern** — `otp_totp` gehört uns nicht.
- **Unumstößlich korrekt** — ein Migrationsfehler sperrt alle MFA-pflichtigen
 Admins aus.

## Decision

Wir führen ein **Proxy-Modell** `core.EncryptedTOTPDevice` (proxy über
`django_otp…TOTPDevice`, teilt sich die Tabelle `otp_totp_totpdevice`) ein, das
das Secret **at rest mit dem vorhandenen MultiFernet** ver-/entschlüsselt:

- **Lesen** — `bin_key` entschlüsselt `key` transparent
 ([`core/services/security/totp.py`](../../src/core/services/security/totp.py)).
 `verify_token`/`config_url` nutzen intern `bin_key` und funktionieren dadurch
 unverändert weiter. Der Lesepfad ist **format-tolerant**: ein noch nicht
 migrierter Klartext-Key wird unverändert verarbeitet.
- **Schreiben** — `save()` verschlüsselt ein Klartext-Secret (z. B. der
 `default_key` bei `create()`) **vor** dem Persistieren; bereits verschlüsselte
 Keys bleiben unangetastet (idempotent).
- **Zugriffsschicht** — die gesamte Applikation (Views, Sudo-Re-Auth,
 Compliance-Zählung, `User.has_confirmed_totp_device`) greift ausschließlich
 über `EncryptedTOTPDevice` zu; das nackte `TOTPDevice` wird nur noch von der
 Proxy-Definition und der Datenmigration referenziert.

**Datenmigration** [`0102_totp_secret_at_rest`](../../src/core/migrations/0102_totp_secret_at_rest.py)
im **eigenen (core-)Namespace**:

1. registriert das Proxy-Modell (kein Tabellen-DDL),
2. weitet `otp_totp_totpdevice.key` per reversiblem `RunSQL` von `varchar(80)`
 auf `varchar(255)` — ein Fernet-Token ist ~120–140 Zeichen lang und passte
 sonst nicht in die Spalte,
3. verschlüsselt bestehende Klartext-Keys **in place** (`RunPython`).

Die Datenmigration ist **idempotent** (Format-Erkennung Fernet-Token vs.
40-Hex — dieselbe Logik nutzt das Modell) und **reversibel**: das Reverse
entschlüsselt zurück und verengt die Spalte wieder; die Operationsreihenfolge
ist so gewählt, dass beim Zurückdrehen zuerst entschlüsselt (→ Klartext passt
wieder in `varchar(80)`) und dann verengt wird.

**Key-Rotation** bleibt über MultiFernet möglich: Alt-Tokens bleiben nach
Voranstellen eines neuen Primärschlüssels lesbar (Mehr-Key-Decrypt);
`reencrypt_fields` wickelt TOTP-Secrets zusätzlich aktiv unter den neuen
Primärschlüssel um, sodass ein ausrangierter Schlüssel entfernt werden kann.

## Consequences

- **+** Ein DB-/Backup-/Replica-Leak liefert nur noch Chiffretext des
 TOTP-Secrets — Konsistenz mit #790/ADR-014.
- **+** `django-otp`-Logik (Verify, Throttle, `config_url`) bleibt unverändert;
 nur die Secret-Persistenz ist gekapselt → minimales Divergenzrisiko auf dem
 Admin-Lockout-kritischen Pfad.
- **+** **Rewrap-Concurrency-sicher.** `verify_token` schreibt intern ein
 Voll-`save()` inkl. `key` (Throttle-/`last_t`-/`drift`-Update). Damit ein
 solcher Verify keinen parallelen `reencrypt_fields`-Rewrap überschreibt (Lost
 Update → Gerät bliebe still unter dem Alt-Schlüssel → MFA-Lockout beim
 Entfernen des Alt-Schlüssels), lässt `EncryptedTOTPDevice.save()` bei einem
 Voll-`save()` auf eine bestehende Zeile mit bereits verschlüsseltem Secret die
 `key`-Spalte unangetastet. Das Secret wird nur noch bewusst geschrieben
 (Klartext-Verschlüsselung bei `create()`/Provisioning bzw.
 `reencrypt_fields.update(key=…)`), nie durch einen Verify.
- **+** `otp_totp`-Migrationsdateien bleiben unberührt; der Schemaeingriff läuft
 als reversibles `RunSQL` in unserem Namespace.
- **−** Die DB-Spalte `otp_totp_totpdevice.key` ist jetzt breiter als die
 `max_length=80` des Fremd-Modells (bewusste, dokumentierte Divergenz; für ein
 `CharField` beim Schreiben folgenlos, da wir das Secret nie per `full_clean`
 validieren).
- **− Upgrade-Hazard (`django-otp`-Bump).** Ein künftiges `django-otp`-Release
 (Dependabot bumpt automatisch) könnte die `key`-Feldbreite ändern und dazu
 eine `otp_totp`-Migration mit `AlterField('key', max_length=80)` ausliefern.
 Django erzeugte daraus `ALTER … TYPE varchar(80)`, das an unseren bereits
 gespeicherten **~140-Zeichen-Fernet-Tokens hart scheitert** (`value too long
 for type character varying(80)`) und `migrate` blockiert — im schlimmsten Fall
 mitten im Deploy. **Gegenmaßnahme:** Bei jedem `django-otp`-Bump die
 generierten Migrationen prüfen; taucht ein `AlterField` auf `key` auf, im
 core-Namespace eine **Nachfolge-Migration** hinterhersetzen, die die Spalte
 wieder auf `varchar(255)` weitet (analog Migration 0102, nach der
 `otp_totp`-Migration einsortiert). Der Guard-Test
 `TestTotpKeyColumnWidthGuard` in
 [`test_totp_secret_at_rest.py`](../../src/tests/test_totp_secret_at_rest.py)
 prüft die effektive `varchar`-Breite und fängt eine unbeabsichtigte
 Re-Verengung früh (rot im Testlauf, bevor es produktiv aufschlägt).
- **−** Ohne `ENCRYPTION_KEY(S)` ist kein TOTP-Login möglich — derselbe
 Boot-Time-Hard-Fail wie für alle Fernet-Felder gilt (bewusst).
- **−** Ein Klartext-`TOTPDevice`-Zugriff (Basisklasse) an neuem Code würde am
 verschlüsselten Secret scheitern; die Zugriffsschicht ist deshalb auf
 `EncryptedTOTPDevice` vereinheitlicht.

## Alternatives considered

- **Restrisiko nur dokumentieren (Variante b, threat-model):** Verworfen — der
 Maintainer-Entscheid verlangt aktive Verschlüsselung; „nur dokumentieren"
 hätte die #790-Inkonsistenz belassen.
- **Eigenes konkretes Device-Modell + Tabelle + Daten-Umzug:** Verworfen
 hätte alle TOTP-Felder (Throttling/Timestamps/`last_t`) reproduzieren und die
 Daten in eine neue Tabelle umziehen müssen; mehr Fläche für subtile
 Divergenz und ein riskanterer Reverse-Pfad. Das Proxy teilt die erprobte
 Engine 1:1.
- **Spalte über eine `otp_totp`-Migration weiten:** Verworfen — das hieße,
 Fremd-App-Migrationshistorie zu kapern. Reversibles `RunSQL` im core-Namespace
 erreicht dasselbe ohne Eingriff in fremde Migrationsdateien.
- **Transparente DB-at-rest-Verschlüsselung (TDE/Volume):** Ergänzend sinnvoll,
 aber kein Ersatz — schützt nicht gegen einen kompromittierten DB-User oder
 einen entschlüsselten Logical-Dump (bleibt als Operator-Kontrolle im
 threat-model vermerkt).

## References

- [`src/core/models/mfa.py`](../../src/core/models/mfa.py) (Proxy-Modell)
- [`src/core/services/security/totp.py`](../../src/core/services/security/totp.py) (Ver-/Entschlüsselung + Format-Erkennung)
- [`src/core/migrations/0102_totp_secret_at_rest.py`](../../src/core/migrations/0102_totp_secret_at_rest.py) (idempotente, reversible Datenmigration)
- [`src/tests/test_totp_secret_at_rest.py`](../../src/tests/test_totp_secret_at_rest.py)
- [ADR-015](015-mfa-totp.md) (MFA-Verfahren), [ADR-014](014-encrypted-file-vault.md) (Fernet-Feldverschlüsselung)
- [`docs/threat-model.md`](../threat-model.md) (Asset „MFA-Secrets"), [`docs/security-notes.md`](../security-notes.md)
- Refs #1362, #790

# ADR-049: One-Command-Bootstrap für das lokale Setup

- **Status:** Accepted
- **Date:** 2026-08-26
- **Deciders:** Tobias Nix
- **Refs:** #1062, #1977, #1040, #799, ADR-018, ADR-020, ADR-033

## Context

Ein frischer Klon brauchte vor der ersten Anmeldung vier Handgriffe: einen
Fernet-Schlüssel erzeugen und exportieren, `docker compose up --build -d`,
`manage.py migrate`, `manage.py seed`. Jeder davon ist eine Stelle, an der
Mitwirkende abspringen — und drei davon sind reine Mechanik ohne
Erkenntnisgewinn.

Der offensichtliche Kurzschluss wäre, den geteilten `docker-entrypoint.sh`
migrieren zu lassen. Genau das ist seit #802
(C-34) bewusst ausgeschlossen: der Entrypoint läuft in **jeder** Web-Replica
**jedes** Stacks. Migrierte er, konkurrierten parallele Replicas beim Deploy um
den Migrate-Lock, lange `RunPython`-Schritte blockierten Worker, und die
Deploy-Gates aus `docker-migrate.sh` (Rollen-Posture nach
#1002,
Ownership-Normalisierung nach #1085)
wären umgangen.

Der Seed ist zusätzlich fail-closed über `SEED_ALLOWED` gesperrt
(#1040, S1). Er legt
Konten mit dokumentiertem Passwort (`anlaufstelle2026`) an — auf einem
Produktionssystem wäre die Bequemlichkeit ein Totalschaden.

Und der Schlüssel: #1977
hat einen fest verdrahteten Dev-Schlüssel in der ausgelieferten Compose
verworfen. Er wäre für jeden Klon derselbe und damit keiner. Seither bricht das
lokale Compose ohne `ENCRYPTION_KEY` fail-loud ab — korrekt, aber unvereinbar
mit einem Ein-Kommando-Start.

## Decision

### 1. Der Auto-Pfad lebt in einem eigenen Dienst, nicht im Entrypoint

`docker-compose.yml` bekommt einen einmaligen Dienst `init`, der auf eine
gesunde Datenbank wartet, das Schema migriert, danach Demo-Daten anlegt und
sich beendet. `web` startet über `service_completed_successfully` erst nach
seinem erfolgreichen Ende — die versprochene Login-Seite zeigt sonst einen
Datenbankfehler statt einer Anmeldemaske.

`docker-entrypoint.sh` und `docker-migrate.sh` bleiben unverändert. Der
getrennte Migrationspfad aus [docs/ops-runbook.md § 1.2](../ops-runbook.md)
ist weiterhin der einzige Weg für Produktion, Staging, das dev-Deployment und
die Demo.

### 2. Kein Bootstrap ohne eigenes Compose-Kommando im Deployment

Der Auto-Bootstrap existiert **ausschließlich** in `docker-compose.yml`. Kein
Deployment-Stack (`prod`, `staging`, `dev`, `demo`) bekommt einen
`init`-Dienst, ein `SEED_ALLOWED` oder einen Seed-Aufruf. Die Ersteinrichtung
in Produktion bleibt `manage.py create_super_admin` (ADR-018), begleitet von
`scripts/initial-setup.sh`.

### 3. „Nur wenn leer" ist eine Command-Entscheidung, keine Shell-Heuristik

`manage.py seed --only-if-empty` seedet genau dann, wenn noch **kein einziges
Benutzerkonto** existiert. Die Probe geht auf `core_user` — nicht auf einen
Migrations-Marker: ein migriertes, aber ungefülltes Schema ist genau der
Zustand, den der Bootstrap befüllen soll. `core_user` steht bewusst nicht unter
RLS (Migration 0047), die Probe sieht also auch ohne Bypass-Kontext alle Zeilen.

Die Entscheidung liegt im Command statt im Shell-Skript, weil sie dort testbar
ist und dieselbe Datenbankverbindung sieht wie der Seed selbst.
`--only-if-empty` und `--flush` schließen sich aus, und der
`SEED_ALLOWED`-Guard bleibt die erste Auswertung: der neue Schalter darf
fail-closed nicht in ein stilles „nichts zu tun" verwandeln.

### 4. Der lokale Schlüssel entsteht zufällig auf der Maschine, die ihn braucht

`scripts/local/bootstrap.sh` erzeugt beim allerersten Start einen zufälligen
Fernet-Schlüssel und legt ihn mit Mode 0600 in einem benannten Volume ab
(`localstate`, gemountet auf das Home des Container-Benutzers). Ein gesetztes
`ENCRYPTION_KEY` gewinnt weiterhin und wird unverändert durchgereicht.

Damit bleibt die Aussage aus #1977 gültig — es gibt kein ausgeliefertes
Fest-Geheimnis —, ohne dass vor dem ersten Start ein Handgriff nötig ist. Die
Persistenz ist keine Bequemlichkeit, sondern Bedingung: ein bei jedem Start neu
erzeugter Schlüssel ließe bereits verschlüsselte Entwicklungsdaten unlesbar
zurück.

**Weitergereicht wird der Pfad, nicht der Wert.** Die Container bekommen
`LOCAL_ENCRYPTION_KEY_FILE`; `settings/dev.py` liest die Datei, wenn
`ENCRYPTION_KEY` leer ist. Der naheliegende Weg — der Startbefehl exportiert
den gelesenen Schlüssel in seine Shell — trägt nur bis PID 1:
`docker compose exec`/`run` startet einen neuen Prozess aus der
Container-**Konfiguration**, nicht aus der Umgebung von PID 1. Jedes
`manage.py`-Kommando nach dem Start liefe dann still in den Klartext-Modus
dieser Settings, schriebe Art.-9-Felder unverschlüsselt und bräche auf den
Lesepfaden mit `EncryptionKeyMissing` ab. Über den Pfad sieht jeder Prozess im
Container denselben Schlüssel. Der Rückfall ist strikt opt-in: ohne die
Variable ändert sich nichts — auch nicht in `test.py`/`e2e.py`, die von `dev`
erben.

### 5. Der lokale Webdienst bindet auf Loopback

Der Stack fährt mit Konten hoch, deren Passwort dokumentiert ist. Auf
`0.0.0.0` wäre das dieselbe Lücke, die #799 für Postgres geschlossen hat —
`web` bindet deshalb wie `db` auf `127.0.0.1`.

## Consequences

- `git clone … && cd … && docker compose up` genügt. Gemessen auf dem
  Entwicklungshost (Image vorgebaut): Kaltstart bis HTTP 200 auf `/login/`
  21,2 s, Warmstart 10,9 s — beides deutlich unter dem 60-Sekunden-Ziel aus
  #1062.
- `docker compose exec web python manage.py …` arbeitet mit demselben
  Schlüssel wie der laufende Server; die Kommandos aus README, Ops-Runbook und
  Handtest-Matrix bleiben damit uneingeschränkt gültig.
- Der Quick-Start in README/README.en verliert seine Migrate- und
  Seed-Schritte. Der Architektur-Guard `TestReadmeQuickStart` koppelt beides:
  fällt der `init`-Dienst weg, verlangt er die Schritte im Text zurück.
- Wer lokal mit einem eigenen Schlüssel arbeitet, muss ihn weiterhin selbst
  aufbewahren. Wer keinen setzt, findet ihn im Volume — ein `docker compose
  down -v` verwirft ihn zusammen mit der Datenbank, was für Wegwerf-Daten
  richtig ist.
- ClamAV wird im lokalen Stack zum Profil-Dienst
  (`docker compose --profile clamav up clamav`). `CLAMAV_ENABLED` ist lokal
  ungesetzt, der Scanner war also ohnehin unbenutzt — als profilloser Dienst
  lud jedes `up` trotzdem ein ~1-GB-Image samt Signatur-Update.
- Der lokale Stack läuft weiterhin als Bootstrap-Superuser und meldet
  `/health/` deshalb erwartungsgemäß als `degraded`
  (#1982). Der
  Auto-Bootstrap ändert daran nichts und ist ausdrücklich **kein** Ersatz für
  die Drei-Rollen-Topologie der Produktion (ADR-020, ADR-033).

## Alternatives considered

- **Migration in `docker-entrypoint.sh`** — verworfen: der Entrypoint ist der
  geteilte Startpfad aller Stacks, siehe Context (#802).
- **`docker-migrate.sh` im `init`-Dienst aufrufen** — verworfen: der One-Shot
  trägt zwei Gates für die Drei-Rollen-Topologie der Produktion, die das lokale
  Compose bewusst nicht hat. `check_db_roles` liefe dort bei jedem Start in
  seine Warnung (Exit 2), `normalize_db_ownership` ist als No-op dokumentiert —
  im Erst-Start-Log eines neuen Mitwirkenden wäre beides nur Rauschen. Zudem
  liegt das Skript im Image auf `/app/docker-migrate.sh` und wird vom
  `./src:/app`-Bind-Mount verdeckt (genau der Befund aus #1977).
- **Eigenes `docker-compose.local.yml`** — verworfen: es bräuchte ein `-f`.
  Das Ziel ist ausdrücklich `docker compose up` **ohne** weitere Flags. Der
  Basisdatei einen Bootstrap zu geben ist auch ehrlicher: sie ist bereits die
  lokale Compose, ihr Kopfkommentar sagt das seit #799.
- **Fester Dev-Schlüssel in der Compose** — verworfen in #1977 und hier
  bestätigt: für alle Klone derselbe Wert ist kein Geheimnis.
- **Schlüssel in eine `.env`/`env_file` schreiben, die Compose selbst
  einliest** — verworfen: Compose wertet `env_file` beim Lesen der Datei aus,
  also *bevor* der Bootstrap läuft. Der erste Start hätte keinen Schlüssel
  (Henne-Ei), und die Datei landete im Klon-Verzeichnis statt in einem
  Wegwerf-Volume.
- **Den Schlüssel im Startbefehl in die Shell exportieren** — verworfen, weil
  es nur bis PID 1 trägt: `exec`/`run` sehen ihn nicht (siehe Decision § 4).
- **Seed-Entscheidung im Shell-Skript** (`psql`-Abfrage auf `core_user`) —
  verworfen: ungetestet, außerhalb der Reichweite der Suite und mit einer
  zweiten Datenbankverbindung, die von der des Seeds abweichen kann.
- **Auto-Seed auch für die Demo-Instanz** — nicht nötig: sie hat mit dem
  stündlichen `seed --flush --scale=medium`-Timer bereits ihren eigenen,
  bewusst anderen Reset-Pfad.

## References

- [`docker-compose.yml`](https://github.com/anlaufstelle/app/blob/main/docker-compose.yml) — `init`-Dienst, `localstate`-Volume
- [`scripts/local/bootstrap.sh`](https://github.com/anlaufstelle/app/blob/main/scripts/local/bootstrap.sh) — Schlüssel, Migrate, Seed
- [`src/core/management/commands/seed.py`](https://github.com/anlaufstelle/app/blob/main/src/core/management/commands/seed.py) — `--only-if-empty`
- [`src/tests/test_architecture_guards_ops.py`](https://github.com/anlaufstelle/app/blob/main/src/tests/test_architecture_guards_ops.py) — `TestLocalOneCommandBootstrap`, `TestReadmeQuickStart`
- [docs/ops-runbook.md § 1.2](../ops-runbook.md) — getrennter Migrationspfad der Produktion

# ADR-030: Offline-Sync-Orchestrierung, Replay-Contract & Dead-Letter-Semantik

- **Status:** Accepted
- **Date:** 2026-07-03
- **Deciders:** Tobias Nix
- **Refs:** #1351 (Tiefenanalyse Offline-Modus, P1), #1383 (M6 Sync-Orchestrator), #1384 (M7 Queue-Robustheit), #1385 (M8 Dead-Letter-UI), #1386 (M10 SW-Robustheit), #1387 (M11 Create-422), #1338 (M9 Optimistic-Lock-Token), #1329 (M12 Idempotenz-TTL), [ADR-022](022-offline-snapshot-keys.md) (Offline-Snapshot-Keys)

## Context

[ADR-022](022-offline-snapshot-keys.md) hat die **Vertraulichkeit** des Offline-Modus festgelegt (serverseitig gefilterte Bundles, gerätegebundene non-extractable Keys, TTL-Purge). Die Tiefenanalyse #1351 hat gezeigt, dass die offene Flanke die **Verfügbarkeit/Integrität ungesyncter Arbeit** ist: ungesyncte Offline-Edits konnten auf mehreren Wegen still vernichtet werden, konkurrierende Replays aus mehreren Tabs liefen unkoordiniert (Doppel-Anlage, Idle-Wipe-Race), und der HTTP-Contract zwischen Replay-Client und Server war uneinheitlich (Konflikte/Validierungsfehler verschwanden als scheinbarer 200-Erfolg).

Die P0-Runde (S1, #1352–#1356) hat die stillen Discard-Pfade geschlossen. Diese ADR hält die **P1-Architektur** fest: wie Replays koordiniert werden, welchen Wire-Contract Client und Server teilen, und was mit dauerhaft nicht zustellbarer Arbeit geschieht. Ohne festgeschriebene Invarianten driftet genau dieser Bereich wieder auseinander (jede der drei Schichten — Client-Queue, Server-Views, Service Worker — kann den Contract einseitig brechen).

## Decision

### 1. Sync-Orchestrierung: ein Lauf pro Origin, nicht pro Tab

Alle Replay-/Revalidierungs-Trigger (die früher drei unkoordinierten `online`-Listener plus Direkt-Replays aus Speichervorgängen) laufen durch **einen** Orchestrator (`src/static/js/sync-orchestrator.js`):

- **Exklusiver Web Lock** `"anlaufstelle-offline-mutex"` (`navigator.locks`) umschließt jede Sync-Sequenz. Über Tabs hinweg läuft damit zu jeder Zeit höchstens eine Sequenz; parallele Trigger werden serialisiert und finden nichts mehr zu tun (billige No-ops) statt zu duplizieren.
- **Feste Sequenz-Reihenfolge:** `replayQueue` → `replayAllModifiedEvents` → (nur bei `hasSessionKey()`) `purgeExpired` + `revalidateCachedClients` — Writes vor Revalidierung.
- **Pro-Tab-Koaleszenz:** ein wartender Request + `rerun`-Flag; ein Tab reiht Trigger nicht unbegrenzt an, verliert aber auch keinen (nach Abschluss folgt genau ein weiterer Lauf, wenn währenddessen ein Trigger kam).
- **BroadcastChannel** `"anlaufstelle-offline"`: `{type:"sync-finished"}` refresht Sync-Status-/Konflikt-Badges in anderen Tabs; `{type:"key-cleared"}` weist andere Tabs an, ihren In-Memory-Key nach einem Wipe/Lock zu verwerfen.
- **Fallbacks:** ohne `navigator.locks` degradiert der Wrapper zur direkten Ausführung (Alt-Verhalten); ohne `BroadcastChannel` entfällt nur die Cross-Tab-Benachrichtigung.

**Bewusste Ausnahme (deadlock-notwendig):** Web Locks sind nicht reentrant. Der einzige Pfad, der aus einer gehaltenen Sequenz heraus erneut ins Lock liefe, ist die Entschlüsselung (`decryptPayload` → `_loadKey` → Idle-Gate). Dieser Boot-/Decrypt-Gate läuft daher **ungesperrt** (`_enforceIdleWipeLocked`). Er ist datenverlustfrei: bei vorhandener ungesyncter Arbeit wird nur der Key verworfen (`clearSessionKey`, Chiffrat bleibt, Replay nach Re-Auth), ein destruktiver `purgeAll` passiert ausschließlich, wenn nichts Ungesyncedtes existiert. Der **realistische** Idle-Wipe (Intervall/`visibilitychange`) läuft dagegen gesperrt — das ist der eigentliche Fix des Idle-Wipe-Race aus #1324.

### 2. HTTP-Replay-Contract (SSOT für Client, Server, Service Worker)

Die vier Mutations-Views antworten Replay-/JSON-Clients einheitlich:

| Fall | Status | Body | Bedingung |
|---|---|---|---|
| Versionskonflikt | 409 | `{error:"conflict", server_state:{…}, client_expected}` | `_wants_json_response` (Accept JSON **oder** HX-Request) |
| Fehlender/leerer Token (JSON-Edit) | 409 | wie Konflikt, `error:"missing-token"`, `client_expected:null` | nur `_wants_json_response`; HTML-Pfad bleibt No-Op |
| Ungültiges Formular | 422 | `{error:"invalid", errors:<form.errors.get_json_data()>}` | **nur roher** `Accept: application/json` (nicht HX); gilt Update **und** Create |
| Ratelimit | 429 | leer | immer |
| Erfolg | 302→200 | HTML | unverändert |

`server_state` trägt die ressourcentypischen Felder (Event: `data_json`/`updated_at`/`document_type_name`; WorkItem: `title`/`description`/`status`/`updated_at`). Der Optimistic-Lock-Token (`expected_updated_at`) ist im JSON-Pfad **Pflicht** und wird unter `select_for_update` in derselben Transaktion geprüft (#1338) — ein fehlender Token ist kein stiller Last-Write-Wins mehr, sondern ein 409. Idempotenz für Offline-Create (Event **und** WorkItem) über `X-Idempotency-Key` mit serverseitiger Dedup-TTL **72 h ≥ Bundle-Lease 48 h** (#1329).

Der Client klassifiziert die Antwort deterministisch: Erfolg nur bei Redirect (≠ `/login/`) bzw. HTMX-Partial; 409 → sichtbarer Konflikt (aus dem Auto-Replay genommen); 422/400/404/410 → Dead-Letter; 403 → ein CSRF-Retry, dann Dead-Letter; 429/5xx/Netzfehler → Backoff bzw. Batch-Abbruch. **Kein Head-of-Line-Blocking** (ein toter Record blockiert die übrigen nicht).

### 3. Dead-Letter-Semantik: ungesyncte Arbeit stirbt nie still

Die Kern-Invariante aus S1 wird festgeschrieben und um den `dead`-Zustand erweitert: Ein Record mit ungesyncter Arbeit (`modified`/`new`/`conflict`/`dead` bzw. eine Queue-Row) wird **nur** gelöscht durch (1) explizite Nutzeraktion (Verwerfen), (2) Security-Purge (Revalidate 404/410 mit force; permanenter Decrypt-Fehler nach Salt-Rotation), (3) Logout/`purgeAll`. `dead` ist **kein Löschen** — dauerhaft nicht zustellbare Arbeit bleibt erhalten und wird über die Sync-Status-/Dead-Letter-UI (#1385) sichtbar mit Retry/Verwerfen/Export (ENT-OFFL-16: lokale Notiz exportierbar). `dead` zählt in `countUnsyncedEvents` mit, damit der Idle-Wipe einen Dead-only-Bestand lockt statt purgt.

### 4. Service-Worker-Rolle

Der Service Worker koordiniert **kein** Replay (der tote Background-Sync-/`REPLAY_QUEUE`-Pfad ist entfernt; Koordination liegt allein beim Orchestrator/Web-Lock). Er reicht Mutations-Requests durch bzw. queued sie bei Netzfehler/Timeout, mit Fetch-Timeouts gegen Lie-Fi und client-genauem ACK-Routing (#1386). Neue SW-Versionen übernehmen erst nach Nutzer-Bestätigung (Update-Gate).

Zwei Grenz-Invarianten sichern die SW→Queue-Naht (sonst bricht der Idempotenz- bzw. Klassifikations-Contract genau hier, unbemerkt von den Einzelschicht-Tests): (a) **Idempotenz-Key-Teilung** — der SW vergibt beim Erstversuch genau **einen** `X-Idempotency-Key` (übernimmt einen bereits client-gesetzten, sonst frisch), setzt ihn identisch auf den Netz-Erstversuch **und** in die persistierte Queue-Row und führt `x-idempotency-key` in seiner Header-Allowlist; so dedupliziert der Server einen Slow-Commit (SW-Timeout < Gunicorn-Timeout) gegen den späteren Replay statt zwei Objekte anzulegen. (b) **Replay-Marker** — alle client-getriebenen Replays (generische Queue und Offline-Edit) tragen `X-Offline-Replay: 1`; der SW reicht markierte Requests network-only durch und re-queued sie **nicht**, damit ein langsamer Replay nicht als vermeintlicher Netzfehler erneut abgefangen wird (Doppelkanal/Spurious-Dead-Letter).

## Consequences

- **+** Über Tabs hinweg genau ein Sync-Lauf → keine Doppel-Anlage, kein Idle-Wipe-Race für die realistischen Trigger; der frühere unkoordinierte Multi-Tab-Zustand ist beseitigt.
- **+** Ein einziger, an drei Schichten getesteter Wire-Contract: Konflikte und Validierungsfehler sind für den Client eindeutig unterscheidbar statt als 200 verschluckt; Token-Pflicht + `select_for_update` schließen den Lost-Update-Pfad.
- **+** Ungesyncte Arbeit ist gegen stillen Verlust geschützt und im Fehlerfall für die Fachkraft sichtbar/exportierbar statt unsichtbar zu verhungern.
- **−** Der Boot-/Decrypt-Idle-Gate bleibt bewusst ungesperrt (Deadlock-Vermeidung). Rest-Effekt: ein bootender Tab kann via `key-cleared` den laufenden Sync eines anderen Tabs abbrechen — **transient, kein Datenverlust** (Records bleiben, Replay nach Re-Auth). Eine optionale Härtung (Broadcast nur aus gesperrten Top-Level-Triggern; `hasUnsyncedData`-Re-Check unmittelbar vor `purgeAll`) ist als Folgearbeit vermerkt.
- **−** Der Contract ist an drei Stellen (Client-Queue, Server-Views, SW-Header-Allowlist) konsistent zu halten; ein Abweichen einer Schicht (z. B. Strippen von `Accept: application/json` im SW) bricht die Klassifikation. Absicherung: cross-schicht Integrations-E2E.
- **−** `navigator.locks`/`BroadcastChannel` heben den Browser-Floor leicht an; für die Zielbrowser 2026 unkritisch, Fallbacks sind vorhanden.

## Alternatives considered

- **Leader-Election per BroadcastChannel statt Web Lock.** Verworfen: aufwendiger und fehleranfälliger (Leader-Tod, Wahl-Races) als der vom Browser garantierte exklusive Lock; BroadcastChannel bleibt für Zustands-Benachrichtigung, nicht für gegenseitigen Ausschluss.
- **Background Sync API für Replay.** Verworfen (YAGNI, #1351): uneinheitliche Browser-Unterstützung, und der tatsächliche Trigger (`online` + Orchestrator) deckt den Bedarf; der tote Handler wurde entfernt statt reaktiviert.
- **Serverseitige Idempotenz per DB-Unique-Constraint statt Cache-Dedup.** Zurückgestellt: der Cache-Dedup mit 72-h-TTL schließt das #1329-Fenster; eine atomare DB-Constraint (die auch konkurrierende Replays desselben Keys über mehrere Worker deduplizierte) ist als Folge-Härtung vermerkt.
- **`dead`-Records nach Frist automatisch verwerfen.** Verworfen: widerspricht der Kern-Invariante; dauerhaft nicht zustellbare Arbeit gehört der Fachkraft angezeigt, nicht vom System entsorgt.

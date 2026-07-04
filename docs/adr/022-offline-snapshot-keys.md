# ADR-022: Offline-Snapshot und Offline-Keys

- **Status:** Accepted
- **Date:** 2026-05-28
- **Deciders:** Tobias Nix
- **Refs:** #572, #574, #786, #1100, #1109, #1110, #1111, #1355

> **Accepted (2026-06-15, #1100) — mit eingegrenztem Scope.** Die in dieser ADR geforderte Security-Review + konzeptioneller Pen-Test gegen das Tablet-Diebstahl-Szenario ist erfolgt ([Befund-Doc](../archive/audits/2026-06-14-offline-snapshot-security-review.md)); die drei als Blocker eingestuften Befunde sind behoben und unit- + E2E-verifiziert: Sync-Konflikt-Token (F-07, #1109), Client-TTL-Durchsetzung + Server-Revalidierung (F-04/F-10, #1110) und Offline-Cache-Entzug bei Rechteentzug/Deaktivierung (F-03, #1110); dazu Idempotenz-Schutz beim Queue-Replay (F-09).
>
> **Akzeptierter Scope:** Akzeptiert und erprobt ist das **Offline-Lesen** server-vorgefilterter Snapshot-Bundles (verschluesselter IndexedDB-Layer, Idle-Key-Wipe, TTL-Durchsetzung + Server-Revalidierung).
>
> **Re-Evaluation (2026-06-29, #1111):** Das **Offline-Editieren** (Stage A) ist jetzt **verdrahtet und erprobt** — nicht mehr deferred. Das Bundle traegt pro Event die editierbaren Feld-Definitionen (Optionen/Pflichtfeld/Hilfetext, **sensitivity-gefiltert wie die Werte**) plus ein `can_edit`-Flag; der Offline-Viewer ([`offline_detail.html`](../../src/templates/core/clients/offline_detail.html) / [`offline-client-view.js`](../../src/static/js/offline-client-view.js)) bietet den Edit-Einstieg, ruft `window.offlineEdit.markEventModified` und spiegelt `pending`/`synced`/`conflict`. Der Pfad `markEventModified` → Replay gegen `/events/<pk>/edit/` → `synced` bzw. 409-Konflikt ist unit- und E2E-verifiziert ([`test_offline_edit_conflict.py`](../../src/tests/e2e/test_offline_edit_conflict.py)). **Die Konflikt-Semantik bleibt unveraendert Last-Write-Wins mit Konflikt-Markierung** — bewusst kein Hard-Merge; die fachlichen Grenzen dieser Strategie sind unten unter [„Akzeptierte Restrisiken"](#akzeptierte-restrisiken) dokumentiert und gelten fort.
>
> **Update (2026-07-01, #1321/#1322):** Der Offline-Zugang ist von *einer* Fallback-Seite auf ein **In-Place-App-Shell-Modell** umgestellt. Einstieg ist der **Offline-Arbeitsplatz** ([`/offline/`](../../src/core/views/pwa.py), #1321), der die mitgenommenen Personen client-seitig aus der verschluesselten IndexedDB listet ([`offline-home.js`](../../src/static/js/offline-home.js)). Die Detailseite wird **in-place an der kanonischen URL** `/clients/<pk>/` gerendert: der Service Worker ([`sw.js`](../../src/static/js/sw.js)) liefert dafuer den pk-losen [`OfflineClientShellView`](../../src/core/views/offline.py) aus, statt auf `/offline/clients/<pk>/` zu **redirecten** (#1322) — der Online-/Offline-Split an der URL entfaellt. Der Krypto-/Snapshot-Kern dieser ADR (Bundle-Aufbau, Idle-Key-Wipe, TTL-Durchsetzung, Server-Revalidierung) bleibt davon **unberuehrt**.
>
> **Update (2026-07-04, #1398):** Das **Offline-Erfassen/-Bearbeiten von Fallaufgaben (WorkItems)** ist verdrahtet (Create + Edit inkl. Zuweisung; Replay gegen `/workitems/new/` bzw. `/workitems/<pk>/edit/` mit `expected_updated_at`-Konflikt-Token + Idempotenz-Key, analog Events). **Neuer Bundle-Bestandteil:** fuer Staff+ traegt das Bundle jetzt die **zuweisbaren Nutzer:innen** der Einrichtung (`assignable_users`, nur pk + Anzeigename) — eine bewusste, **begrenzte** Erweiterung des Offline-Datenumfangs (Mitarbeiter:innen-Namen, **keine** Klientel-PII; online ohnehin jeder authentifizierten Kraft im Zuweisungs-Dropdown sichtbar). Assistenz erhaelt keinen Roster und kann WorkItems nicht mutieren (`StaffRequired`). Der Krypto-/TTL-/Wipe-Kern bleibt unveraendert.

## Context

Anlaufstelle wird teilweise in Aussendienst-Settings genutzt — aufsuchende Beratung, Schul-/Jugendamt-Aussentermine, Sprechstunden ohne stabile Netz-Anbindung. Drei Anforderungen kollidieren:

- **Lesen ohne Konnektivitaet:** Fachkraefte brauchen wenigstens den letzten bekannten Stand zu „ihren" Klient:innen vor Ort. Ein reines Online-Modell wuerde die Aussendienst-Nutzung blockieren.
- **Sensible Daten duerfen nicht ungeschuetzt auf das Mobilgeraet.** Die App liegt im Browser des Mitarbeiter-Geraets — ein gestohlenes Tablet darf den Datenbestand nicht freigeben. Plain IndexedDB ist nicht ausreichend.
- **Server-seitige Sichtbarkeitsregeln muessen offline weiter gelten.** Field-Level-Sensitivity, `visible_to(user)`, Facility-Scoping — alles, was online den Zugriff begrenzt, darf nicht durch ein Offline-Bundle umgangen werden.

Eine native App wuerde diese Probleme anders loesen, kommt fuer die Zielgruppe (kleine Traeger, keine MDM-Infrastruktur) aber nicht in Frage.

## Decision

Anlaufstelle baut **server-seitig vorgefilterte Snapshot-Bundles**, die client-seitig in einer verschluesselten IndexedDB-Schicht abgelegt werden.

- **Snapshot-Build server-seitig** ([`src/core/services/system/offline.py`](../../src/core/services/system/offline.py)): pro Klient ein Bundle, das `visible_to(user)` und `user_can_see_field(user, …)` **vor** der Serialisierung anwendet. Datei-Inhalte werden nie ins Bundle gelegt — nur Marker „Datei vorhanden" + Anzahl. Bundle-Groesse ist auf wenige hundert kB pro Klient gedeckelt (`MAX_EVENTS_PER_BUNDLE=50`, `LOOKBACK_DAYS=90`).
- **Keine Offline-PDF-Generierung (Abgrenzung zu [ADR-010](010-sync-pdf-generation.md)).** Offline-Bundles enthalten nur vorgefilterte Stamm-/Verlaufsdaten; Datei-Inhalte nur als „vorhanden"-Marker — **kein** PDF-Renderer und **kein** Cache erzeugter PDFs im Offline-Layer. PDFs entstehen ausschliesslich serverseitig und synchron (ADR-010).
- **Schema-Version + TTL im Bundle:** `BUNDLE_SCHEMA_VERSION=1` und `BUNDLE_TTL_SECONDS=48h`. Schema-Mismatch nach App-Upgrade zwingt den Client zum Purge; TTL erzwingt einen Re-Sync auch ohne Schemawechsel.
- **Client-seitige Verschluesselung pro Geraet.** Das Bundle wird im Browser ueber einen `crypto_session`-Mechanismus verschluesselt, bevor es in IndexedDB landet. Der Login-abgeleitete AES-GCM-256-Schluessel ist ein **non-extractable** `CryptoKey` in einer eigenen IndexedDB (`anlaufstelle-crypto`, Tabelle `meta`, [`crypto.js`](../../src/static/js/crypto.js)) — **nicht** im SessionStorage; die rohen Schluessel-Bytes sind nicht exportierbar. Schluessel verlaesst das Geraet nicht; ein gestohlenes Tablet ohne aktive Session liefert nur Chiffretext. Static-JS-Schicht: [`offline-store.js`](../../src/static/js/offline-store.js), [`offline-client.js`](../../src/static/js/offline-client.js).
- **Sync-Semantik (Offline-Editing, Stage A):** Schreibzugriffe offline werden in einer lokalen Queue ([`offline-queue.js`](../../src/static/js/offline-queue.js)) gehalten und bei Reconnect Server-gegen-Server gespielt. Default-Konfliktstrategie ist **Last-Write-Wins mit Konflikt-Markierung**: der Server akzeptiert das Update, markiert aber Felder mit divergenter Server-Version fuer eine fachliche Pruefung ([`conflict-resolver.js`](../../src/static/js/conflict-resolver.js)). Hard-Merge ist explizit nicht versucht. **Status (2026-06-29, #1111):** Die serverseitige Konflikt-Erkennung ist fuer F-07 gehaertet, Replay- und Resolver-Schicht existieren — und der Edit-**Einstieg** im Offline-Viewer ist jetzt **verdrahtet**: das Bundle liefert pro Event die editierbaren Feld-Definitionen (sensitivity-gefiltert) + `can_edit`, der Viewer rendert ein Offline-Edit-Formular und ruft `markEventModified`; `pending`/`synced`/`conflict` werden gespiegelt. Unit- + E2E-verifiziert ([`test_offline_edit_conflict.py`](../../src/tests/e2e/test_offline_edit_conflict.py)).
- **Server-seitige Filter sind autoritativ.** Die Browser-Crypto ist Schutz gegen Geraete-Diebstahl, nicht gegen Rollen-Eskalation — alle Sichtbarkeits-Entscheidungen werden serverseitig getroffen, bevor das Bundle den Server verlaesst.

## Consequences

- **+** **Soll** Aussendienst-Nutzung ermoeglichen, ohne dass Plain-Text-Personendaten am Mobilgeraet liegen.
- **+** Server-seitige Sichtbarkeitsregeln greifen vor der Serialisierung — ein Bundle kann nicht mehr enthalten, als der User online sehen wuerde (im Code verifizierbar).
- **+** Bundle-Groesse bleibt durch harte Caps (Anzahl Events, Lookback-Fenster) berechenbar; kein „ganze DB ins Tablet"-Risiko.
- **+** TTL + Schema-Version verhindern lange lebende, veraltete Caches.
- **+** Last-Write-Wins + Konflikt-Markierung ist einfach zu implementieren und liefert eine fachlich nachvollziehbare Spur, wo Re-Konvergenz noetig ist.
- **−** Browser-Crypto haengt an einer Login-Session. Nach Re-Login muss das Bundle ggf. neu gebaut werden — UX-Reibung.
- **−** Last-Write-Wins kann fachlich „falsch" sein (juengerer Eintrag ueberschreibt qualitativ besseren aelteren). Die Konflikt-Markierung ist ein Pflaster, kein Heilmittel.
- **−** Anhaenge sind offline nicht lesbar — nur als „Datei vorhanden"-Marker sichtbar. Akzeptiert, weil die Alternative (Anhaenge offline cachen) das Bedrohungs-Modell deutlich verbreitert.
- **−** Wartungs-Tax: jede Aenderung an `visible_to` / `user_can_see_field` muss am Snapshot-Builder mitgezogen werden, sonst entsteht Online/Offline-Drift.
- **+** **Offline-Editieren (Stage A) ist seit #1111 verdrahtet und erprobt:** das Bundle traegt die editierbaren Feld-Definitionen (sensitivity-gefiltert) + ein `can_edit`-Flag, der Viewer-Edit-Einstieg ruft `markEventModified` → Replay; unit- + E2E-getestet (Reconnect→`synced`, Server-Konkurrenz→Konflikt). Die fachlichen LWW-Grenzen bleiben bestehen (s. o. + Restrisiken).
- **−** Last-Write-Wins bleibt auch nach der Verdrahtung die Konflikt-Strategie: ein offline entstandener Edit kann beim Replay einen fachlich besseren neueren Server-Stand nur per Konflikt-Markierung (nicht automatisch) zur Pruefung stellen. Die akzeptierten Restrisiken (F-01/F-02/F-05, Cold-Boot/RAM) sind unten gesondert dokumentiert.
- **+** **Cases sind rollen-/status-gegated wie online (Refs #1355):** Non-Staff sehen im Bundle nur `status=OPEN`-Faelle ohne `description` (leerer String statt Key-Omission, schema-stabil); Staff+ sieht wie online via CaseListView/CaseDetailView alle Faelle inkl. description. Vorher verliess ungefiltert jeder Fall inkl. description den Server — ein Bruch der Kernzusage oben, der mit diesem Fix behoben ist.

## Alternatives considered

- **Online-only — Aussendienst nicht unterstuetzt.** Verworfen: fachlich zu hart; aufsuchende Beratung ist im Zielprofil.
- **Native App (iOS/Android).** Verworfen fuer v1: Distribution + MDM-Aufwand passt nicht zu kleinen Traegern. Web-Stack mit Offline-Layer ist „gut genug" und vermeidet eine zweite Codebasis.
- **Cached-Web ohne client-seitige Crypto.** Verworfen: ein gestohlenes Tablet wuerde im Browser-DevTool sofort die IndexedDB-Inhalte freigeben.
- **CRDTs / operationale Transformation fuer Offline-Edits.** Verworfen fuer v1: Komplexitaet im Verhaeltnis zu seltenen echten Konflikten zu hoch. Last-Write-Wins + Markierung deckt die realen Faelle.
- **Schluessel-Vorhaltung in einem Geraete-Vault (WebAuthn / Hardware-Key).** Vertagt: erweitert Zielgruppen-Anforderungen (Hardware-Verfuegbarkeit, Recovery). Nach erstem Pen-Test ggf. nachruestbar.

## Akzeptierte Restrisiken

Bei der Annahme (2026-06-15) bewusst akzeptierte Grenzen aus der Security-Review (Abschn. 4.3 und 7 im [Befund-Doc](../archive/audits/2026-06-14-offline-snapshot-security-review.md)); vor einem breiten Rollout erneut zu bewerten:

- **Passwortstaerke als Single-Point (F-02):** Bei Geraetediebstahl liegen Salt und Chiffretext beide lokal vor; ein Offline-Brute-Force gegen PBKDF2 (600k Iterationen, SHA-256) ist bei schwachem Nutzerpasswort moeglich. Eine zweite Schluesselquelle (Geraete-Vault/WebAuthn) ist bewusst vertagt. Mitigation: organisatorische Passwortstaerke; Hardware-Key optional nachruestbar.
- **Kein Offline-spezifisches Auto-Lock (F-01):** Der Key-Wipe haengt an der Session-Lebenszeit (Default 30 Min, Facility-konfigurierbar). Ein entsperrtes, gestohlenes Geraet bleibt bis zu dieser Grenze lesbar; es gibt kein kuerzeres Offline-Lock und keinen Wipe bei `pagehide` (bewusst, „Streetwork").
- **Schema-Version-Purge nicht implementiert (F-05):** Das Bundle traegt eine `schemaVersion`, aber kein Lesepfad purged bei Mismatch. Solange `BUNDLE_SCHEMA_VERSION` ([`offline.py`](../../src/core/services/system/offline.py)) nicht erhoeht wird, ist das risikolos; vor dem naechsten nicht-abwaertskompatiblen Bundle-Layout-Wechsel ist der Purge nachzuruesten.
- **Offline-Editieren verdrahtet + erprobt (F-08 → #1111):** der Edit-Einstieg im Viewer ist gebaut und unit-/E2E-getestet (siehe Statusblock). Das **akzeptierte Restrisiko bleibt die Konflikt-Strategie**: Last-Write-Wins mit Konflikt-Markierung ist bewusst kein Hard-Merge — ein juengerer Eintrag kann einen fachlich besseren aelteren ueberschreiben, und die Markierung ist eine Pruef-Spur, kein automatischer Merge. Anhaenge bleiben offline nicht editierbar (nur „Datei vorhanden"-Marker; der Server haelt bestehende Anhaenge beim Replay).
- **Ausserhalb der App-Verantwortung:** Cold-Boot-/RAM-Imaging gegen den Browserprozess, fehlende OS-Festplattenverschluesselung, MDM, kompromittiertes Geraet (Malware/Keylogger), Backups/WAL. In dieser ADR ueber die Native-App-Alternative bereits eingeordnet.

## References

- [`src/core/services/system/offline.py`](../../src/core/services/system/offline.py) — Snapshot-Build (Schema-Version, TTL, Sichtbarkeits-Gates)
- [`src/core/views/offline.py`](../../src/core/views/offline.py)
- [`src/static/js/offline-store.js`](../../src/static/js/offline-store.js) — verschluesselter IndexedDB-Layer
- [`src/static/js/offline-queue.js`](../../src/static/js/offline-queue.js) — Offline-Write-Queue
- [`src/static/js/offline-edit.js`](../../src/static/js/offline-edit.js) — `markEventModified` + Replay (Stage A)
- [`src/static/js/offline-client-view.js`](../../src/static/js/offline-client-view.js) — Offline-Viewer inkl. Edit-Einstieg (#1111)
- [`src/templates/core/clients/offline_detail.html`](../../src/templates/core/clients/offline_detail.html) — Offline-Detail + Inline-Edit-Formular
- [`src/static/js/conflict-resolver.js`](../../src/static/js/conflict-resolver.js)
- [`src/tests/e2e/test_offline_edit_conflict.py`](../../src/tests/e2e/test_offline_edit_conflict.py) — E2E Offline-Edit-Replay + Konflikt (#1111)
- [ADR-006](006-fernet-field-encryption.md) — server-seitige Feldverschluesselung
- [ADR-014](014-encrypted-file-vault.md) — Datei-Vault (warum Datei-Inhalte nicht offline gehen)
- Issue #572, #574, #786

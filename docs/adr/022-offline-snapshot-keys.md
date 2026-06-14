# ADR-022: Offline-Snapshot und Offline-Keys

- **Status:** Proposed
- **Date:** 2026-05-28
- **Deciders:** Tobias Nix
- **Refs:** #572, #574, #786, #1100, #1109, #1110, #1111

> **Hinweis Status:** Das Feature ist als Code-Entwurf vorhanden, aber **nicht in Pilotbetrieb**. Vor einer breiten Freigabe sind eine Security-Review und ein Pen-Test gegen das Tablet-Diebstahl-Szenario erforderlich; die Annahmen zu Bedrohungs-Modell, Schluessel-Lebenszyklus und Sync-Semantik sind dann zu finalisieren. Diese ADR dokumentiert den aktuellen Stand und ist **vor einem breiten Production-Rollout zu re-evaluieren**.
>
> **Remediation-Stand 2026-06-14 (#1100):** Die Security-Review ([Befund-Doc](../archive/audits/2026-06-14-offline-snapshot-security-review.md)) ist erfolgt; die drei als `Accepted`-Blocker eingestuften Befunde sind behoben und unit- + E2E-verifiziert: Sync-Konflikt-Token (F-07, #1109), Client-TTL-Durchsetzung + Server-Revalidierung (F-04/F-10, #1110) und Offline-Cache-Entzug bei Rechteentzug/Deaktivierung (F-03, #1110); zusaetzlich Idempotenz-Schutz beim Queue-Replay (F-09). Offen vor einem `Accepted`: Verdrahtung + E2E-Erprobung des Offline-Edit-Pfads (F-08, #1111) sowie die explizite Dokumentation der akzeptierten Restrisiken (Passwortstaerke als Single-Point F-02, kein Offline-spezifisches Auto-Lock F-01, Schema-Version-Purge F-05). Die Statusentscheidung (`Accepted`) trifft der Maintainer.

## Context

Anlaufstelle wird teilweise in Aussendienst-Settings genutzt — aufsuchende Beratung, Schul-/Jugendamt-Aussentermine, Sprechstunden ohne stabile Netz-Anbindung. Drei Anforderungen kollidieren:

- **Lesen ohne Konnektivitaet:** Fachkraefte brauchen wenigstens den letzten bekannten Stand zu „ihren" Klient:innen vor Ort. Ein reines Online-Modell wuerde die Aussendienst-Nutzung blockieren.
- **Sensible Daten duerfen nicht ungeschuetzt auf das Mobilgeraet.** Die App liegt im Browser des Mitarbeiter-Geraets — ein gestohlenes Tablet darf den Datenbestand nicht freigeben. Plain IndexedDB ist nicht ausreichend.
- **Server-seitige Sichtbarkeitsregeln muessen offline weiter gelten.** Field-Level-Sensitivity, `visible_to(user)`, Facility-Scoping — alles, was online den Zugriff begrenzt, darf nicht durch ein Offline-Bundle umgangen werden.

Eine native App wuerde diese Probleme anders loesen, kommt fuer die Zielgruppe (kleine Traeger, keine MDM-Infrastruktur) aber nicht in Frage.

## Decision

Anlaufstelle baut **server-seitig vorgefilterte Snapshot-Bundles**, die client-seitig in einer verschluesselten IndexedDB-Schicht abgelegt werden.

- **Snapshot-Build server-seitig** ([`src/core/services/system/offline.py`](../../src/core/services/system/offline.py)): pro Klient ein Bundle, das `visible_to(user)` und `user_can_see_field(user, …)` **vor** der Serialisierung anwendet. Datei-Inhalte werden nie ins Bundle gelegt — nur Marker „Datei vorhanden" + Anzahl. Bundle-Groesse ist auf wenige hundert kB pro Klient gedeckelt (`MAX_EVENTS_PER_BUNDLE=50`, `LOOKBACK_DAYS=90`).
- **Schema-Version + TTL im Bundle:** `BUNDLE_SCHEMA_VERSION=1` und `BUNDLE_TTL_SECONDS=48h`. Schema-Mismatch nach App-Upgrade zwingt den Client zum Purge; TTL erzwingt einen Re-Sync auch ohne Schemawechsel.
- **Client-seitige Verschluesselung pro Geraet.** Das Bundle wird im Browser ueber einen `crypto_session`-Mechanismus (Login-abgeleiteter Schluessel im SessionStorage) verschluesselt, bevor es in IndexedDB landet. Schluessel verlaesst das Geraet nicht; ein gestohlenes Tablet ohne aktive Session liefert nur Chiffretext. Static-JS-Schicht: [`offline-store.js`](../../src/static/js/offline-store.js), [`offline-client.js`](../../src/static/js/offline-client.js).
- **Sync-Semantik (Offline-Editing, Stage A):** Schreibzugriffe offline werden in einer lokalen Queue ([`offline-queue.js`](../../src/static/js/offline-queue.js)) gehalten und bei Reconnect Server-gegen-Server gespielt. Default-Konfliktstrategie ist **Last-Write-Wins mit Konflikt-Markierung**: der Server akzeptiert das Update, markiert aber Felder mit divergenter Server-Version fuer eine fachliche Pruefung ([`conflict-resolver.js`](../../src/static/js/conflict-resolver.js)). Hard-Merge ist explizit nicht versucht.
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
- **−** Status `Proposed`: Annahmen zur Schluessel-Ableitung und zur Konflikt-Strategie sollten vor breitem Rollout in einem Bedrohungs-Modell-Review (mit Pen-Test gegen das Tablet-Diebstahl-Szenario) verfestigt werden.

## Alternatives considered

- **Online-only — Aussendienst nicht unterstuetzt.** Verworfen: fachlich zu hart; aufsuchende Beratung ist im Zielprofil.
- **Native App (iOS/Android).** Verworfen fuer v1: Distribution + MDM-Aufwand passt nicht zu kleinen Traegern. Web-Stack mit Offline-Layer ist „gut genug" und vermeidet eine zweite Codebasis.
- **Cached-Web ohne client-seitige Crypto.** Verworfen: ein gestohlenes Tablet wuerde im Browser-DevTool sofort die IndexedDB-Inhalte freigeben.
- **CRDTs / operationale Transformation fuer Offline-Edits.** Verworfen fuer v1: Komplexitaet im Verhaeltnis zu seltenen echten Konflikten zu hoch. Last-Write-Wins + Markierung deckt die realen Faelle.
- **Schluessel-Vorhaltung in einem Geraete-Vault (WebAuthn / Hardware-Key).** Vertagt: erweitert Zielgruppen-Anforderungen (Hardware-Verfuegbarkeit, Recovery). Nach erstem Pen-Test ggf. nachruestbar.

## References

- [`src/core/services/system/offline.py`](../../src/core/services/system/offline.py) — Snapshot-Build (Schema-Version, TTL, Sichtbarkeits-Gates)
- [`src/core/views/offline.py`](../../src/core/views/offline.py)
- [`src/static/js/offline-store.js`](../../src/static/js/offline-store.js) — verschluesselter IndexedDB-Layer
- [`src/static/js/offline-queue.js`](../../src/static/js/offline-queue.js) — Offline-Write-Queue
- [`src/static/js/conflict-resolver.js`](../../src/static/js/conflict-resolver.js)
- [ADR-006](006-fernet-field-encryption.md) — server-seitige Feldverschluesselung
- [ADR-014](014-encrypted-file-vault.md) — Datei-Vault (warum Datei-Inhalte nicht offline gehen)
- Issue #572, #574, #786

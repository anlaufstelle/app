/*
 * offline-revocations.js — Teil 3/4 des offline-store-Verbunds (Refs #1838):
 * die personen-genaue Offline-Widerrufsliste (#1760) — der Server meldet
 * anonymisierte Personen, dieser Pfad tilgt ihren kompletten lokalen
 * Bestand (Bundle-Spiegel UND ungesyncte Arbeit), bevor irgendein Replay
 * die geloeschte PII zurueck auf den Server schriebe.
 * Architektur-Uebersicht + Dexie-Schema-Doku: offline-db.js. Laedt NACH
 * offline-bundles.js und VOR offline-store.js (Reihenfolge fix, siehe
 * base.html/offline_base.html).
 */
(function () {
    "use strict";

    // Refs #1838: private Helfer aus offline-db.js — verbrauchbarer
    // Einmal-Kanal (Details + Sicherheitsbegruendung dort). Fail-closed wie
    // in offline-store.js: ohne Kanal bleibt das Modul inert.
    const _internals = window.__offlineStoreInternals && window.__offlineStoreInternals.take();
    if (!_internals) {
        // eslint-disable-next-line no-console
        console.error("[offline-revocations] interner Kanal fehlt — Ladereihenfolge pruefen (Refs #1838).");
        return;
    }
    const { db, listDecrypted } = _internals;

    /* ─── #1760 — personen-genaue Widerrufsliste (Offline-Revocations) ────── */

    /*
     * Refs #1760 (#1476/R20, #1493): Der Server fuehrt eine Liste anonymisierter
     * Personen; dieser Pfad wendet sie auf den lokalen Bestand an.
     *
     * WARUM ein eigener Kanal neben `revalidateCachedClient`: dessen Purge
     * haengt an 401/404/410. Eine ANONYMISIERTE Person existiert aber weiter und
     * liefert 200 — und selbst dieses 200 ueberschreibt den lokalen Klartext
     * NICHT, solange zu ihr ungesyncte Aenderungen offen sind (`"skipped"`).
     * Der lokale 48h-Klartext ueberlebte damit die Loeschung. Die Revoke-Liste
     * ist die positive Gegenrichtung: sie sagt, was WEG MUSS, unabhaengig von
     * der Antwort des Bundle-Endpoints.
     *
     * FAIL-SAFE-ENTSCHEID zu ungesyncter Arbeit: Bundle-Spiegel UND ungesyncte
     * Eintraege (events mit localStatus modified/new/conflict/dead, queue-Rows,
     * Entwuerfe) der Person werden VERWORFEN — nicht als Dead-Letter erhalten.
     * Zwei Gruende:
     *   1. Ein Replay schriebe frische Klartext-PII ueber eine Person, deren
     *      Datensatz gerade nach Art. 17 getilgt wurde — die Loeschung liefe
     *      rueckwaerts. Auf dem Server wuerde er ohnehin auf einen
     *      anonymisierten Datensatz treffen.
     *   2. Ein Dead-Letter behielte den entschluesselbaren Klartext genau dort
     *      liegen, wo der Widerruf ihn entfernen soll.
     * Das ist derselbe Loeschgrund wie der Sicherheits-Purge bei 401/404/410
     * (F-10/#1110, `removeOfflineClient`s `force`-Vertrag) und nutzt bewusst
     * DIESELBE Primitive — kein zweiter Purge-Pfad.
     *
     * SCHLUESSEL-GATE: Der Bundle-Purge braucht keinen Session-Key (planer
     * Dexie-Delete). Queue-/Entwurfs-Rows muessen dagegen entschluesselt werden,
     * um den Personenbezug zu erkennen; ohne Key bleiben sie liegen (sie sind
     * dann ohnehin unlesbarer Chiffretext) und der naechste Lauf holt es nach —
     * die Liste ist ohne Cursor idempotent und deckt das volle Retention-
     * Fenster ab (2x Bundle-TTL).
     */
    function _revocationsUrl() {
        return window.URL_PATTERNS.ENDPOINTS.REVOCATIONS;
    }

    /*
     * True, wenn `haystack` (URL, Formular-Key oder serialisierter Payload) eine
     * der widerrufenen UUIDs nennt. Case-insensitiv: die Server-UUID ist
     * kanonisch klein geschrieben, ein handgeschriebener Formular-Key koennte
     * es nicht sein.
     */
    function _mentionsRevoked(haystack, needlesLower) {
        if (!haystack) return false;
        const text = String(haystack).toLowerCase();
        for (const pk of needlesLower) {
            if (text.indexOf(pk) !== -1) return true;
        }
        return false;
    }

    /*
     * Refs #1774: Die URL eines gequeuten `POST /events/<uuid>/edit/` (analog
     * `/workitems/<uuid>/edit/`) traegt die EREIGNIS-/AUFGABEN-pk, nicht die
     * Personen-UUID; der Body eines Edit-POSTs nennt die Person ebenfalls
     * nicht (nur CSRF, `expected_updated_at`, Feld-Slugs). Ueber
     * `_mentionsRevoked` allein blieb er darum nach einem Widerruf stehen und
     * der naechste Replay schrieb frische Klartext-PII zu einer nach Art. 17
     * getilgten Person.
     *
     * Die lokale `events`-Tabelle kennt die Zuordnung: `pk` und `clientPk`
     * liegen dort als PLAINTEXT-Indexfelder (kein Decrypt, kein Session-Key
     * noetig — der Purge greift damit auch im Idle-Lock). Diese Aufloesung
     * liefert die Record-pks aller lokal bekannten Ereignisse/Aufgaben der
     * widerrufenen Personen; sie werden dem Suchbegriff-Satz des Queue-/
     * Entwurfs-Purges hinzugefuegt.
     *
     * WORKITEM_STATUS-GRENZE (bewusst): Ein gequeuter
     * `POST /partials/workitems/<uuid>/status/` traegt nur die Aufgaben-pk in
     * der URL, keine Personen-UUID im Body. Diese Aufloesung erfasst ihn NUR,
     * wenn die Aufgabe bereits einen `events`-Eintrag hat (`data.kind ===
     * "workitem"`, siehe `_isWorkItemRow`) — also nur, wenn sie offline schon
     * einmal VOLL editiert wurde. Eine reine Bundle-Aufgabe, die nie offline
     * editiert wurde und deren Status offline umgeschaltet wird, hat KEINE
     * `events`-Zeile; ihr Status-POST bleibt nach einem Widerruf zunaechst in
     * der Queue stehen — die serverseitige zweite Linie (#1771) faengt genau
     * diesen Fall ab.
     *
     * REIHENFOLGE (kritisch): Der Aufruf MUSS vor dem Bundle-Purge stehen —
     * `removeOfflineClient(pk, {force: true})` loescht genau die events-Rows,
     * aus denen die Zuordnung stammt. Danach aufgeloest, waere die Tabelle
     * leer und die Luecke wieder offen.
     *
     * REST-GRENZE (bewusst): Ein Edit-POST, dessen Ereignis lokal NICHT (mehr)
     * bekannt ist UND dessen Payload die Personen-UUID nicht traegt, bleibt
     * stehen. Ohne jeden Personenbezug waere sein Verwurf ein Breitband-Purge
     * fremder Arbeit; die serverseitige zweite Linie (#1771) faengt ihn ab.
     */
    async function _localRecordPksOfRevoked(revokedLower) {
        const rows = await db.events.toArray();
        const out = [];
        for (const row of rows) {
            const owner = row && row.clientPk ? String(row.clientPk).toLowerCase() : "";
            if (owner && revokedLower.indexOf(owner) !== -1 && row.pk) {
                out.push(String(row.pk).toLowerCase());
            }
        }
        return out;
    }

    /*
     * `needlesLower` = widerrufene Personen-UUIDs PLUS die lokal aufgeloesten
     * Record-pks ihrer Ereignisse/Aufgaben (siehe `_localRecordPksOfRevoked`).
     * Beide Kanaele zaehlen fuer URL/Formular-Key UND Payload: eine Queue-Row
     * oder ein Entwurf, der eines von beiden nennt, ist Arbeit AN dieser Person.
     */
    async function _purgeRevokedQueueAndDrafts(needlesLower) {
        let purgedQueueEntries = 0;
        let purgedDrafts = 0;
        // `listDecrypted` ueberspringt Rows, die ohne Session-Key nicht lesbar
        // sind (TRANSIENT, Refs #1352) — sie bleiben unangetastet, statt blind
        // geloescht zu werden. Ein permanenter Decrypt-Fehler entwertet sie
        // ohnehin bereits (Auto-Discard/Tombstone).
        const queueRows = await listDecrypted("queue");
        for (const row of queueRows) {
            const payload = JSON.stringify(row.data || {});
            if (_mentionsRevoked(row.url, needlesLower) || _mentionsRevoked(payload, needlesLower)) {
                await db.queue.delete(row.id);
                purgedQueueEntries += 1;
            }
        }
        const draftRows = await listDecrypted("drafts");
        for (const row of draftRows) {
            const payload = JSON.stringify(row.data || {});
            if (_mentionsRevoked(row.formKey, needlesLower) || _mentionsRevoked(payload, needlesLower)) {
                await db.drafts.delete(row.formKey);
                purgedDrafts += 1;
            }
        }
        return { purgedQueueEntries: purgedQueueEntries, purgedDrafts: purgedDrafts };
    }

    /*
     * Holt die Widerrufsliste und wendet sie an. Rueckgabe:
     *   {status: "ok"|"error"|"ratelimited", purgedClients, purgedQueueEntries,
     *    purgedDrafts}
     * `error`/`ratelimited` lassen den Bestand UNANGETASTET (fail-open beim
     * Transport — ohne verlaessliche Liste darf nichts geloescht werden; der
     * naechste Sync/Boot wiederholt den Abruf, und die 48h-TTL bleibt fuer ein
     * dauerhaft offline gebliebenes Geraet die letzte Verteidigungslinie).
     */
    async function applyClientRevocations() {
        const empty = { purgedClients: 0, purgedQueueEntries: 0, purgedDrafts: 0 };
        let response;
        try {
            response = await fetch(_revocationsUrl(), {
                method: "GET",
                credentials: "same-origin",
                headers: { Accept: "application/json" },
            });
        } catch (_e) {
            return { status: "error", ...empty };
        }
        if (response.status === 429) {
            return { status: "ratelimited", ...empty };
        }
        if (!response.ok) {
            return { status: "error", ...empty };
        }
        let payload;
        try {
            payload = await response.json();
        } catch (_e) {
            // Login-Redirect (der `fetch` folgt ihm) liefert eine HTML-Seite —
            // kein Widerrufssignal, also nichts loeschen.
            return { status: "error", ...empty };
        }
        const pks = (payload && payload.revocations) || [];
        if (!Array.isArray(pks) || pks.length === 0) {
            return { status: "ok", ...empty };
        }
        const revokedLower = pks.map((p) => String(p).toLowerCase());

        // Refs #1774: Ereignis->Person ZUERST aufloesen — der Bundle-Purge
        // unten loescht genau die events-Rows, die diese Zuordnung tragen.
        const recordPksLower = await _localRecordPksOfRevoked(revokedLower);

        let purgedClients = 0;
        // Refs #2382: die pks der lokal tatsaechlich getroffenen Personen —
        // einzige Grundlage fuer den Abgleich der Offline-Personenansicht
        // (siehe _fireRevocationEvent unten), NICHT die volle Server-Liste.
        const purgedClientPks = [];
        for (const pk of pks) {
            const key = String(pk);
            const known = await db.clients.where("pk").equals(key).count();
            // `force: true`: ungesyncte Arbeit dieser Person faellt bewusst mit
            // (siehe Fail-Safe-Entscheid im Abschnitts-Kommentar oben).
            // Refs #1838: removeOfflineClient lebt in offline-bundles.js —
            // Aufloesung zur Callzeit ueber die Fassade (wie purgeExpiredBundles
            // in offline-db.js).
            await window.offlineStore.removeOfflineClient(key, { force: true });
            if (known) {
                purgedClients += 1;
                purgedClientPks.push(key.toLowerCase());
            }
        }

        const extra = await _purgeRevokedQueueAndDrafts(revokedLower.concat(recordPksLower));
        const result = { status: "ok", purgedClients: purgedClients, ...extra };
        _fireRevocationEvent(result, purgedClientPks);
        return result;
    }

    /*
     * Refs #2382: EINE Quelle fuer alle drei Aufrufer (sync-orchestrator.js
     * Sync-Sequenz + Boot-Check, offline-store.js Fallback-Listener) statt
     * eines eigenen Signals je Aufrufer. Der bisherige Zustand liess das
     * Ergebnis von applyClientRevocations verworfen liegen — die offene
     * Offline-Personenansicht einer gerade widerrufenen Person erfuhr nichts
     * davon und zeigte weiter das (inzwischen falsche) "wird synchronisiert".
     *
     * DATENSPARSAMKEIT: ``detail`` traegt ausschliesslich Zaehlwerte und die
     * widerrufenen UUIDs (keine Pseudonyme/Inhalte) — reine Identifikatoren,
     * die die Ansicht ohnehin schon aus der URL kennt.
     *
     * Feuert NUR, wenn tatsaechlich etwas geloescht wurde — eine leere Liste,
     * ein Transportfehler oder ein Rate-Limit (alle drei liefern ``empty``
     * unveraendert) lassen den Bestand unangetastet und damit auch keine
     * offene Ansicht betroffen; ebenso eine nicht-leere Liste, die lokal auf
     * nichts trifft (rein remote bekannte Personen).
     */
    function _fireRevocationEvent(result, purgedClientPks) {
        const changed = result.purgedClients > 0 || result.purgedQueueEntries > 0 || result.purgedDrafts > 0;
        if (!changed || typeof window === "undefined" || !window.dispatchEvent) return;
        window.dispatchEvent(
            new CustomEvent("offline-client-revocations-applied", {
                detail: {
                    purgedClients: result.purgedClients,
                    purgedQueueEntries: result.purgedQueueEntries,
                    purgedDrafts: result.purgedDrafts,
                    pks: purgedClientPks,
                },
            })
        );
    }

    // Refs #1838: Export dieser Datei auf die EINE Fassade (siehe offline-db.js).
    window.offlineStore = Object.assign(window.offlineStore || {}, {
        // Refs #1760 — personen-genaue Widerrufsliste (Sync + Boot-Check)
        applyClientRevocations: applyClientRevocations,
    });
})();

"""Ergänzende Architekturpins für die per Vitest ausgeführten JS-Verträge (#1912)."""

from pathlib import Path

from django.conf import settings


def _js(name: str) -> str:
    return (Path(settings.BASE_DIR) / "static" / "js" / name).read_text()


def _template(name: str) -> str:
    return (Path(settings.BASE_DIR) / "templates" / name).read_text()


class TestTallyWriteAllowlistContract:
    def test_tally_pattern_is_an_exact_path(self):
        source = _js("url-patterns.js")
        assert "TALLY_INCREMENT: /^\\/partials\\/tally\\/increment\\/$/" in source
        assert "self.URL_PATTERNS.TALLY_INCREMENT" in source
        assert "QUEUE_PATTERNS" in source

    def test_service_worker_checks_origin_before_write_patterns(self):
        source = _js("url-patterns.js")
        start = source.index("self.URL_PATTERNS.isQueueableWriteUrl")
        end = source.index("self.URL_PATTERNS.extractClientPk", start)
        function = source[start:end]
        origin_check = function.index("parsed.origin !== self.location.origin")
        pattern_check = function.index("QUEUE_PATTERNS.some")
        assert origin_check < pattern_check
        assert "pattern.test(parsed.pathname)" in function
        assert "return false" in function[:pattern_check]

    def test_negative_examples_are_named_next_to_exact_allowlist(self):
        source = _js("url-patterns.js")
        assert "/partials/tally/increment/extra/" in source
        assert "/partials/tally/increment-evil/" in source
        assert "https://evil.example/partials/tally/increment/" in source


class TestTallyIdempotencyTransportContract:
    def test_valid_urlencoded_body_key_precedes_random_key(self):
        source = _js("sw.js")
        body_helper = source[source.index("function tallyBodyIdempotencyKey") : source.index("function _withHeader")]
        assert "content-type" in body_helper
        assert "application/x-www-form-urlencoded" in body_helper
        assert 'params.get("idempotency_key")' in body_helper
        assert "IDEMPOTENCY_KEY_RE.test" in body_helper

        fetch_branch = source[source.index('self.addEventListener("fetch"') :]
        body_key = fetch_branch.index("bodyIdempotencyKey")
        random_key = fetch_branch.index("crypto.randomUUID()")
        first_attempt = fetch_branch.index('X-Idempotency-Key", idempotencyKey')
        assert body_key < random_key < first_attempt
        assert "clientIdemKey || bodyIdempotencyKey || crypto.randomUUID()" in fetch_branch
        assert 'headers["x-idempotency-key"] = idempotencyKey' in fetch_branch

    def test_cold_shell_sets_identical_key_in_header_and_body(self):
        source = _js("offline-tally.js")
        assert 'params.set("idempotency_key", idempotencyKey)' in source
        assert 'params.set("date", form.dataset.tallyDate' in source
        assert 'params.set("time_filter", form.dataset.tallyTimeFilter' in source
        assert '"X-Idempotency-Key": idempotencyKey' in source
        assert '"Content-Type": "application/x-www-form-urlencoded"' in source


class TestTallyEncryptedQueueAndSchemaContract:
    def test_tally_state_is_derived_from_existing_encrypted_queue(self):
        source = _js("offline-store.js")
        start = source.index("async function listTallyQueueState")
        end = source.index("async function retryQueueEntry", start)
        function = source[start:end]
        assert 'listDecrypted("queue")' in function
        assert 'putEncrypted("queue"' not in function
        assert "localStorage" not in function
        assert "URLSearchParams" in function
        assert 'params.get("document_type")' in function
        assert "formData" not in function

    def test_facility_schema_and_service_worker_cache_are_bumped(self):
        # Refs #1838: die Schema-Konstante lebt seit dem Verbund-Split in
        # offline-db.js (eine Quelle, ueber den internen Kanal geteilt).
        store = _js("offline-db.js")
        sw = _js("sw.js")
        assert "const FACILITY_BUNDLE_SCHEMA_VERSION = 2;" in store
        # Refs #1947: v39 — Markenfarben und 44-px-Tally-Ziele werden auch in
        # den precachten Offline-Shells erneuert.
        # Refs #1828: v40 — Quick-Win-Welle erneuert die konsolidierten
        # Offline-JS-Assets im Precache.
        # Refs #1838: v41 — der offline-store-Verbund (4 Dateien) muss atomar
        # precached werden.
        assert 'const CACHE_NAME = "anlaufstelle-v41";' in sw
        assert '"/static/js/offline-tally.js"' in sw

    def test_old_facility_bundles_remain_fail_closed(self):
        # Refs #1838: _isFacilitySchemaMismatch + Facility-Purge leben im
        # Bundles-Modul des Verbunds.
        store = _js("offline-bundles.js")
        assert "return schemaVersion !== FACILITY_BUNDLE_SCHEMA_VERSION" in store
        assert "await db.facility.delete(FACILITY_ROW_KEY)" in store

    def test_tally_payload_stays_inside_encrypted_facility_envelope(self):
        # Refs #1838: saveFacilityBundle lebt im Bundles-Modul des Verbunds.
        source = _js("offline-bundles.js")
        start = source.index("async function saveFacilityBundle")
        end = source.index("async function getOfflineFacility", start)
        function = source[start:end]
        assert "tally: bundle.tally" in function
        assert "crypto.encryptPayload" in function
        encrypt = function.index("crypto.encryptPayload")
        database_write = function.index("db.facility.put")
        assert encrypt < database_write


class TestTallyReconciliationContract:
    def test_templates_pass_full_tally_status_msgids_with_sentinels(self):
        online = _template("core/zeitstrom/index.html")
        cold = _template("core/zeitstrom/offline_zeitstrom.html")
        row = _template("core/zeitstrom/partials/tally_row.html")
        for source in (online, cold):
            assert "Serverstand: __count__" in source
            assert "ausstehend: __count__" in source
            assert "+1 wird gesendet" in source
            assert "+__count__ werden gesendet" in source
            assert "Stand: __timestamp__" in source
        assert "count counter=1" in row
        assert "count counter=2" in row

    def test_ui_never_optimistically_adds_pending_to_confirmed(self):
        source = _js("offline-tally.js")
        assert "confirmedLabel: replaceCount(labels.serverStatus, confirmed)" in source
        assert "pendingLabel: replaceCount(labels.pending, pending)" in source
        assert "pendingStatusLabel(pending, labels)" in source
        assert "confirmed + pending" not in source
        assert "confirmed +=" not in source

    def test_reconnect_waits_for_orchestrated_replay_and_revalidation(self):
        source = _js("offline-tally.js")
        assert 'addEventListener("offline-sync-finished"' in source
        assert 'window.addEventListener("online", requestFallbackReconcile)' in source
        assert 'window.syncOrchestrator.requestSync("tally-ui")' not in source
        assert "renderRevalidatedSnapshot(event.detail)" in source

    def test_warm_zeitstrom_loads_shared_tally_enhancer(self):
        source = _template("core/zeitstrom/index.html")
        assert "js/offline-tally.js" in source

    def test_deadletter_is_visible_without_rendering_submitted_values(self):
        source = _js("offline-tally.js")
        assert "blockedCount" in source
        assert 'const CONFLICT_URL = "/offline/conflicts/"' in source
        assert "conflictHref: CONFLICT_URL" in source
        assert "fieldValue" not in source
        assert "data_json" not in source

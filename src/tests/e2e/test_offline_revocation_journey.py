"""E2E: Anonymisierung entwertet ein bereits ausgeliefertes Offline-Bundle (Refs #1760).

Die Luecke (Refs #1476/R20, #1493): Nach der Anonymisierung behielt ein Geraet den
Klartext der Person bis zu 48 h im IndexedDB. Die bestehende Revalidierung greift
dafuer nicht — eine anonymisierte Person existiert weiter und liefert **200**, und
dieses 200 ueberschreibt den lokalen Stand bewusst NICHT, solange ungesyncte
Aenderungen offen sind (``"skipped"``-Zweig in ``revalidateCachedClient``).

Beide Wege, auf denen der Client das Widerrufs-Signal einholt, sind hier
verdrahtet:

1. **Sync** — ``syncOrchestrator.requestSync()`` fuehrt ``applyClientRevocations``
   als ersten Schritt der Sequenz aus.
2. **Boot-Check** — ein frischer Seiten-Aufruf prueft die Liste auch dann, wenn
   kein ``online``-Event feuert und nichts zu replayen ist.

Der scharfe Fall ist Test 1: die Person traegt einen **ungesyncten** Offline-Edit,
d.h. genau die Konstellation, in der die alte Revalidierung den Klartext stehen
liess. Zusaetzlich wird geprueft, dass der gequeute Schreibzugriff **nicht**
repliziert wird (er wuerde sonst getilgte PII zurueckschreiben) und dass die
zweite, nicht widerrufene Person unangetastet bleibt.

Datenseed/Anonymisierung laufen ueber ``manage.py shell`` gegen die Worker-DB
(``e2e_env``), die Interaktion ueber Playwright. Echtes Login (kein
storage_state), damit ``crypto_session`` einen Schluessel ableitet — sonst
scheitert ``offlineStore.saveClientBundle`` an ``EncryptionKeyMissing``.

WICHTIG: E2E seriell ausfuehren (RAM-Limit der Container) — nicht parallelisieren.
"""

from __future__ import annotations

import subprocess
import uuid
from contextlib import suppress

import pytest

from ._helpers import do_real_login as _do_real_login
from ._helpers import python_bin as _python

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Server-seitiges Seed/Inspektion ueber manage.py shell (Worker-DB via e2e_env)


def _shell(e2e_env, script):
    out = subprocess.run(
        [_python(), "src/manage.py", "shell", "--no-imports", "-c", script],
        capture_output=True,
        text=True,
        env=e2e_env,
        check=True,
    )
    return [ln for ln in out.stdout.strip().splitlines() if ln.strip()]


def _seed_client_with_event(e2e_env, label):
    """Frische Person + ein NORMAL-„Kontakt"-Event mit sprechendem Klartext.

    Gibt ``(client_pk, event_pk, pseudonym)`` zurueck.
    """
    suffix = uuid.uuid4().hex[:8]
    pseudonym = f"E2E-Revoke-{label}-{suffix}"
    script = (
        "from core.models import Client, DocumentType, Event, Facility;"
        " from core.models.user import User;"
        " from django.utils import timezone;"
        " f = Facility.objects.get(name='Hauptstelle');"
        " u = User.objects.get(username='miriam');"
        " dt = DocumentType.objects.get(facility=f, name='Kontakt');"
        " c = Client.objects.create(facility=f, contact_stage='identified',"
        f"  pseudonym='{pseudonym}', notes='Notiz-{suffix}', created_by=u);"
        " e = Event.objects.create(facility=f, client=c, document_type=dt,"
        f"  occurred_at=timezone.now(), data_json={{'notiz': 'Klartext-{suffix}', 'dauer': 10}},"
        "  created_by=u);"
        " print(c.pk); print(e.pk)"
    )
    lines = _shell(e2e_env, script)
    return lines[-2], lines[-1], pseudonym


def _anonymize(e2e_env, client_pk):
    """Serverseitige Anonymisierung — schreibt den Revoke-Eintrag (Refs #1760)."""
    _shell(
        e2e_env,
        "from core.models import Client;"
        " from core.services.client import anonymize_client;"
        f" anonymize_client(Client.objects.get(pk='{client_pk}'))",
    )


def _revocation_rows(e2e_env, client_pk):
    return _shell(
        e2e_env,
        "from core.models import OfflineClientRevocation;"
        f" print(OfflineClientRevocation.objects.filter(client_pk='{client_pk}').count())",
    )[-1]


def _server_event_notes(e2e_env, client_pk):
    return _shell(
        e2e_env,
        "from core.models import Event;"
        f" [print(e.data_json.get('notiz')) for e in Event.objects.filter(client_id='{client_pk}', is_deleted=False)]",
    )


# ---------------------------------------------------------------------------
# Browser-Helfer (Muster: test_offline_edit_conflict.py)


def _cache_bundle(page, client_pk):
    """Bundle holen + verschluesselt ablegen (wie der „Mitnehmen"-Button)."""
    return page.evaluate(
        """async (pk) => {
            const resp = await fetch(`/api/v1/offline/bundle/client/${pk}/`, {
                headers: {Accept: 'application/json'},
            });
            if (!resp.ok) return {ok: false, status: resp.status};
            const bundle = await resp.json();
            await window.offlineStore.saveClientBundle(bundle);
            return {ok: true, pseudonym: bundle.client.pseudonym};
        }""",
        client_pk,
    )


def _local_state(page, client_pk):
    """Was liegt lokal noch zu dieser Person? (Bundle + Roh-Zeilen + Queue)"""
    return page.evaluate(
        """async (pk) => {
            const bundle = await window.offlineStore.getOfflineClient(pk);
            return {
                hasBundle: !!bundle,
                pseudonym: bundle ? bundle.client.pseudonym : null,
                clientRows: await window.offlineStore.db.clients.where('pk').equals(pk).count(),
                caseRows: await window.offlineStore.db.cases.where('clientPk').equals(pk).count(),
                eventRows: await window.offlineStore.db.events.where('clientPk').equals(pk).count(),
                queueRows: await window.offlineStore.db.queue.count(),
            };
        }""",
        client_pk,
    )


def _queue_offline_write(page, client_pk):
    """Einen Offline-Schreibzugriff zu dieser Person in die generische Queue legen.

    ``enqueueRequest`` verlangt ``content-type`` UND den ``hx-request``-Header
    (Harness-Konvention der bestehenden Offline-E2E-Tests) — ohne sie wird der
    Eintrag beim Replay nicht als HTMX-POST erkannt.
    """
    return page.evaluate(
        """async (pk) => {
            await window.offlineQueue.enqueueRequest(
                '/events/new/',
                'POST',
                'client=' + pk + '&notiz=Offline-Klartext',
                {'content-type': 'application/x-www-form-urlencoded', 'hx-request': 'true'}
            );
            return await window.offlineStore.db.queue.count();
        }""",
        client_pk,
    )


def _mark_event_modified_offline(page, client_pk, event_pk):
    """Ungesyncten Offline-Edit erzeugen — genau der Zustand, in dem die alte
    Revalidierung den lokalen Klartext stehen liess (``"skipped"``)."""
    return page.evaluate(
        """async (args) => {
            await window.offlineStore.saveOfflineEdit({
                pk: args.eventPk,
                clientPk: args.clientPk,
                localStatus: 'modified',
                data: {occurredAt: '2026-01-02T09:30', fields: {notiz: 'Offline geaendert'}},
            });
            return await window.offlineStore.countUnsyncedEvents();
        }""",
        {"clientPk": client_pk, "eventPk": event_pk},
    )


def _wait_until(page, predicate, timeout_ms=15000, interval_ms=250):
    """Python-seitiges Pollen auf eine Bedingung im Browser.

    ``page.wait_for_function`` mit einer **async** Callback ist in dieser Suite
    eine bekannte Falle: das Promise-Objekt selbst ist truthy, die Funktion
    „erfuellt" also sofort (False-Positive). Alles, was einen ``await`` braucht
    (IndexedDB-Zaehlungen), wird deshalb hier gepollt.
    """
    waited = 0
    while waited < timeout_ms:
        if predicate():
            return
        page.wait_for_timeout(interval_ms)
        waited += interval_ms
    raise AssertionError("Bedingung nicht innerhalb des Timeouts erfuellt")


def _raw_ciphertext_dump(page):
    """Alle Roh-Zeilen aller PII-Tabellen — fuer die „nichts bleibt liegen"-Probe."""
    return page.evaluate(
        """async () => {
            const out = {};
            for (const t of ['clients', 'cases', 'events', 'queue', 'drafts']) {
                out[t] = await window.offlineStore.db[t].toArray();
            }
            return JSON.stringify(out);
        }"""
    )


# ---------------------------------------------------------------------------


class TestRevocationOnSync:
    """Der Sync-Pfad: Person offline mitgenommen → anonymisiert → naechster Sync purgt."""

    def test_anonymized_person_is_purged_from_indexeddb_on_next_sync(self, browser, base_url, e2e_env):
        revoked_pk, revoked_event_pk, revoked_pseudonym = _seed_client_with_event(e2e_env, "weg")
        kept_pk, _, kept_pseudonym = _seed_client_with_event(e2e_env, "bleibt")

        context = browser.new_context(locale="de-DE")
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            _do_real_login(page, base_url)

            # 1. Beide Personen offline mitnehmen — Klartext liegt jetzt lokal.
            assert _cache_bundle(page, revoked_pk)["ok"]
            assert _cache_bundle(page, kept_pk)["ok"]
            before = _local_state(page, revoked_pk)
            assert before["hasBundle"] is True
            assert before["pseudonym"] == revoked_pseudonym

            # 2. Ungesyncte Arbeit zu der Person: ein Edit + ein Queue-Eintrag.
            #    Genau diese Konstellation liess die alte Revalidierung den
            #    lokalen Klartext behalten.
            assert _mark_event_modified_offline(page, revoked_pk, revoked_event_pk) >= 1
            assert _queue_offline_write(page, revoked_pk) >= 1

            # 3. Serverseitig anonymisieren -> Revoke-Eintrag entsteht.
            _anonymize(e2e_env, revoked_pk)
            assert _revocation_rows(e2e_env, revoked_pk) == "1"

            # 4. Naechster Sync (Orchestrator-Sequenz, Widerruf laeuft als
            #    ERSTER Schritt — vor jedem Replay).
            page.evaluate("async () => { await window.syncOrchestrator.requestSync('e2e'); }")

            # 5. Nichts von der Person bleibt lokal — auch nicht die ungesyncte
            #    Arbeit (fail-safe: ein Replay wuerde getilgte PII zurueckschreiben).
            after = _local_state(page, revoked_pk)
            assert after["hasBundle"] is False
            assert after["clientRows"] == 0
            assert after["caseRows"] == 0
            assert after["eventRows"] == 0
            assert after["queueRows"] == 0

            # 6. Kein Klartext-Rest in irgendeiner Roh-Zeile.
            dump = _raw_ciphertext_dump(page)
            assert revoked_pseudonym not in dump
            assert revoked_pk not in dump

            # 7. Die nicht widerrufene Person bleibt vollstaendig erhalten.
            kept = _local_state(page, kept_pk)
            assert kept["hasBundle"] is True
            assert kept["pseudonym"] == kept_pseudonym

            # 8. Der gequeute Schreibzugriff wurde NICHT repliziert — sonst
            #    stuende bei der anonymisierten Person wieder frischer Klartext.
            assert "Offline-Klartext" not in _server_event_notes(e2e_env, revoked_pk)
        finally:
            with suppress(Exception):
                context.close()


class TestRevocationOnBoot:
    """Der Boot-Check: ohne ``online``-Event und ohne replaybare Arbeit."""

    def test_fresh_page_load_purges_anonymized_person(self, browser, base_url, e2e_env):
        revoked_pk, _, revoked_pseudonym = _seed_client_with_event(e2e_env, "boot")

        context = browser.new_context(locale="de-DE")
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            _do_real_login(page, base_url)
            assert _cache_bundle(page, revoked_pk)["ok"]
            assert _local_state(page, revoked_pk)["hasBundle"] is True

            _anonymize(e2e_env, revoked_pk)

            # Frischer Seiten-Aufruf: kein ``online``-Event, keine replaybare
            # Arbeit — nur der Boot-Check aus sync-orchestrator.js.
            page.goto(f"{base_url}/", wait_until="domcontentloaded")
            page.wait_for_function("() => window.offlineStore && window.crypto_session")
            page.evaluate("async () => { await window.crypto_session.ready(); }")
            # Python-seitig pollen: ``wait_for_function`` mit einer ASYNC
            # Callback liefert ein sofortiges truthy Promise und waere ein
            # False-Positive (Harness-Falle der Offline-E2E-Suite).
            _wait_until(page, lambda: _local_state(page, revoked_pk)["clientRows"] == 0)

            after = _local_state(page, revoked_pk)
            assert after["hasBundle"] is False
            assert revoked_pseudonym not in _raw_ciphertext_dump(page)
        finally:
            with suppress(Exception):
                context.close()

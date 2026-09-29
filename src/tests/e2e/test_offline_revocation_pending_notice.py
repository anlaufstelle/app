"""E2E: Offline-Personenansicht meldet den Widerruf ehrlich (Refs #2382).

Herkunft: Handtest-Abschluss Meilenstein handtest (#38), Prüfpunkt A-OFF-001.
Eine offline gequeute Änderung an einem Ereignis einer Person, die inzwischen
anonymisiert wurde, wird beim nächsten Abgleich lokal verworfen (#1760/#1774).
Die GEÖFFNETE Offline-Personenansicht zeigte danach weiter den gelben Hinweis
"Offline gespeichert – wird beim nächsten Online-Kontakt synchronisiert." und
das Badge "Nicht synchronisiert", obwohl die IndexedDB zu diesem Zeitpunkt
schon leer war — kein Toast, kein Statuswechsel sagte dem Nutzer, dass die
Änderung nicht übertragen wurde.

Diese Spec deckt genau die REPRO-Schrittfolge aus dem Ticket: anders als
``test_offline_revocation_journey.py`` (das nur den IndexedDB-Endzustand nach
einem Reload/Boot-Check prüft) bleibt hier dieselbe Ansicht die GANZE Zeit
offen — der Fix betrifft den window-CustomEvent-Listener in
``offline-client-view.js``, nicht den Purge-Mechanismus selbst (der ist bereits
in #1760 abgesichert).

Datenseed/Anonymisierung laufen über ``manage.py shell`` gegen die Worker-DB
(``e2e_env``), die Interaktion über Playwright. Echtes Login (kein
storage_state), damit ``crypto_session`` einen Schlüssel ableitet — sonst
scheitert ``offlineStore.saveClientBundle`` an ``EncryptionKeyMissing``.

WICHTIG: E2E seriell ausführen (RAM-Limit der Container) — nicht parallelisieren.
"""

from __future__ import annotations

import uuid
from contextlib import suppress

import pytest

from ._axe import AXE_TAGS, run_axe
from ._helpers import django_shell as _shell
from ._helpers import do_real_login as _do_real_login
from ._helpers import wait_until as _wait_until

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Server-seitiges Seed/Inspektion über manage.py shell (Worker-DB via e2e_env)


def _seed_client_with_event(e2e_env):
    """Frische Person + ein NORMAL-„Kontakt"-Event mit editierbarem Feld.

    Gibt ``(client_pk, event_pk, pseudonym)`` zurück.
    """
    suffix = uuid.uuid4().hex[:8]
    pseudonym = f"E2E-Discard-{suffix}"
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
        f"  occurred_at=timezone.now(), data_json={{'notiz': 'Original-{suffix}', 'dauer': 10}},"
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


# ---------------------------------------------------------------------------
# Browser-Helfer (Muster: test_offline_edit_conflict.py)


def _cache_bundle(page, client_pk):
    """Bundle holen + verschlüsselt in IndexedDB ablegen (wie der Mitnehmen-Button)."""
    return page.evaluate(
        """async (pk) => {
            const resp = await fetch(`/api/v1/offline/bundle/client/${pk}/`, {
                headers: {Accept: 'application/json'},
            });
            if (!resp.ok) return {ok: false, status: resp.status};
            const bundle = await resp.json();
            await window.offlineStore.saveClientBundle(bundle);
            return {ok: true};
        }""",
        client_pk,
    )


def _open_offline_detail(page, base_url, client_pk):
    page.goto(f"{base_url}/offline/clients/{client_pk}/", wait_until="domcontentloaded")
    page.locator("[data-testid='offline-event']").first.wait_for(state="visible", timeout=15000)


def _go_offline(page):
    page.context.set_offline(True)
    page.evaluate("window.dispatchEvent(new Event('offline'))")


def _go_online(page):
    page.context.set_offline(False)
    page.evaluate("window.dispatchEvent(new Event('online'))")


def _edit_notiz_offline(page, event_pk, value):
    """Edit-Affordanz öffnen, ``notiz`` ändern, speichern → landet in der Queue."""
    page.locator(f"[data-testid='offline-edit-btn-{event_pk}']").click()
    page.locator("[data-testid='offline-edit-form']").wait_for(state="visible", timeout=10000)
    page.locator("[data-testid='offline-edit-input-notiz']").fill(value)
    page.locator("[data-testid='offline-edit-save']").click()


def _client_rows(page, client_pk):
    return page.evaluate(
        "(pk) => window.offlineStore.db.clients.where('pk').equals(pk).count()",
        client_pk,
    )


# ---------------------------------------------------------------------------


class TestRevocationPendingNoticeReplacedWhileViewOpen:
    """Die Ansicht bleibt die GANZE Zeit offen — genau das Ticket-Repro."""

    def test_discards_pending_notice_and_rebuilds_after_reconnect(self, browser, base_url, e2e_env):
        client_pk, event_pk, pseudonym = _seed_client_with_event(e2e_env)

        # Refs Standardblock 104: Offline-Kontexte IMMER mit fester Zeitzone —
        # der Offline-Spiegel zeigt sonst die Browser-Zeitzone.
        context = browser.new_context(locale="de-DE", timezone_id="Europe/Berlin")
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            _do_real_login(page, base_url)

            # 1. Fachkraft nimmt die Person offline mit ("Mitnehmen").
            assert _cache_bundle(page, client_pk)["ok"]

            # 2. Offline-Ansicht öffnen (noch online — bleibt danach die ganze
            #    Zeit offen, genau wie im Ticket-Repro).
            _open_offline_detail(page, base_url, client_pk)

            # 3. Netz trennen, ein Ereignis bearbeiten, speichern.
            _go_offline(page)
            _edit_notiz_offline(page, event_pk, "Offline geändert")
            page.locator("[data-testid='offline-edit-pending']").wait_for(state="visible", timeout=10000)
            page.locator("[data-testid='event-unsynced-badge']").first.wait_for(state="visible", timeout=10000)

            # 4. Person online anonymisieren lassen (Löschantrag-Pfad simuliert
            #    über den Service — der Revoke-Eintrag entsteht serverseitig).
            _anonymize(e2e_env, client_pk)

            # 5. Wieder online gehen → Sync-Sequenz läuft, applyClientRevocations
            #    purgt den lokalen Bestand UND feuert jetzt das Ereignis, auf das
            #    die offene Ansicht hört.
            _go_online(page)

            # 6. Der ehrliche Hinweis ersetzt das falsche "wird synchronisiert".
            page.locator("[data-testid='offline-client-discarded']").wait_for(state="visible", timeout=15000)
            assert not page.locator("[data-testid='offline-edit-pending']").is_visible()
            assert not page.locator("[data-testid='event-unsynced-badge']").is_visible()
            # 7. Neuaufbau: die Ansicht zeigt den generischen Leerzustand.
            page.locator("[data-testid='offline-not-available']").wait_for(state="visible", timeout=10000)

            # axe-Smoke über die geänderte Ansicht (wieder online — die
            # Script-Injektion aus _axe.py braucht das Netz). Baseline für
            # diese Seite ist bereits leer (test_offline_detail_touch_targets.py),
            # jeder critical/serious-Fund wäre also neu.
            result = run_axe(page, base_url, tags=AXE_TAGS)
            blocking = result.blocking()
            assert not blocking, f"axe critical/serious auf {page.url}: {blocking}"

            # 8. Kein Klartext-Rest — Ticket-Anforderung "Datensparsamkeit".
            body_text = page.locator("body").inner_text()
            assert pseudonym not in body_text
            assert "Offline geändert" not in body_text

            # 9. IndexedDB ist tatsächlich leer (nicht nur die Anzeige).
            assert _wait_until(page, lambda: _client_rows(page, client_pk) == 0), (
                "Client-Bundle wurde nicht aus der IndexedDB entfernt"
            )
        finally:
            with suppress(Exception):
                page.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")
            with suppress(Exception):
                context.close()

"""E2E: Touch-Zielgrößen der Offline-Personenansicht (Refs #2383).

Handtest-Befund ``ZUSATZ-OFF-TOUCH`` (Meilenstein handtest #38, P3): in
``core/clients/offline_detail.html`` sind bei 390×844 und 375×812
(iPhone-UA, Touch) drei Textknöpfe zu niedrig für eine Trefferfläche —
„Bearbeiten“ je Ereignis (``offline-edit-btn-<pk>``, 62×16 px), „Aufgabe
erstellen“ (``offline-new-workitem-btn``, 133×20 px) und „Bearbeiten“ an
Aufgaben (``offline-wi-edit-btn-<pk>``, dieselbe Klasse
``text-xs text-accent hover:underline`` ohne Mindesthöhe). Projektkonvention
(WCAG 2.5.5, ``min-h-[44px] md:min-h-0`` u. a. in ``clients/list.html``,
``clients/detail.html``, ``src/tests/e2e/test_layout.py:128``): Touch-Ziele
mobil ≥ 44 px Höhe, Desktop-Layout unverändert.

Test-Technik: echtes Login (kein ``storage_state``, Muster
``test_offline_workitem_ui.py`` — ``crypto_session`` braucht den
In-Memory-Schlüssel für ``offlineStore.saveClientBundle``), Bundle-Cache
gegen die neu gesäte Person, Offline-Ansicht ONLINE öffnen (die Seite ist
ein per JS aus IndexedDB gefüllter Scaffold, kein SW-Precache-Ziel), axe-
Smoke dort ONLINE laufen lassen (``add_script_tag`` braucht das Netz),
danach ``context.set_offline(True)`` und die drei Ziele per
``getBoundingClientRect`` (``bounding_box()``) vermessen — deckungsgleich
mit dem Ticket-Repro „Netz trennen, Ansicht öffnen“, nur dass hier zuerst
online navigiert wird (Muster ``_open_offline_detail``/``_go_offline`` aus
``test_offline_workitem_ui.py``).

WICHTIG: E2E seriell ausführen (RAM-Limit der Container) — nicht
parallelisieren.
"""

from __future__ import annotations

import subprocess
import uuid
from contextlib import suppress

import pytest

from ._axe import AXE_TAGS, run_axe
from ._helpers import do_real_login as _do_real_login
from ._helpers import python_bin as _python

pytestmark = pytest.mark.e2e

_IPHONE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)

_MIN_HIT_AREA_PX = 44
_DESKTOP_WIDTH = 1280
_DESKTOP_HEIGHT = 900


# ---------------------------------------------------------------------------
# Server-seitiges Seed über manage.py shell (Muster test_offline_workitem_ui.py)


def _shell(e2e_env, script):
    out = subprocess.run(
        [_python(), "src/manage.py", "shell", "--no-imports", "-c", script],
        capture_output=True,
        text=True,
        env=e2e_env,
        check=True,
    )
    return [ln for ln in out.stdout.strip().splitlines() if ln.strip()]


def _seed_client_with_event_and_workitem(e2e_env):
    """Person mit einem Ereignis + einer offenen, unzugewiesenen Aufgabe —
    alle drei Touch-Ziele der Offline-Personenansicht brauchen echte
    Datensätze (Ereignis-Bearbeiten, Aufgabe-erstellen-Knopf, Aufgabe-
    Bearbeiten)."""
    marker = uuid.uuid4().hex[:8]
    script = (
        "from core.models import Client, DocumentType, Event, Facility, WorkItem;"
        " from core.models.user import User;"
        " from django.utils import timezone;"
        " f = Facility.objects.get(name='Hauptstelle');"
        " u = User.objects.get(username='miriam');"
        " dt = DocumentType.objects.get(facility=f, name='Kontakt');"
        f" c = Client.objects.create(facility=f, contact_stage='identified',"
        f"  pseudonym='E2E-Touch-{marker}', created_by=u);"
        " e = Event.objects.create(facility=f, client=c, document_type=dt,"
        "  occurred_at=timezone.now(), data_json={'notiz': 'E2E-Touch', 'dauer': 10}, created_by=u);"
        f" w = WorkItem.objects.create(facility=f, client=c, created_by=u,"
        f"  title='E2E-Touch-Aufgabe-{marker}', item_type='task', priority='normal', status='open');"
        " print(c.pk); print(e.pk); print(w.pk)"
    )
    lines = _shell(e2e_env, script)
    return lines[-3].strip(), lines[-2].strip(), lines[-1].strip()


# ---------------------------------------------------------------------------
# Browser-Helfer (Muster aus test_offline_workitem_ui.py)


def _cache_bundle(page, client_pk):
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
    page.locator("[data-testid='offline-client-view']").wait_for(state="visible", timeout=15000)


def _go_offline(page):
    page.context.set_offline(True)
    page.evaluate("window.dispatchEvent(new Event('offline'))")


class TestOfflineDetailTouchTargets:
    """Refs #2383: die drei Textknöpfe der Offline-Personenansicht ≥ 44 px mobil."""

    @pytest.fixture
    def mobile_context(self, browser):
        """Context mit iPhone-UA + Touch, Viewport erst je Test gesetzt (Muster
        ``test_client_detail_mobile_art18.py``). Kein ``storage_state`` — die
        Ziele leben unter ``/offline/``, das echte Login braucht den
        ``crypto_session``-Schlüssel."""
        context = browser.new_context(
            locale="de-DE",
            viewport={"width": 390, "height": 844},
            is_mobile=True,
            has_touch=True,
            device_scale_factor=2,
            user_agent=_IPHONE_UA,
        )
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            yield page
        finally:
            context.close()

    @pytest.mark.parametrize("width,height", [(390, 844), (375, 812)])
    def test_touch_targets_reach_44px_offline(self, mobile_context, base_url, e2e_env, width, height):
        page = mobile_context
        page.set_viewport_size({"width": width, "height": height})
        client_pk, event_pk, wi_pk = _seed_client_with_event_and_workitem(e2e_env)
        try:
            _do_real_login(page, base_url)
            cached = _cache_bundle(page, client_pk)
            assert cached["ok"], f"Bundle-Cache fehlgeschlagen: {cached!r}"
            _open_offline_detail(page, base_url, client_pk)

            edit_event = page.locator(f"[data-testid='offline-edit-btn-{event_pk}']")
            new_workitem = page.locator("[data-testid='offline-new-workitem-btn']")
            edit_workitem = page.locator(f"[data-testid='offline-wi-edit-btn-{wi_pk}']")
            edit_event.wait_for(state="visible", timeout=15000)
            new_workitem.wait_for(state="visible", timeout=15000)
            edit_workitem.wait_for(state="visible", timeout=15000)

            # axe-Smoke ONLINE (add_script_tag lädt axe.min.js übers Netz;
            # a11y_baseline.json ist für diese Seite leer — jeder
            # critical/serious-Fund wäre bereits ein Fehlschlag).
            result = run_axe(page, base_url, tags=AXE_TAGS)
            blocking = result.blocking()
            assert not blocking, f"axe critical/serious auf {page.url} ({width}x{height}): {blocking}"

            _go_offline(page)
            for locator, label in (
                (edit_event, "Bearbeiten (Ereignis)"),
                (new_workitem, "Aufgabe erstellen"),
                (edit_workitem, "Bearbeiten (Aufgabe)"),
            ):
                box = locator.bounding_box()
                assert box is not None, f"Ziel '{label}' nicht im DOM gefunden."
                assert box["height"] >= _MIN_HIT_AREA_PX, (
                    f"Trefferfläche '{label}' zu niedrig: {box['height']}px < {_MIN_HIT_AREA_PX}px "
                    f"bei {width}x{height} (WCAG 2.5.5-Zielgröße)."
                )
        finally:
            with suppress(Exception):
                page.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")

    def test_desktop_layout_unchanged(self, browser, base_url, e2e_env):
        """Desktop-Gegenprobe: ``md:min-h-0`` setzt die Mindesthöhe ab dem
        md-Breakpoint zurück — die Ziele bleiben so kompakt wie vor dem Fix."""
        client_pk, event_pk, wi_pk = _seed_client_with_event_and_workitem(e2e_env)
        context = browser.new_context(
            locale="de-DE",
            viewport={"width": _DESKTOP_WIDTH, "height": _DESKTOP_HEIGHT},
        )
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            _do_real_login(page, base_url)
            cached = _cache_bundle(page, client_pk)
            assert cached["ok"], f"Bundle-Cache fehlgeschlagen: {cached!r}"
            _open_offline_detail(page, base_url, client_pk)

            edit_event = page.locator(f"[data-testid='offline-edit-btn-{event_pk}']")
            new_workitem = page.locator("[data-testid='offline-new-workitem-btn']")
            edit_workitem = page.locator(f"[data-testid='offline-wi-edit-btn-{wi_pk}']")
            edit_event.wait_for(state="visible", timeout=15000)
            new_workitem.wait_for(state="visible", timeout=15000)
            edit_workitem.wait_for(state="visible", timeout=15000)

            _go_offline(page)
            for locator, label in (
                (edit_event, "Bearbeiten (Ereignis)"),
                (new_workitem, "Aufgabe erstellen"),
                (edit_workitem, "Bearbeiten (Aufgabe)"),
            ):
                box = locator.bounding_box()
                assert box is not None, f"Ziel '{label}' nicht im DOM gefunden."
                assert box["height"] < _MIN_HIT_AREA_PX, (
                    f"Ziel '{label}' bei {_DESKTOP_WIDTH}px faelschlich auf {box['height']}px "
                    f"angehoben — md:min-h-0 muss die 44px-Mindesthoehe am Desktop zuruecksetzen."
                )
        finally:
            with suppress(Exception):
                page.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")
            context.close()

"""E2E: Zwei-Faktor-Pflicht waehrend der Offline-Phase haelt den Replay an (Refs #2289).

Der Browser-Nachweis zum Akzeptanzpunkt des Tickets: ein echter Offline-Lauf,
bei dem die MFA-Pflicht **waehrend** der Offline-Phase serverseitig scharf
gestellt wird.

Ablauf:

1. Person mitnehmen (Klient-Bundle), offline die Akte in-place oeffnen.
2. Offline eine **Neuanlage mit Fall** erfassen UND ein vorhandenes Ereignis
   **aendern** — beide liegen danach unsynchronisiert in IndexedDB.
3. Serverseitig ``mfa_required`` setzen (die Middleware leitet Schreibanfragen
   ab jetzt VOR der View auf ``/mfa/setup/`` bzw. ``/mfa/verify/`` um).
4. Wieder online → Replay: die lokalen Daten muessen **unveraendert** liegen
   bleiben (Badges „nicht synchronisiert" bleiben stehen, serverseitig
   entsteht nichts), und der Hinweis „Anmeldung erforderlich" erscheint.
5. Zweiten Faktor bestaetigen → naechster Seitenstart drain die Warteschlange
   → serverseitig genau EIN neues Ereignis, am richtigen Fall, und die
   Aenderung ist angekommen.

Vor #2289 war Schritt 4 der stille Datenverlust: ``fetch`` folgt dem 302 bis
zur MFA-Seite, der Create-Replay wertete das als Erfolg und loeschte die nie
angelegte Neuanlage.

ZZ-Prefix der Klasse (Konvention aus ``test_mfa_backup_codes.py``): der Test
setzt einen ungewoehnlichen Serverzustand (MFA-Pflicht + TOTP-Device fuer
``markus``) und raeumt ihn danach wieder ab; andere Tests sollen ihn nicht
sehen. ``markus`` ist bewusst NICHT einer der Nutzer der geteilten
Storage-State-Fixtures.

WICHTIG: E2E seriell ausfuehren (RAM-Limit der Container) — nicht
parallelisieren.
"""

from __future__ import annotations

import re
import subprocess
import time
import uuid
from contextlib import suppress

import pytest

from ._axe import format_violations, run_axe
from ._helpers import do_real_login as _do_real_login
from ._helpers import python_bin as _python
from ._helpers import wait_for_active_service_worker as _wait_for_active_service_worker

pytestmark = pytest.mark.e2e

USER = "markus"

ANDROID_UA = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36"
)


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


def _seed_client_case_and_event(e2e_env):
    """Klient + offener Fall + ein bereits synchronisiertes Ereignis anlegen.

    Das Ereignis ist der Gegenstand der Offline-**Aenderung**; der Fall ist die
    Zuordnung der Offline-**Neuanlage**.
    """
    suffix = uuid.uuid4().hex[:8]
    script = (
        "from core.models import Client, Facility, Case, DocumentType, Event;"
        " from core.models.user import User;"
        " from django.utils import timezone;"
        " f = Facility.objects.get(name='Hauptstelle');"
        f" u = User.objects.get(username='{USER}');"
        " c = Client.objects.create(facility=f, contact_stage='identified',"
        f"  pseudonym='E2E-AUTH-{suffix}', created_by=u);"
        f" k = Case.objects.create(facility=f, client=c, title='E2E-Fall-{suffix}',"
        "  status=Case.Status.OPEN, created_by=u);"
        " dt = DocumentType.objects.get(facility=f, name='Kontakt', is_active=True);"
        " e = Event.objects.create(facility=f, client=c, document_type=dt,"
        "  occurred_at=timezone.now(), data_json={}, created_by=u);"
        " print('CLIENT:' + str(c.pk));"
        " print('CASE:' + str(k.pk));"
        " print('EVENT:' + str(e.pk));"
        " print('DOCTYPE:' + str(dt.pk))"
    )
    lines = _shell(e2e_env, script)
    values = {line.split(":", 1)[0]: line.split(":", 1)[1] for line in lines if ":" in line}
    return values["CLIENT"], values["CASE"], values["EVENT"], values["DOCTYPE"]


def _server_event_count_for_client(e2e_env, client_pk):
    return int(
        _shell(
            e2e_env,
            "from core.models import Event;"
            f" print(Event.objects.filter(client_id='{client_pk}', is_deleted=False).count())",
        )[-1]
    )


def _server_event_count_for_case(e2e_env, case_pk):
    return int(
        _shell(
            e2e_env,
            "from core.models import Event;"
            f" print(Event.objects.filter(case_id='{case_pk}', is_deleted=False).count())",
        )[-1]
    )


def _server_update_count(e2e_env, event_pk):
    return int(
        _shell(
            e2e_env,
            "from core.models import EventHistory;"
            f" print(EventHistory.objects.filter(event_id='{event_pk}',"
            " action=EventHistory.Action.UPDATE).count())",
        )[-1]
    )


def _require_mfa(e2e_env, required: bool):
    _shell(
        e2e_env,
        f"from core.models.user import User; User.objects.filter(username='{USER}').update(mfa_required={required})",
    )


def _confirmed_totp_code(e2e_env):
    """Bestaetigtes TOTP-Device anlegen und einen aktuell gueltigen Code liefern.

    Mit bestaetigtem Device verlangt die Middleware ``/mfa/verify/`` statt
    ``/mfa/setup/`` — genau der zweite Weg, den das Ticket nennt.
    """
    line = _shell(
        e2e_env,
        "from django_otp.plugins.otp_totp.models import TOTPDevice;"
        " from django_otp.oath import totp;"
        " from core.models.user import User;"
        f" u = User.objects.get(username='{USER}');"
        " TOTPDevice.objects.filter(user=u).delete();"
        " d = TOTPDevice.objects.create(user=u, name='default', confirmed=True);"
        " print('CODE:' + str(totp(d.bin_key, step=d.step, t0=d.t0, digits=d.digits)).zfill(d.digits))",
    )[-1]
    return line.split("CODE:", 1)[1]


def _cleanup_user(e2e_env):
    with suppress(Exception):
        _shell(
            e2e_env,
            "from django_otp.plugins.otp_totp.models import TOTPDevice;"
            " from core.models.user import User;"
            f" u = User.objects.get(username='{USER}');"
            " TOTPDevice.objects.filter(user=u).delete();"
            " User.objects.filter(pk=u.pk).update(mfa_required=False)",
        )


def _wait_until(fn, timeout_s=25.0, interval_s=0.5, desc=""):
    """Python-seitiges Polling (kein ``wait_for_function`` mit async-Praedikat —
    das kehrt beim ersten Poll zurueck, Suite-Konvention)."""
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        last = fn()
        if last:
            return last
        time.sleep(interval_s)
    raise AssertionError(f"Timeout ({timeout_s}s): {desc} — zuletzt: {last!r}")


# ---------------------------------------------------------------------------
# Browser-Helfer (dateilokal, Konvention dieser Suite)


def _android_context(browser, **extra):
    return browser.new_context(
        locale="de-DE",
        viewport={"width": 412, "height": 915},
        device_scale_factor=2.625,
        is_mobile=True,
        has_touch=True,
        user_agent=ANDROID_UA,
        **extra,
    )


def _cache_bundle(page, client_pk):
    return page.evaluate(
        """async (pk) => {
            const resp = await fetch(`/api/v1/offline/bundle/client/${pk}/`, {
                headers: {Accept: 'application/json'},
            });
            if (!resp.ok) return {ok: false, status: resp.status};
            const bundle = await resp.json();
            await window.offlineStore.saveClientBundle(bundle);
            return {ok: true, cases: (bundle.cases || []).length, events: (bundle.events || []).length};
        }""",
        client_pk,
    )


def _unsynced_count(page):
    return page.evaluate("async () => (window.offlineStore ? await window.offlineStore.countUnsyncedEvents() : -1)")


def _go_offline(page):
    page.context.set_offline(True)
    page.evaluate("window.dispatchEvent(new Event('offline'))")


def _go_online(page):
    page.context.set_offline(False)
    page.evaluate("window.dispatchEvent(new Event('online'))")


def _purge_offline(page):
    with suppress(Exception):
        page.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")


# ---------------------------------------------------------------------------


class TestZZOfflineAuthRedirectReplay:
    def test_mfa_requirement_during_offline_phase_pauses_the_replay(self, browser, base_url, e2e_env):
        client_pk, case_pk, event_pk, doctype_pk = _seed_client_case_and_event(e2e_env)
        context = _android_context(browser)
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            _do_real_login(page, base_url, username=USER)
            cached = _cache_bundle(page, client_pk)
            assert cached["ok"], f"Bundle-Cache fehlgeschlagen: {cached!r}"
            assert cached["cases"] >= 1, f"Das Bundle traegt keinen Fall: {cached!r}"
            assert cached["events"] >= 1, f"Das Bundle traegt kein Ereignis: {cached!r}"
            events_before = _server_event_count_for_client(e2e_env, client_pk)
            updates_before = _server_update_count(e2e_env, event_pk)

            page.goto(f"{base_url}/", wait_until="domcontentloaded")
            _wait_for_active_service_worker(page)

            # --- Offline arbeiten -------------------------------------------------
            _go_offline(page)
            page.goto(f"{base_url}/clients/{client_pk}/", wait_until="domcontentloaded")
            page.locator("[data-testid='offline-client-view']").wait_for(state="visible", timeout=15000)

            # (a) Neuanlage MIT Fall
            page.locator("[data-testid='offline-new-event-btn']").click()
            page.locator("[data-testid='offline-create-form']").wait_for(state="visible", timeout=10000)
            # The documentation type is mandatory (saveCreate rejects the form
            # otherwise); the catalogue comes from the cached bundle. Select the
            # seeded type by its value: choosing by position would pick up
            # whichever catalogue entry another spec left behind (a type with a
            # mandatory field blocks the submit via browser form validation).
            page.locator("[data-testid='offline-create-doctype']").select_option(value=doctype_pk)
            page.locator("[data-testid='offline-create-case']").select_option(value=case_pk)
            page.locator("[data-testid='offline-create-save']").click()
            create_error = page.locator("[data-testid='offline-create-error']")
            assert not create_error.is_visible() or not create_error.inner_text().strip(), (
                f"Offline-Erfassung meldet einen Fehler: {create_error.inner_text()!r}"
            )
            page.locator("[data-testid='event-unsynced-badge']").first.wait_for(state="visible", timeout=10000)

            # (b) Aenderung an dem bereits synchronisierten Ereignis
            page.locator(f"[data-testid='offline-edit-btn-{event_pk}']").click()
            page.locator("[data-testid='offline-edit-form']").wait_for(state="visible", timeout=10000)
            edit_input = page.locator("[data-testid^='offline-edit-input-']").first
            edit_input.wait_for(state="visible", timeout=10000)
            edit_input.fill("E2E-Offline-Korrektur")
            page.locator("[data-testid='offline-edit-save']").click()

            _wait_until(lambda: _unsynced_count(page) == 2, desc="zwei unsynchronisierte Eintraege lokal")
            assert _server_event_count_for_client(e2e_env, client_pk) == events_before, (
                "Vor dem Reconnect darf serverseitig nichts entstanden sein"
            )

            # --- MFA-Pflicht waehrend der Offline-Phase scharf stellen -------------
            _require_mfa(e2e_env, True)

            # --- Reconnect: Replay muss ANHALTEN ----------------------------------
            _go_online(page)
            # Synchronisationspunkt OHNE Annahme ueber den Ausgang: entweder der
            # Hinweis erscheint (gewuenscht) oder der Zaehler bewegt sich (der
            # Datenverlust vor #2289). So faellt der Test am Sachbefund, nicht am
            # fehlenden Hinweis-Element.
            _wait_until(
                lambda: page.locator("[data-testid='offline-auth-pending']").is_visible() or _unsynced_count(page) != 2,
                timeout_s=25.0,
                desc="Replay-Runde nach dem Reconnect abgeschlossen",
            )
            assert _unsynced_count(page) == 2, (
                "Die lokalen Eintraege muessen bei einer MFA-Weiterleitung unveraendert liegen bleiben"
            )
            page.locator("[data-testid='offline-auth-pending']").wait_for(state="visible", timeout=15000)
            hint = page.locator("[data-testid='offline-auth-pending']").inner_text()
            assert "Anmeldung erforderlich" in hint, f"Hinweistext unerwartet: {hint!r}"
            assert "2" in hint, f"Der Hinweis nennt die Zahl der lokalen Aenderungen nicht: {hint!r}"

            # a11y-Smoke der geaenderten Offline-Ansicht (der Hinweis ist genau
            # jetzt sichtbar; die Seite ist wieder online, axe-core laesst sich
            # also nachladen). Bewusst auf die NEUEN Knoten gescoped — der
            # Gesamtbefund der Offline-Shell gehoert nicht in diesen Test und
            # die Baseline wird nicht erweitert.
            axe = run_axe(page, base_url)
            hint_violations = [
                violation
                for violation in axe.blocking()
                if any("offline-auth-pending" in "".join(node["target"]) for node in violation["nodes"])
            ]
            assert not hint_violations, (
                'Der Hinweis „Anmeldung erforderlich" bringt neue a11y-Verstoesse mit:\n'
                + format_violations(hint_violations)
            )
            print(f"AXE-OFFLINE-AKTE blocking={axe.blocking_counts()} impacts={axe.counts_by_impact()}")
            assert _server_event_count_for_client(e2e_env, client_pk) == events_before, (
                "Eine MFA-Weiterleitung darf serverseitig nichts anlegen"
            )
            assert _server_update_count(e2e_env, event_pk) == updates_before, (
                "Eine MFA-Weiterleitung darf serverseitig nichts aendern"
            )
            # Gegenprobe, dass die Pause wirklich an der Weiterleitung haengt.
            assert page.locator("[data-testid='event-unsynced-badge']").first.is_visible(), (
                "Das noch nicht uebertragene Ereignis muss sichtbar unsynchronisiert bleiben"
            )

            # --- Zweiten Faktor bestaetigen ---------------------------------------
            code = _confirmed_totp_code(e2e_env)
            page.goto(f"{base_url}/mfa/verify/", wait_until="domcontentloaded")
            page.locator("[data-testid='mfa-token-input']").fill(code)
            page.locator("[data-testid='mfa-verify-button']").click()
            page.wait_for_url(re.compile(r"^(?!.*/mfa/).*$"), timeout=15000)

            # --- Replay laeuft durch (Startup-Drain) ------------------------------
            page.goto(f"{base_url}/clients/{client_pk}/", wait_until="domcontentloaded")
            _wait_until(
                lambda: _server_event_count_for_client(e2e_env, client_pk) == events_before + 1,
                timeout_s=40.0,
                desc="genau ein neues Ereignis nach abgeschlossener Anmeldung",
            )
            _wait_until(
                lambda: _server_update_count(e2e_env, event_pk) == updates_before + 1,
                timeout_s=40.0,
                desc="die Offline-Aenderung ist serverseitig angekommen",
            )
            # Settle-Fenster: ``_wait_until`` kehrt beim ERSTEN Treffer zurueck —
            # ein verspaetet ankommendes Duplikat (zweiter Drain-Lauf, Retry)
            # wuerde sonst nur zufaellig auffallen. Der Zaehler muss ueber
            # mehrere Runden stabil bleiben.
            for _ in range(4):
                time.sleep(1.0)
                assert _server_event_count_for_client(e2e_env, client_pk) == events_before + 1, (
                    "Nach dem Drain darf kein zweites Ereignis entstehen (Idempotenz)"
                )
                assert _server_update_count(e2e_env, event_pk) == updates_before + 1, (
                    "Die Aenderung darf nicht ein zweites Mal geschrieben werden"
                )
            assert _server_event_count_for_case(e2e_env, case_pk) == 1, (
                "Die Neuanlage muss genau einmal und am gewaehlten Fall ankommen"
            )
        finally:
            _purge_offline(page)
            _cleanup_user(e2e_env)
            context.close()

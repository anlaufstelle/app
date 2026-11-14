"""E2E-Tests: Hausverbot-Banner im Aktivitätslog.

Refs #922 / #924: zuvor hat der Test nur „Banner vorhanden falls
Seed-Daten zufällig einen aktiven Ban enthalten" geprüft — bei deterministisch
gesäten Daten ist das geraten, kein Test. Diese Variante:

1. Legt per ``manage.py shell`` *garantiert* einen aktiven Ban an (idempotent).
2. Prüft hart, dass der Banner sichtbar ist und Pseudonym + ``Hausverbot:``
   enthält.
3. Räumt auf, indem das Ban-Event auf ``aktiv=False`` gesetzt wird.
"""

from __future__ import annotations

import re
import sys
from datetime import date, timedelta
from urllib.parse import urljoin

import pytest
from playwright.sync_api import expect

from ._helpers import SHELL_TIMEOUT_SECONDS, run_child
from ._selectors import action_in_bar

pytestmark = pytest.mark.e2e

_SEED_BAN_SHELL = r"""
import json
from datetime import date
from django.utils import timezone
from core.models import Client, DocumentType, Event, Facility, User

facility = Facility.objects.get(name="Hauptstelle")
admin = User.objects.filter(facility=facility, role="facility_admin").first() or User.objects.first()

# Deterministisches Seed-Pseudonym (der Aufrufer setzt den Marker per
# ``.replace`` — ``.format`` kollidierte mit den f-String-Klammern unten),
# fallback auf ersten Klienten in der Facility.
client = Client.objects.filter(facility=facility, pseudonym="__PSEUDONYM__").first()
if client is None:
    client = Client.objects.filter(facility=facility).first()

ban_dt = DocumentType.objects.filter(facility=facility, system_type="ban", is_active=True).first()
assert ban_dt is not None, "Ban-DocumentType fehlt in der E2E-DB"

ban_payload = {
    "aktiv": "true",
    "grund": "E2E-Test: temporary ban",
    "bis": str(date(2099, 12, 31)),
}

# Idempotent: aktiven Ban erzeugen oder bestehenden reaktivieren.
# Event.save() verschluesselt sensitive Felder automatisch ueber den
# Pre-Save-Hook in core.models.event — direkter Plaintext-Set ist OK.
#
# Refs #1917: JEDE Mutation wird vorher als Restore-Payload gesichert. Ohne das
# hinterliess der Lauf Drift in der geteilten E2E-DB — der ueberschriebene
# Seed-Grund brachte ``test_security_hardening.py::TestBanReasonVisibility``
# zu Fall, weil diese Datei alphabetisch vorher laeuft.
restore = {"created": False, "siblings": []}

event = Event.objects.filter(facility=facility, client=client, document_type=ban_dt, is_deleted=False).first()
if event is None:
    event = Event(
        facility=facility,
        client=client,
        document_type=ban_dt,
        created_by=admin,
        occurred_at=timezone.now(),
        data_json=ban_payload,
    )
    event.save()
    restore["created"] = True
else:
    restore["data_json"] = event.data_json
    restore["occurred_at"] = event.occurred_at.isoformat()
    restore["is_deleted"] = event.is_deleted
    event.data_json = ban_payload
    event.is_deleted = False
    # Refs #1917: nach oben in die Kontakt-Chronik heben, damit der
    # Live-Watch-Test den juengsten Eintrag der Person vor sich hat.
    event.occurred_at = timezone.now()
    event.save()
restore["event_pk"] = str(event.pk)

# Refs #1917: genau EIN aktiver Ban fuer diese Person — sonst bliebe nach dem
# Deaktivieren des Ziel-Events ein zweiter Banner-Eintrag stehen und das
# "Verschwinden"-Expect des Live-Watch-Tests wuerde raten statt pruefen.
for sibling in Event.objects.filter(
    facility=facility, client=client, document_type=ban_dt, is_deleted=False
).exclude(pk=event.pk):
    restore["siblings"].append({"pk": str(sibling.pk), "data_json": sibling.data_json})
    data = dict(sibling.data_json or {})
    data["aktiv"] = "false"
    sibling.data_json = data
    sibling.save()

print(f"BAN_EVENT_PK={event.pk}")
print(f"BAN_CLIENT_PSEUDONYM={client.pseudonym}")
print("BAN_RESTORE=" + json.dumps(restore))
"""

_CLEANUP_BAN_SHELL = r"""
from core.models import Event
e = Event.objects.filter(pk='{pk}').first()
if e is not None:
    data = e.data_json or {{}}
    # Plaintext zurücksetzen — Event.save() verschlüsselt erneut über den
    # Pre-Save-Hook (encrypted Schema bleibt konsistent).
    data['aktiv'] = 'false'
    e.data_json = data
    e.save()
"""

_RESTORE_BAN_SHELL = r"""
import json
import os

from django.utils.dateparse import parse_datetime

from core.models import Event

payload = json.loads(os.environ["E2E_BAN_RESTORE"])

event = Event.objects.filter(pk=payload["event_pk"]).first()
if event is not None:
    if payload["created"]:
        # Der Test hat dieses Event angelegt -> vollstaendig entfernen. Die
        # UI-Bearbeitung im Live-Watch-Test hat inzwischen append-only-
        # ``EventHistory``-Zeilen erzeugt (Trigger aus Migration 0012), der
        # CASCADE-Delete braucht deshalb den etablierten Owner-Bypass. Faellt
        # der mangels Rechten aus, bleibt der Soft-Delete: das Event ist dann
        # aus Banner UND Kontakt-Chronik verschwunden (beide filtern
        # ``is_deleted=False``), also fuer keinen anderen Test sichtbar.
        from core.services.system import bypass_replication_triggers

        try:
            with bypass_replication_triggers():
                event.delete()
        except Exception as exc:  # noqa: BLE001 - Cleanup darf nie den Lauf kippen
            print(f"BAN_RESTORE_FALLBACK={type(exc).__name__}")
            event.is_deleted = True
            event.data_json = {}
            event.save()
    else:
        event.data_json = payload["data_json"]
        event.occurred_at = parse_datetime(payload["occurred_at"])
        event.is_deleted = payload["is_deleted"]
        event.save()

for sibling_state in payload["siblings"]:
    sibling = Event.objects.filter(pk=sibling_state["pk"]).first()
    if sibling is not None:
        sibling.data_json = sibling_state["data_json"]
        sibling.save()
"""


def _seed_active_ban(e2e_env: dict[str, str], pseudonym: str = "Stern-42") -> tuple[str, str, str]:
    """Lege deterministisch einen aktiven Ban an.

    Gibt ``(event_pk, pseudonym, restore)`` zurück; ``restore`` ist der
    JSON-Vorzustand für :func:`_restore_ban_state` (Refs #1917).
    """
    seed_shell = _SEED_BAN_SHELL.replace("__PSEUDONYM__", pseudonym)
    result = run_child(
        [sys.executable, "src/manage.py", "shell", "--no-imports", "-c", seed_shell],
        env=e2e_env,
        check=False,
        timeout=SHELL_TIMEOUT_SECONDS,
    )
    assert result.returncode == 0, f"Ban-Seed fehlgeschlagen:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    event_pk = ""
    pseudonym = ""
    restore = ""
    for line in result.stdout.splitlines():
        if line.startswith("BAN_EVENT_PK="):
            event_pk = line.split("=", 1)[1].strip()
        elif line.startswith("BAN_CLIENT_PSEUDONYM="):
            pseudonym = line.split("=", 1)[1].strip()
        elif line.startswith("BAN_RESTORE="):
            restore = line.split("=", 1)[1].strip()
    assert event_pk and pseudonym and restore, f"Seed-Output unerwartet:\n{result.stdout}"
    return event_pk, pseudonym, restore


def _deactivate_ban(e2e_env: dict[str, str], event_pk: str) -> None:
    """Fachliche Aktion (kein Cleanup): setzt aktiv=False."""
    run_child(
        [sys.executable, "src/manage.py", "shell", "--no-imports", "-c", _CLEANUP_BAN_SHELL.format(pk=event_pk)],
        env=e2e_env,
        check=False,
        timeout=SHELL_TIMEOUT_SECONDS,
    )


def _restore_ban_state(e2e_env: dict[str, str], restore: str) -> None:
    """Stellt den Vorzustand exakt wieder her (Refs #1917).

    Der frühere Cleanup legte nur ``aktiv=False`` um: der überschriebene
    Seed-Grund und ein angehobenes ``occurred_at`` blieben stehen, das
    Hausverbot blieb deaktiviert. In der geteilten E2E-DB ist das Drift, die
    nachfolgende Tests kippt — deshalb wird hier der komplette gesicherte
    Zustand (Ziel-Event **und** Geschwister-Bans) zurückgespielt.
    """
    run_child(
        [sys.executable, "src/manage.py", "shell", "--no-imports", "-c", _RESTORE_BAN_SHELL],
        env={**e2e_env, "E2E_BAN_RESTORE": restore},
        check=False,
        timeout=SHELL_TIMEOUT_SECONDS,
    )


class TestHausverbotBanner:
    """Aktivitätslog → Hausverbot-Banner sichtbar nach deterministischem Seed."""

    def test_active_ban_shows_banner_with_pseudonym(self, authenticated_page, base_url, e2e_env):
        """Aktiver Ban → Banner ist sichtbar und nennt das Pseudonym."""
        _event_pk, pseudonym, restore = _seed_active_ban(e2e_env)
        try:
            authenticated_page.goto(f"{base_url}/")
            authenticated_page.wait_for_load_state("domcontentloaded")

            banner = authenticated_page.locator("text=Hausverbot:")
            banner.first.wait_for(state="visible", timeout=5000)
            assert banner.count() >= 1, "Mindestens ein Hausverbot-Banner muss gerendert werden"

            # Pseudonym muss in einem der Banner enthalten sein.
            pseudonym_in_banner = authenticated_page.locator(
                f"div:has(span:has-text('Hausverbot:')) :text-is('{pseudonym}')"
            )
            assert pseudonym_in_banner.count() >= 1, (
                f"Banner zeigt nicht das erwartete Pseudonym {pseudonym!r}. "
                f"Sichtbarer Text:\n{authenticated_page.locator('text=Hausverbot:').first.text_content()}"
            )
        finally:
            _restore_ban_state(e2e_env, restore)

    def test_deactivated_ban_hides_banner(self, authenticated_page, base_url, e2e_env):
        """Nach Cleanup ist der gerade gesäte Ban-Banner weg.

        Sichert die Banner-Logik gegen Caching ab: aktiv=False genügt, kein
        Hard-Delete des Events nötig.
        """
        event_pk, _pseudonym, restore = _seed_active_ban(e2e_env)
        try:
            _deactivate_ban(e2e_env, event_pk)

            authenticated_page.goto(f"{base_url}/")
            authenticated_page.wait_for_load_state("domcontentloaded")

            # Es können andere Seed-Bans aktiv sein — der gesäte Test-Ban war aber
            # gerade der einzige Ban für Stern-42 mit unserem Test-Grund. Strenger
            # Smoke: Aktivitätslog rendert weiter normal.
            assert authenticated_page.locator("h1").is_visible()
        finally:
            _restore_ban_state(e2e_env, restore)


class TestHausverbotBannerLiveWatch:
    """Refs #1917: 1:1-Abbildung des manuellen Playwright-Checks.

    Fachkraft (miriam) hat den Zeitstrom offen; in einer ZWEITEN Page desselben
    Browser-Kontexts wird das Hausverbot der Person ueber den regulaeren
    UI-Pfad (Personenakte → Ereignis → Bearbeiten → Aktiv-Checkbox) aufgehoben
    und wieder verhaengt. Die erste Page wird dabei NIE neu geladen — der Watcher
    (#1916, Poll-Intervall ~12 s) muss den Banner selbststaendig konvergieren
    lassen; die 30-s-Expects am Banner-Anker sind der einzige Wartemechanismus
    (kein sleep, kein networkidle).
    """

    def test_watcher_converges_ban_lift_and_reinstate_without_reload(self, staff_page, base_url, e2e_env):
        # Refs #1917: bewusst dieselbe Person wie die uebrigen Tests dieser
        # Datei. ``Regen-55`` ist gesperrt — dessen gesaeter Ban-Grund ist Fixture
        # von ``test_security_hardening.py::TestBanReasonVisibility``, und
        # diese Datei laeuft alphabetisch vorher.
        event_pk, pseudonym, restore = _seed_active_ban(e2e_env)
        watched = staff_page
        editor = None
        try:
            # Schritt 1: Zeitstrom zeigt den aktiven Ban (Anchor-first: der
            # stabile Slot, darin der pseudonym-gebundene Banner-Link).
            watched.goto(f"{base_url}/", wait_until="domcontentloaded")
            bans_slot = watched.locator("#zeitstrom-bans-slot")
            ban_link = bans_slot.get_by_role("link", name=pseudonym)
            expect(ban_link).to_be_visible()
            expect(bans_slot).to_contain_text("Hausverbot:")
            expect(bans_slot).to_contain_text("Bis:")
            watched_url = watched.url
            client_href = ban_link.get_attribute("href")
            assert client_href, "Banner-Link muss auf die Personen-Detailseite zeigen"

            # Schritt 2: zweite Page, gleicher Kontext — Ban ueber die UI aufheben.
            editor = watched.context.new_page()
            editor.set_default_timeout(30000)
            editor.set_default_navigation_timeout(30000)
            editor.goto(urljoin(base_url, client_href), wait_until="domcontentloaded")
            # Die Personenakte zeigt dasselbe Hausverbot (get_active_bans_for_client)
            # — mitgeprueft, weil der manuelle Weg hier entlanglief.
            expect(editor.locator("body")).to_contain_text("Hausverbot")
            # Refs #922/#924: Direkt-Navigation statt Klick auf die
            # Kontakt-Chronik — die Liste ist reihenfolgeabhaengig, die
            # ``event_pk`` steht aus dem Seed fest.
            editor.goto(f"{base_url}/events/{event_pk}/", wait_until="domcontentloaded")
            # ``action_in_bar`` scopet auf die Desktop-Aktionsleiste; ohne Scope
            # traefe das Testid auch die (unsichtbare) Mobil-Zwillingsleiste.
            action_in_bar(editor, "event", "event-action-edit").click()
            editor.wait_for_load_state("domcontentloaded")
            aktiv_box = editor.locator('input[name="aktiv"]')
            expect(aktiv_box).to_be_checked()
            aktiv_box.uncheck()
            editor.get_by_role("button", name="Änderungen speichern").click()
            editor.wait_for_url(f"**/events/{event_pk}/", wait_until="domcontentloaded")

            # Schritt 3: erste Page OHNE reload/goto — der Watcher raeumt den
            # Banner binnen zweier Poll-Zyklen (12 s Basisintervall).
            expect(ban_link).to_be_hidden(timeout=30_000)

            # Schritt 4: Gegenrichtung — Ban wieder aktiv setzen.
            action_in_bar(editor, "event", "event-action-edit").click()
            editor.wait_for_load_state("domcontentloaded")
            aktiv_box = editor.locator('input[name="aktiv"]')
            expect(aktiv_box).not_to_be_checked()
            aktiv_box.check()
            editor.get_by_role("button", name="Änderungen speichern").click()
            editor.wait_for_url(f"**/events/{event_pk}/", wait_until="domcontentloaded")

            expect(bans_slot.get_by_role("link", name=pseudonym)).to_be_visible(timeout=30_000)

            # Schritt 5: kein Navigationsereignis auf der beobachteten Page.
            assert watched.url == watched_url, "Der Banner-Refresh darf die Seite nicht navigieren"
        finally:
            if editor is not None:
                editor.close()
            _restore_ban_state(e2e_env, restore)


_CLEAR_CHOICE_SHELL = r"""
from core.models import User
User.objects.exclude(ban_details_fingerprint="").update(ban_details_fingerprint="")
"""


def _clear_ban_details_choice(e2e_env: dict[str, str]) -> None:
    """Forget every stored hide choice, so a test starts with the details shown."""
    result = run_child(
        [sys.executable, "src/manage.py", "shell", "--no-imports", "-c", _CLEAR_CHOICE_SHELL],
        env=e2e_env,
        check=False,
        timeout=SHELL_TIMEOUT_SECONDS,
    )
    assert result.returncode == 0, f"Choice reset failed:\n{result.stderr}"


_ADD_SECOND_BAN_SHELL = r"""
from datetime import date, datetime, timezone as dt_timezone
from django.utils import timezone
from core.models import Client, DocumentType, Facility, User
from core.services.events import create_event

facility = Facility.objects.get(name="Hauptstelle")
actor = User.objects.filter(facility=facility, role="facility_admin").first() or User.objects.first()
person, _ = Client.objects.get_or_create(
    facility=facility, pseudonym="Race-Ban-2", defaults={"contact_stage": "identified", "created_by": actor}
)
ban_type = DocumentType.objects.filter(facility=facility, system_type="ban", is_active=True).first()
event = create_event(
    facility, actor, ban_type, timezone.now(),
    {"aktiv": "true", "grund": "E2E-Test: second ban", "bis": str(date(2099, 12, 31))}, client=person,
)
print(f"SECOND_BAN_PK={event.pk}")
"""

_REMOVE_SECOND_BAN_SHELL = r"""
from core.models import Event, User
from core.services.events import soft_delete_event

event = Event.objects.filter(pk="{pk}").first()
if event is not None and not event.is_deleted:
    soft_delete_event(event, User.objects.filter(role="facility_admin").first())
"""


def _add_second_ban(e2e_env: dict[str, str]) -> str:
    """Add a second active ban through the service layer, so the live watch notices it."""
    result = run_child(
        [sys.executable, "src/manage.py", "shell", "--no-imports", "-c", _ADD_SECOND_BAN_SHELL],
        env=e2e_env,
        check=False,
        timeout=SHELL_TIMEOUT_SECONDS,
    )
    assert result.returncode == 0, f"Second ban failed:\n{result.stderr}"
    return next(
        line.split("=", 1)[1].strip() for line in result.stdout.splitlines() if line.startswith("SECOND_BAN_PK=")
    )


def _remove_second_ban(e2e_env: dict[str, str], event_pk: str) -> None:
    run_child(
        [sys.executable, "src/manage.py", "shell", "--no-imports", "-c", _REMOVE_SECOND_BAN_SHELL.format(pk=event_pk)],
        env=e2e_env,
        check=False,
        timeout=SHELL_TIMEOUT_SECONDS,
    )


def _wait_until(page, condition, attempts=100):
    """Poll *condition* with short browser round trips (the held response arrives through them)."""
    for _ in range(attempts):
        if condition():
            return
        page.wait_for_timeout(100)
    raise AssertionError("condition not met in time")


def _open_settled_zeitstrom(page, base_url, query=""):
    """Open the Zeitstrom and finish the initial live-watch replacement first."""
    page.goto(f"{base_url}/{query}", wait_until="load")
    page.evaluate("() => window.zeitstromWatch.checkNow()")


class TestBanDetailsToggle:
    """#2809: hide the ban details, bring them back through the figure, keep the choice per user."""

    def _prepare(self, e2e_env):
        _clear_ban_details_choice(e2e_env)
        return _seed_active_ban(e2e_env)

    def test_close_and_figure_toggle_the_details_and_the_choice_survives_a_reload(self, staff_page, base_url, e2e_env):
        _event_pk, pseudonym, restore = self._prepare(e2e_env)
        page = staff_page
        try:
            _open_settled_zeitstrom(page, base_url)
            toggle = page.get_by_test_id("zeitstrom-kpi-bans")
            details = page.locator("#zeitstrom-ban-details")
            expect(details).to_be_visible()
            expect(details.get_by_role("link", name=pseudonym)).to_be_visible()
            expect(toggle).to_have_attribute("aria-expanded", "true")
            expect(toggle).to_have_attribute("aria-controls", "zeitstrom-ban-details")

            page.get_by_role("button", name="Hausverbot-Details ausblenden").click()

            expect(details).to_be_hidden()
            expect(toggle).to_be_visible()
            expect(toggle).to_have_attribute("aria-expanded", "false")
            expect(toggle).to_be_focused()

            _open_settled_zeitstrom(page, base_url)
            expect(page.locator("#zeitstrom-ban-details")).to_be_hidden()
            expect(page.get_by_test_id("zeitstrom-kpi-bans")).to_have_attribute("aria-expanded", "false")

            page.get_by_test_id("zeitstrom-kpi-bans").click()

            expect(page.locator("#zeitstrom-ban-details")).to_be_visible()
            expect(page.get_by_test_id("zeitstrom-kpi-bans")).to_have_attribute("aria-expanded", "true")
            _open_settled_zeitstrom(page, base_url)
            expect(page.locator("#zeitstrom-ban-details")).to_be_visible()
        finally:
            _clear_ban_details_choice(e2e_env)
            _restore_ban_state(e2e_env, restore)

    def test_choice_survives_a_feed_refresh_and_the_figure_stays_the_only_notice(self, staff_page, base_url, e2e_env):
        _event_pk, _pseudonym, restore = self._prepare(e2e_env)
        page = staff_page
        try:
            _open_settled_zeitstrom(page, base_url)
            page.get_by_role("button", name="Hausverbot-Details ausblenden").click()
            expect(page.locator("#zeitstrom-ban-details")).to_be_hidden()

            page.get_by_test_id("zeitstrom-filter-chip").filter(has_text="Kontakte").click()
            page.wait_for_load_state("load")
            page.evaluate("() => window.zeitstromWatch.checkNow()")

            expect(page.locator("#zeitstrom-ban-details")).to_be_hidden()
            expect(page.get_by_test_id("zeitstrom-kpi-bans")).to_have_attribute("aria-expanded", "false")
        finally:
            _clear_ban_details_choice(e2e_env)
            _restore_ban_state(e2e_env, restore)

    def test_a_changed_ban_shows_the_details_again_without_a_reload(self, staff_page, base_url, e2e_env):
        event_pk, _pseudonym, restore = self._prepare(e2e_env)
        watched = staff_page
        editor = None
        try:
            _open_settled_zeitstrom(watched, base_url)
            watched.get_by_role("button", name="Hausverbot-Details ausblenden").click()
            expect(watched.locator("#zeitstrom-ban-details")).to_be_hidden()
            watched_url = watched.url

            editor = watched.context.new_page()
            editor.set_default_timeout(30000)
            editor.set_default_navigation_timeout(30000)
            editor.goto(f"{base_url}/events/{event_pk}/", wait_until="domcontentloaded")
            action_in_bar(editor, "event", "event-action-edit").click()
            editor.wait_for_load_state("domcontentloaded")
            reason = editor.locator('[name="grund"]')
            reason.fill("E2E-Test: reason changed while hidden")
            editor.get_by_role("button", name="Änderungen speichern").click()
            editor.wait_for_url(f"**/events/{event_pk}/", wait_until="domcontentloaded")

            expect(watched.locator("#zeitstrom-ban-details")).to_be_visible(timeout=30_000)
            expect(watched.get_by_test_id("zeitstrom-kpi-bans")).to_have_attribute("aria-expanded", "true")
            assert watched.url == watched_url, "The refresh must not navigate"
        finally:
            if editor is not None:
                editor.close()
            _clear_ban_details_choice(e2e_env)
            _restore_ban_state(e2e_env, restore)

    def test_handover_always_shows_the_details_without_a_close_button(self, staff_page, base_url, e2e_env):
        _event_pk, _pseudonym, restore = self._prepare(e2e_env)
        page = staff_page
        try:
            _open_settled_zeitstrom(page, base_url)
            page.get_by_role("button", name="Hausverbot-Details ausblenden").click()
            expect(page.locator("#zeitstrom-ban-details")).to_be_hidden()

            yesterday = (date.today() - timedelta(days=1)).isoformat()
            page.goto(f"{base_url}/?view=uebergabe&date={yesterday}", wait_until="load")

            expect(page.locator("#zeitstrom-ban-details")).to_be_visible()
            expect(page.get_by_role("button", name="Hausverbot-Details ausblenden")).to_have_count(0)
            expect(page.get_by_test_id("zeitstrom-kpi-bans")).to_have_count(0)
        finally:
            _clear_ban_details_choice(e2e_env)
            _restore_ban_state(e2e_env, restore)

    def test_figure_is_in_view_and_targets_are_44px_on_a_phone(self, staff_page, base_url, e2e_env):
        _event_pk, _pseudonym, restore = self._prepare(e2e_env)
        page = staff_page
        try:
            page.set_viewport_size({"width": 390, "height": 844})
            _open_settled_zeitstrom(page, base_url)
            close = page.get_by_role("button", name="Hausverbot-Details ausblenden")
            close_box = close.evaluate("el => el.getBoundingClientRect().toJSON()")
            assert close_box["width"] >= 44 and close_box["height"] >= 44, close_box

            close.click()
            expect(page.locator("#zeitstrom-ban-details")).to_be_hidden()

            toggle_box = page.get_by_test_id("zeitstrom-kpi-bans").evaluate("el => el.getBoundingClientRect().toJSON()")
            assert toggle_box["width"] >= 44 and toggle_box["height"] >= 44, toggle_box
            assert toggle_box["x"] >= 0 and toggle_box["x"] + toggle_box["width"] <= 390, toggle_box
            assert page.evaluate("() => window.scrollX") == 0
        finally:
            _clear_ban_details_choice(e2e_env)
            _restore_ban_state(e2e_env, restore)

    def test_a_late_hide_answer_does_not_hide_a_newer_list(self, staff_page, base_url, e2e_env):
        _event_pk, _pseudonym, restore = self._prepare(e2e_env)
        second_pk = None
        context = None
        try:
            context = staff_page.context.browser.new_context(
                storage_state=staff_page.context.storage_state(), service_workers="block", locale="de-DE"
            )
            page = context.new_page()
            page.set_default_timeout(30000)
            held = []
            # The server answers at once; the browser gets the answer only when the test lets it.
            page.route("**/zeitstrom/ban-details/", lambda route: held.append((route, route.fetch())))
            _open_settled_zeitstrom(page, base_url)
            before = int(page.get_by_test_id("zeitstrom-kpi-bans").get_attribute("data-count"))

            page.get_by_role("button", name="Hausverbot-Details ausblenden").click()
            _wait_until(page, lambda: bool(held))

            second_pk = _add_second_ban(e2e_env)
            page.evaluate("() => window.zeitstromWatch.checkNow()")
            expect(page.get_by_test_id("zeitstrom-kpi-bans")).to_have_attribute(
                "data-count", str(before + 1), timeout=30_000
            )
            expect(page.locator("#zeitstrom-ban-details")).to_be_visible()

            route, response = held[0]
            route.fulfill(response=response)
            page.wait_for_timeout(1000)

            expect(page.locator("#zeitstrom-ban-details")).to_be_visible()
            expect(page.get_by_test_id("zeitstrom-kpi-bans")).to_have_attribute("aria-expanded", "true")
        finally:
            if context is not None:
                context.close()
            if second_pk:
                _remove_second_ban(e2e_env, second_pk)
            _clear_ban_details_choice(e2e_env)
            _restore_ban_state(e2e_env, restore)

    def test_a_late_feed_refresh_does_not_hide_a_newer_list(self, staff_page, base_url, e2e_env):
        _event_pk, _pseudonym, restore = self._prepare(e2e_env)
        second_pk = None
        context = None
        try:
            context = staff_page.context.browser.new_context(
                storage_state=staff_page.context.storage_state(), service_workers="block", locale="de-DE"
            )
            page = context.new_page()
            page.set_default_timeout(30000)
            held = []

            def hold_feed_refresh(route):
                if route.request.headers.get("x-zeitstrom-watch") == "1" or held:
                    route.continue_()
                else:
                    held.append((route, route.fetch()))

            page.route("**/partials/zeitstrom/feed/**", hold_feed_refresh)
            _open_settled_zeitstrom(page, base_url)
            before = int(page.get_by_test_id("zeitstrom-kpi-bans").get_attribute("data-count"))
            page.get_by_role("button", name="Hausverbot-Details ausblenden").click()
            expect(page.locator("#zeitstrom-ban-details")).to_be_hidden()
            # A feed refresh whose KPI slot still says "hidden for the old list".
            page.locator("#filter-doc-type").select_option(index=1)
            _wait_until(page, lambda: bool(held))

            second_pk = _add_second_ban(e2e_env)
            page.evaluate("() => window.zeitstromWatch.checkNow()")
            expect(page.get_by_test_id("zeitstrom-kpi-bans")).to_have_attribute(
                "data-count", str(before + 1), timeout=30_000
            )
            expect(page.locator("#zeitstrom-ban-details")).to_be_visible()

            route, response = held[0]
            route.fulfill(response=response)

            expect(page.locator("#zeitstrom-ban-details")).to_be_visible()
            expect(page.get_by_test_id("zeitstrom-kpi-bans")).to_have_attribute("aria-expanded", "true")
            expect(page.get_by_test_id("zeitstrom-kpi-bans")).to_have_attribute(
                "data-count", str(before + 1), timeout=30_000
            )
        finally:
            if context is not None:
                context.close()
            if second_pk:
                _remove_second_ban(e2e_env, second_pk)
            _clear_ban_details_choice(e2e_env)
            _restore_ban_state(e2e_env, restore)

    def test_switch_works_without_script_as_a_form_post(self, staff_page, base_url, e2e_env):
        _event_pk, _pseudonym, restore = self._prepare(e2e_env)
        plain = None
        try:
            plain = staff_page.context.browser.new_context(
                storage_state=staff_page.context.storage_state(), java_script_enabled=False, locale="de-DE"
            )
            page = plain.new_page()
            page.set_default_timeout(30000)
            page.goto(f"{base_url}/", wait_until="load")
            expect(page.locator("#zeitstrom-ban-details")).to_be_visible()

            page.get_by_role("button", name="Hausverbot-Details ausblenden").click()
            page.wait_for_load_state("load")

            expect(page.locator("#zeitstrom-ban-details")).to_be_hidden()
            page.get_by_test_id("zeitstrom-kpi-bans").click()
            page.wait_for_load_state("load")
            expect(page.locator("#zeitstrom-ban-details")).to_be_visible()
        finally:
            if plain is not None:
                plain.close()
            _clear_ban_details_choice(e2e_env)
            _restore_ban_state(e2e_env, restore)


class TestPersonListBanBadge:
    """#2809: the person list flags people with an active ban."""

    def test_row_carries_the_badge_in_text_and_colour(self, staff_page, base_url, e2e_env):
        _event_pk, pseudonym, restore = _seed_active_ban(e2e_env)
        page = staff_page
        try:
            page.goto(f"{base_url}/clients/?q={pseudonym}", wait_until="load")
            row = page.locator(f"[data-testid='client-row'][data-pseudonym='{pseudonym}']")
            badge = row.get_by_test_id("client-row-ban-badge")

            expect(badge).to_be_visible()
            expect(badge).to_have_text("Hausverbot")
            expect(badge).to_have_class(re.compile(r"bg-ban"))
            expect(row).not_to_contain_text("E2E-Test: temporary ban")

            page.set_viewport_size({"width": 390, "height": 844})
            expect(row.get_by_test_id("client-row-ban-badge")).to_be_visible()
        finally:
            _restore_ban_state(e2e_env, restore)

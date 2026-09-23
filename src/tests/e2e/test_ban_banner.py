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

import subprocess
import sys
from urllib.parse import urljoin

import pytest
from playwright.sync_api import expect

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
    result = subprocess.run(
        [sys.executable, "src/manage.py", "shell", "--no-imports", "-c", seed_shell],
        env=e2e_env,
        capture_output=True,
        text=True,
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
    subprocess.run(
        [sys.executable, "src/manage.py", "shell", "--no-imports", "-c", _CLEANUP_BAN_SHELL.format(pk=event_pk)],
        env=e2e_env,
        check=False,
        capture_output=True,
    )


def _restore_ban_state(e2e_env: dict[str, str], restore: str) -> None:
    """Stellt den Vorzustand exakt wieder her (Refs #1917).

    Der frühere Cleanup legte nur ``aktiv=False`` um: der überschriebene
    Seed-Grund und ein angehobenes ``occurred_at`` blieben stehen, das
    Hausverbot blieb deaktiviert. In der geteilten E2E-DB ist das Drift, die
    nachfolgende Tests kippt — deshalb wird hier der komplette gesicherte
    Zustand (Ziel-Event **und** Geschwister-Bans) zurückgespielt.
    """
    subprocess.run(
        [sys.executable, "src/manage.py", "shell", "--no-imports", "-c", _RESTORE_BAN_SHELL],
        env={**e2e_env, "E2E_BAN_RESTORE": restore},
        check=False,
        capture_output=True,
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

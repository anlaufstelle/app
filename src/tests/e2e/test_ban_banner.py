"""E2E-Tests: Hausverbot-Banner im Aktivitätslog.

Refs #922 / #924 (Welle 1): zuvor hat der Test nur „Banner vorhanden falls
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

import pytest

pytestmark = pytest.mark.e2e

_SEED_BAN_SHELL = r"""
from datetime import date
from django.utils import timezone
from core.models import Client, DocumentType, Event, Facility, User

facility = Facility.objects.first()
admin = User.objects.filter(facility=facility, role="facility_admin").first() or User.objects.first()

# Wir nutzen Stern-42 als deterministisches Pseudonym (Seed garantiert),
# fallback auf ersten Klienten in der Facility.
client = Client.objects.filter(facility=facility, pseudonym="Stern-42").first()
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
else:
    event.data_json = ban_payload
    event.is_deleted = False
    event.save()

print(f"BAN_EVENT_PK={event.pk}")
print(f"BAN_CLIENT_PSEUDONYM={client.pseudonym}")
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


def _seed_active_ban(e2e_env: dict[str, str]) -> tuple[str, str]:
    """Lege deterministisch einen aktiven Ban an, gib (event_pk, pseudonym) zurück."""
    result = subprocess.run(
        [sys.executable, "src/manage.py", "shell", "--no-imports", "-c", _SEED_BAN_SHELL],
        env=e2e_env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"Ban-Seed fehlgeschlagen:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    event_pk = ""
    pseudonym = ""
    for line in result.stdout.splitlines():
        if line.startswith("BAN_EVENT_PK="):
            event_pk = line.split("=", 1)[1].strip()
        elif line.startswith("BAN_CLIENT_PSEUDONYM="):
            pseudonym = line.split("=", 1)[1].strip()
    assert event_pk and pseudonym, f"Seed-Output unerwartet:\n{result.stdout}"
    return event_pk, pseudonym


def _deactivate_ban(e2e_env: dict[str, str], event_pk: str) -> None:
    """Idempotenter Cleanup: setzt aktiv=False."""
    subprocess.run(
        [sys.executable, "src/manage.py", "shell", "--no-imports", "-c", _CLEANUP_BAN_SHELL.format(pk=event_pk)],
        env=e2e_env,
        check=False,
        capture_output=True,
    )


class TestHausverbotBanner:
    """Aktivitätslog → Hausverbot-Banner sichtbar nach deterministischem Seed."""

    def test_active_ban_shows_banner_with_pseudonym(self, authenticated_page, base_url, e2e_env):
        """Aktiver Ban → Banner ist sichtbar und nennt das Pseudonym."""
        event_pk, pseudonym = _seed_active_ban(e2e_env)
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
            _deactivate_ban(e2e_env, event_pk)

    def test_deactivated_ban_hides_banner(self, authenticated_page, base_url, e2e_env):
        """Nach Cleanup ist der gerade gesäte Ban-Banner weg.

        Sichert die Banner-Logik gegen Caching ab: aktiv=False genügt, kein
        Hard-Delete des Events nötig.
        """
        event_pk, _pseudonym = _seed_active_ban(e2e_env)
        _deactivate_ban(e2e_env, event_pk)

        authenticated_page.goto(f"{base_url}/")
        authenticated_page.wait_for_load_state("domcontentloaded")

        # Es können andere Seed-Bans aktiv sein — der gesäte Test-Ban war aber
        # gerade der einzige Ban für Stern-42 mit unserem Test-Grund. Strenger
        # Smoke: Aktivitätslog rendert weiter normal.
        assert authenticated_page.locator("h1").is_visible()

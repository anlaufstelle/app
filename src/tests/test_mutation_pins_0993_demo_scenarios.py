"""Pins fuer die Auswahl des Demo-Kontos (``core.services.demo_scenarios``).

Quelle: Mutations-Delta des Checkpoints release-0.99.2, 1 Mutant in
``account_for``: nur aktive Konten kommen in Frage. Die Ansicht meldet das
gewaehlte Konto ohne ``authenticate()`` an — ein deaktiviertes Konto darf
deshalb nie gewaehlt werden, auch nicht, wenn es die kleinste PK hat.
"""

import pytest

from core.models import User
from core.services.demo_scenarios import account_for

pytestmark = pytest.mark.django_db


class TestAccountFor:
    def test_deaktiviertes_konto_mit_kleinster_pk_wird_uebersprungen(self, facility):
        """x_account_for__mutmut_8: ohne ``is_active=True`` gewinnt das
        deaktivierte Konto ueber ``order_by("pk")``."""
        konten = [
            User.objects.create_user(username=f"demo-staff-{i}", role=User.Role.STAFF, facility=facility)
            for i in range(2)
        ]
        erstes, zweites = sorted(konten, key=lambda u: u.pk)
        User.objects.filter(pk=erstes.pk).update(is_active=False)
        assert account_for(facility, User.Role.STAFF) == zweites

    def test_nur_deaktivierte_konten_ergeben_kein_konto(self, facility):
        """x_account_for__mutmut_8: ohne Filter kaeme das deaktivierte Konto
        statt ``None`` zurueck."""
        konto = User.objects.create_user(username="demo-lead", role=User.Role.LEAD, facility=facility)
        User.objects.filter(pk=konto.pk).update(is_active=False)
        assert account_for(facility, User.Role.LEAD) is None

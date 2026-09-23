"""Pins fuer die Sperr-PK-Liste der Personenzeilen (``core.services.client.locking``).

Quelle: Mutations-Delta des Checkpoints release-0.99.2, 5 Mutanten in
``client_pks``. Gruppen:

* Sortierung ueber die Zeichenkette (``key=str``) — gemischte Eingaben aus
  PK-Zeichenketten und Instanzen (``UUID``) sind nativ nicht vergleichbar.
* ``None``-Filter ueber ``getattr(ref, "pk", ref)`` — eine Referenz mit
  ``pk=None`` ist kein Personenbezug und faellt heraus.
"""

from types import SimpleNamespace

import pytest

from core.models import Client
from core.services.client.locking import client_pks

pytestmark = pytest.mark.django_db


class TestClientPks:
    def test_gemischte_referenzen_nach_zeichenkette_sortiert(self, facility, staff_user):
        """x_client_pks__mutmut_2 (``key=None``) und _4 (``key`` fehlt): ohne
        Text-Sortierung vergleicht ``sorted`` ``str`` mit ``UUID`` und wirft
        ``TypeError``; erwartet ist die stringbasierte Reihenfolge."""
        a = Client.objects.create(facility=facility, pseudonym="Lock-A", created_by=staff_user)
        b = Client.objects.create(facility=facility, pseudonym="Lock-B", created_by=staff_user)
        erste, zweite = sorted([a, b], key=lambda c: str(c.pk))
        ergebnis = client_pks([str(zweite.pk), erste])
        assert ergebnis == [erste.pk, str(zweite.pk)]
        assert [str(pk) for pk in ergebnis] == sorted([str(a.pk), str(b.pk)])

    def test_referenz_ohne_pk_faellt_heraus(self):
        """x_client_pks__mutmut_13 (Bedingung prueft ``ref`` statt ``ref.pk``),
        _19 (``getattr(ref, "XXpkXX", ref)``) und _20 (``getattr(ref, "PK",
        ref)``): eine Referenz mit ``pk=None`` landete als ``None`` in der Liste."""
        assert client_pks([SimpleNamespace(pk=None), None]) == []

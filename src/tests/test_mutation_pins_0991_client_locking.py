"""Pin-Test des Mutations-Delta-Checkpoints der Welle M (release-0.99.1) zu
``core.services.client.locking``.

Quelle: Ticket #2322 (#2322),
Survivor-Triage des Checkpoint-Deltas ``6435c18e`` (BASE v0.99.0). Gepinnt
werden die drei Prio-2-Survivor dieses Moduls: ``client_pks`` filtert
``None``-Werte ueber ``getattr(ref, "pk", ref) is not None`` — fuer eine laut
Funktionsvertrag zulaessige UNGESPEICHERTE ``Client``-Instanz (``pk=None``
explizit, nicht der Default-``uuid4``) muss dieses Praedikat greifen. Alle
drei Mutanten (Objektargument -> ``None``, Attributname -> ``"XXpkXX"``/
``"PK"``) machen das Praedikat unwirksam: die Instanz besteht den Filter,
obwohl ihre PK ``None`` ist — ``client_pks`` liefert ``[None]`` statt ``[]``.

``lock_clients`` selbst ist bewusst NICHT gesondert gepinnt: Django loest
``filter(pk__in=[None])`` intern als ``EmptyResultSet`` auf (verifiziert —
0 SQL-Statements sowohl fuer ``pks=[]`` als auch fuer ``pks=[None]``), die im
Ticket beschriebene „unnoetige Abfrage" ist auf dieser PostgreSQL-Version also
nicht als Query-Differenz beobachtbar. Der Rueckgabewert von ``client_pks``
selbst bleibt die beobachtbare, direkte Wirkung aller drei Mutanten.

Konvention wie ``src/tests/test_mutation_pins_094_attachments_crud.py``.
"""

import pytest

from core.models import Client
from core.services.client.locking import client_pks

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


class TestClientPks:
    def test_ungespeicherte_instanz_faellt_heraus(self, facility):
        """Pin: ``x_client_pks__mutmut_13`` (``getattr(None, "pk", ref)``
        statt ``getattr(ref, "pk", ref)``), ``…__mutmut_19`` (Attributname
        ``"XXpkXX"``) und ``…__mutmut_20`` (Attributname ``"PK"``).

        ``Client(pk=None)`` — explizit unpersistiert, kein ``uuid4``-Default
        (der nur bei FEHLENDEM Keyword greift). Original: ``ref.pk is None``
        -> herausgefiltert, ``client_pks`` liefert ``[]``. Mutant: das
        None-Praedikat greift nicht, die Instanz besteht den Filter — das
        SET-Element (ungebrochene zweite ``getattr``-Stelle) liefert trotzdem
        ``None`` -> ``client_pks`` liefert ``[None]``.
        """
        ungespeichert = Client(pk=None, facility=facility, pseudonym="Pins-0991-unsaved")

        pks = client_pks([ungespeichert])

        assert pks == []

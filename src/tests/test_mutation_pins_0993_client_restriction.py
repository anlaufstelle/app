"""Pins fuer das Art.-18-Gate mehrerer Personen (``core.services.client.restriction``).

Quelle: Mutations-Delta des Checkpoints release-0.99.2, 4 Mutanten in
``assert_processing_allowed_for``. Gruppen:

* Default ``lock=False`` — ohne ausdrueckliches ``lock`` liest das Gate
  ungesperrt (keine ``FOR UPDATE``-Abfrage).
* Ungesperrter Zweig — er muss die Einschraenkung jeder einzelnen Person
  frisch lesen, bei keiner Einschraenkung regulaer durchlaufen und bei einer
  einzigen eingeschraenkten Person ablehnen.
"""

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext

from core.models import Client
from core.services.client.restriction import PROCESSING_RESTRICTED_MESSAGE, assert_processing_allowed_for

pytestmark = pytest.mark.django_db


@pytest.fixture
def eingeschraenkt(facility, staff_user):
    client = Client.objects.create(facility=facility, pseudonym="Art18-Eingeschraenkt", created_by=staff_user)
    Client.objects.filter(pk=client.pk).update(processing_restricted=True)
    return client


class TestAssertProcessingAllowedFor:
    def test_default_liest_ungesperrt(self, client_identified, client_qualified):
        """x_assert_processing_allowed_for__mutmut_1: Default ``lock=True``
        schaltete jeden Aufrufer ohne ``lock`` auf den sperrenden Pfad."""
        with CaptureQueriesContext(connection) as ctx:
            assert assert_processing_allowed_for([client_identified, client_qualified]) is None
        assert len(ctx.captured_queries) == 2
        assert [q["sql"].endswith("FOR UPDATE") for q in ctx.captured_queries] == [False, False]

    def test_ungesperrt_ohne_einschraenkung_laeuft_durch(self, client_identified):
        """x_assert_processing_allowed_for__mutmut_7: ``any(None)`` wirft
        ``TypeError`` statt regulaer durchzulaufen."""
        assert assert_processing_allowed_for([client_identified], lock=False) is None

    def test_ungesperrt_lehnt_eingeschraenkte_person_ab(self, eingeschraenkt):
        """x_assert_processing_allowed_for__mutmut_6: ``restricted = None``
        setzt das Gate im ungesperrten Pfad ausser Kraft."""
        with pytest.raises(ValidationError) as exc_info:
            assert_processing_allowed_for([eingeschraenkt], lock=False)
        assert exc_info.value.messages == [str(PROCESSING_RESTRICTED_MESSAGE)]

    def test_ungesperrt_prueft_jede_person_einzeln(self, eingeschraenkt, client_identified):
        """x_assert_processing_allowed_for__mutmut_8: ``_restricted_flag(None)``
        fuer jede Person ignoriert die eingeschraenkte in der Menge."""
        with pytest.raises(ValidationError) as exc_info:
            assert_processing_allowed_for([eingeschraenkt, client_identified], lock=False)
        assert exc_info.value.messages == [str(PROCESSING_RESTRICTED_MESSAGE)]

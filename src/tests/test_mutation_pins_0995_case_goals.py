"""Pins fuer die Einrichtungsgrenze der Meilenstein-Dienste (``core.services.case.goals``).

Quelle: Mutations-Delta des Checkpoints release-0.99.4, 1 Mutant in
``_assert_actor_facility``: ohne ``getattr``-Default warf ein Akteur ohne
``facility_id``-Attribut ``AttributeError`` statt der fail-closed-Absage
mit der Einrichtungsmeldung. Gepinnt am Helfer und am Dienst
``delete_milestone``, der ihn vor jeder Sperre ruft.
"""

import pytest
from django.core.exceptions import ValidationError

from core.models.outcome import Milestone
from core.services.case.goals import _assert_actor_facility, delete_milestone

pytestmark = pytest.mark.django_db

EINRICHTUNGSMELDUNG = "Fall gehört zu einer anderen Einrichtung."


class TestAssertActorFacility:
    """``_assert_actor_facility``: Akteur ohne Einrichtung."""

    def test_akteur_ohne_einrichtung_wird_abgewiesen(self, facility, milestone):
        """x__assert_actor_facility__mutmut_7: ``AttributeError`` statt
        ``ValidationError``; ueber ``delete_milestone`` bleibt der Meilenstein
        in beiden Faellen stehen, nur Fehlertyp und Meldung unterscheiden
        sich."""
        with pytest.raises(ValidationError) as exc:
            _assert_actor_facility(None, facility.pk)
        assert exc.value.messages == [EINRICHTUNGSMELDUNG]
        with pytest.raises(ValidationError) as exc:
            delete_milestone(milestone, None)
        assert exc.value.messages == [EINRICHTUNGSMELDUNG]
        assert Milestone.objects.filter(pk=milestone.pk).exists()

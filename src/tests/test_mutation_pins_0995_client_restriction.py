"""Pins fuer die Einrichtungsgrenze der Verarbeitungseinschraenkung (``core.services.client.restriction``).

Quelle: Mutations-Delta des Checkpoints release-0.99.4, 2 Mutanten, beide in
``_lock_client_of_actor_facility`` (Sperre der Personenzeile fuer
``restrict_processing`` und ``lift_processing_restriction``).
Gruppen:

* Sperr-Lookup — verliert ``pk`` und sperrt die erste Person der
  Einrichtung; der Art.-18-Vermerk landete an der falschen Person.
* Akteur ohne Einrichtung — ein Nutzerobjekt ohne ``facility_id`` wird
  fail-closed mit der Einrichtungsmeldung abgewiesen, nicht mit
  ``AttributeError``, und nichts wird gesperrt.

Die falsche Person steht in der Standardsortierung (``pseudonym``) vor der
Zielperson; zusaetzlich halten die Pins fest, welche Zeile gesperrt wird
(``tests._row_lock_helpers``: PostgreSQL plant die mitgeschnittene Anweisung;
massgeblich sind Tabelle, Sperrart ``FOR UPDATE`` und die Filterwerte je
Spalte, nicht Alias, Quoting, Projektion, Reihenfolge oder Literalform).
"""

from types import SimpleNamespace

import pytest
from django.core.exceptions import ValidationError

from core.models import Client
from core.services.client.restriction import restrict_processing
from tests._row_lock_helpers import mitschnitt

pytestmark = pytest.mark.django_db

EINRICHTUNGSMELDUNG = "Person gehört zu einer anderen Einrichtung."


def _person(facility, user, pseudonym):
    return Client.objects.create(facility=facility, pseudonym=pseudonym, created_by=user)


class TestLockClientOfActorFacility:
    """``_lock_client_of_actor_facility``: Sperre der Zielperson in der Einrichtung des Akteurs."""

    def test_vermerk_landet_nur_an_der_zielperson(self, facility, staff_user):
        """x__lock_client_of_actor_facility__mutmut_14: ohne ``pk`` sperrte
        der Lookup die erste Person der Einrichtung (Sortierung
        ``pseudonym``) und setzte den Art.-18-Vermerk dort."""
        nachbar = _person(facility, staff_user, "Alpha-Nachbar")
        ziel = _person(facility, staff_user, "Zulu-Ziel")
        ergebnis = restrict_processing(ziel, staff_user, reason=Client.RestrictionReason.ACCURACY_DISPUTED)
        assert ergebnis.pk == ziel.pk
        ziel.refresh_from_db()
        nachbar.refresh_from_db()
        assert ziel.processing_restricted is True
        assert nachbar.processing_restricted is False

    def test_sperre_traegt_pk_und_einrichtung(self, facility, staff_user):
        """x__lock_client_of_actor_facility__mutmut_14: every row lock
        ``restrict_processing`` takes on the person is ``FOR UPDATE`` on
        exactly the target person in the facility of the actor, and the
        locked row is the one returned."""
        ziel = _person(facility, staff_user, "Zulu-Ziel")
        with mitschnitt() as abfragen:
            result = restrict_processing(ziel, staff_user, reason=Client.RestrictionReason.ACCURACY_DISPUTED)
        assert result.pk == ziel.pk
        sperren = abfragen.sperren("core_client")
        assert sperren
        assert all(a.sperrart == "UPDATE" for a in sperren)
        assert all(a.traegt(id=[ziel.pk], facility_id=[facility.pk]) for a in sperren)

    def test_akteur_ohne_einrichtung_wird_ohne_sperre_abgewiesen(self, facility, staff_user):
        """x__lock_client_of_actor_facility__mutmut_6: an actor object that
        passes the role check but has no ``facility_id`` attribute raised
        ``AttributeError`` instead of the fail-closed refusal with the
        facility message. Observed through ``restrict_processing``: the
        refusal, no lock on the person and no restriction flag."""
        ziel = _person(facility, staff_user, "Zulu-Ziel")
        actor_without_facility = SimpleNamespace(is_staff_or_above=True)
        with mitschnitt() as abfragen:
            with pytest.raises(ValidationError) as exc:
                restrict_processing(ziel, actor_without_facility, reason=Client.RestrictionReason.ACCURACY_DISPUTED)
        assert exc.value.messages == [EINRICHTUNGSMELDUNG]
        assert abfragen.sperren("core_client") == []
        ziel.refresh_from_db()
        assert ziel.processing_restricted is False

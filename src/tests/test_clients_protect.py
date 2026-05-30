"""PROTECT-Constraint-Tests fuer Client.delete() (Refs Matrix DEV-CLIENT-15).

Refs Matrix DEV-CLIENT-15: ``Case.client`` ist ``on_delete=PROTECT``
(siehe ``src/core/models/case.py``). Ein hartes ``Client.delete()`` auf
einer Person mit mindestens einem aktiven Case muss von Postgres bzw.
Djangos ORM unterbunden werden — sonst gingen versehentlich Fall-Daten
verloren, die ueber das ``Client``-Aggregat verschwinden wuerden.

Diese Tests verifizieren den IST-Zustand: der PROTECT-Constraint greift,
und nach Wegraeumen der Cases laesst sich der Client regulaer loeschen.

Refs #922 (Master), #926 (Welle 3).
"""

import pytest
from django.db import IntegrityError
from django.db.models.deletion import ProtectedError

from core.models import Case, Client


@pytest.mark.django_db
class TestClientProtect:
    """``Case.client = FK(Client, on_delete=PROTECT)`` — kein Loeschen
    von Personen mit aktiven Faellen.
    """

    def test_client_delete_with_active_case_is_blocked(self, client_identified, case_open):
        """Refs Matrix DEV-CLIENT-15: Solange ein Case auf den Client
        zeigt, wird ``client.delete()`` von Django/Postgres geblockt.

        Django wirft typischerweise ``ProtectedError``; bei Bypass der
        Collector-Schicht koennte stattdessen ein ``IntegrityError`` vom
        Foreign-Key-Constraint kommen — beide werden akzeptiert.
        """
        with pytest.raises((ProtectedError, IntegrityError)):
            client_identified.delete()

        # Defense-in-Depth: weder Client noch Case duerfen weg sein.
        assert Client.objects.filter(pk=client_identified.pk).exists(), (
            "Client wurde trotz PROTECT-Constraint geloescht — Datenverlust-Vektor!"
        )
        assert Case.objects.filter(pk=case_open.pk).exists(), (
            "Case wurde im fehlgeschlagenen Loeschversuch beruehrt — Rollback unvollstaendig?"
        )

    def test_client_delete_succeeds_after_cases_are_removed(self, client_identified, case_open):
        """Refs Matrix DEV-CLIENT-15: Sobald alle abhaengigen Cases
        weg sind, kann der Client regulaer geloescht werden — der
        PROTECT-Constraint blockt also nur den Lebenszyklus, nicht den
        Endzustand.
        """
        client_pk = client_identified.pk

        # Zuerst die Cases entfernen (kaskadiert Goals/Milestones, siehe
        # ``test_cases_cascade.py``).
        Case.objects.filter(client=client_identified).delete()

        # Jetzt klappt der Client-Delete.
        client_identified.delete()

        assert not Client.objects.filter(pk=client_pk).exists(), (
            "Client liess sich auch ohne Cases nicht loeschen — weitere PROTECT-Constraints bremsen?"
        )

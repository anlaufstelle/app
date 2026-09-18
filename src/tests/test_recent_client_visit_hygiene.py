"""DSGVO-Hygiene fuer ``RecentClientVisit`` (Refs #1761).

``RecentClientVisit`` traegt kein eigenes PII-Feld (nur FKs + ``visited_at``) —
das Leck ist daher keins von Klartext, sondern eines von *Verweisdauer*: bis
#1761 wurde ausschliesslich auf „letzte 20 pro User" geprunt, sodass

1. die Zeilen einer **anonymisierten** Person unbegrenzt weiterlebten (das
   „zuletzt besucht"-Widget joint auf ``client`` und listete die Person damit
   weiter — nach der Anonymisierung unter dem Ersatz-Pseudonym), und
2. eine Zeile beliebig alt werden konnte, solange dieselbe Person in den
   Top 20 blieb.

Beide Loecher schliesst #1761: Loeschen der Zeilen im Anonymisierungs-Service
(gleiche Transaktion, beide Einstiege — Hard-Pfad UND K-Anon-Bridge) plus eine
Altersfrist von ``RECENT_VISIT_MAX_AGE_DAYS`` Tagen im bestehenden Prune-Pfad.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from core.constants import RECENT_VISIT_MAX_AGE_DAYS, RECENT_VISIT_MAX_PER_USER
from core.models import Client as ClientModel
from core.models import RecentClientVisit
from core.services.client import anonymize_client, track_client_visit


def _backdate(visit, days):
    """``visited_at`` ist ``auto_now`` — Alter nur per Queryset-Update setzbar."""
    RecentClientVisit.objects.filter(pk=visit.pk).update(visited_at=timezone.now() - timedelta(days=days))


@pytest.fixture
def other_client(facility, staff_user):
    return ClientModel.objects.create(facility=facility, pseudonym="Zweitperson", created_by=staff_user)


@pytest.mark.django_db
class TestAgeBasedPrune:
    """Altersfrist: Eintraege aelter als ``RECENT_VISIT_MAX_AGE_DAYS`` fallen raus."""

    def test_constants_are_named_not_magic(self):
        """Beide Prune-Grenzen sind benannte Konstanten (kein Magic Number)."""
        assert RECENT_VISIT_MAX_PER_USER == 20
        assert RECENT_VISIT_MAX_AGE_DAYS == 90

    def test_visit_older_than_max_age_is_pruned(self, staff_user, facility, client_identified, other_client):
        """Ein ueberalterter Eintrag verschwindet beim naechsten Besuch — auch
        wenn er in den Top 20 liegt (das war bis #1761 der Fall)."""
        track_client_visit(staff_user, client_identified, facility)
        stale = RecentClientVisit.objects.get(user=staff_user, client=client_identified)
        _backdate(stale, RECENT_VISIT_MAX_AGE_DAYS + 1)

        track_client_visit(staff_user, other_client, facility)

        assert not RecentClientVisit.objects.filter(pk=stale.pk).exists()
        assert RecentClientVisit.objects.filter(user=staff_user, client=other_client).exists()

    def test_visit_within_max_age_survives(self, staff_user, facility, client_identified, other_client):
        """Knapp innerhalb der Frist bleibt der Eintrag erhalten (kein Off-by-one)."""
        track_client_visit(staff_user, client_identified, facility)
        fresh = RecentClientVisit.objects.get(user=staff_user, client=client_identified)
        _backdate(fresh, RECENT_VISIT_MAX_AGE_DAYS - 1)

        track_client_visit(staff_user, other_client, facility)

        assert RecentClientVisit.objects.filter(pk=fresh.pk).exists()

    def test_age_prune_leaves_other_users_untouched(
        self, staff_user, assistant_user, facility, client_identified, other_client
    ):
        """Der Prune ist pro User gescopt — der Besuch einer Kollegin bleibt."""
        track_client_visit(assistant_user, client_identified, facility)
        foreign = RecentClientVisit.objects.get(user=assistant_user, client=client_identified)
        _backdate(foreign, RECENT_VISIT_MAX_AGE_DAYS + 5)

        track_client_visit(staff_user, other_client, facility)

        assert RecentClientVisit.objects.filter(pk=foreign.pk).exists()

    def test_top_n_prune_still_applies(self, staff_user, facility):
        """Regression: die bestehende „letzte 20"-Grenze bleibt unveraendert."""
        for i in range(RECENT_VISIT_MAX_PER_USER + 5):
            cli = ClientModel.objects.create(facility=facility, pseudonym=f"Prune-{i:02d}", created_by=staff_user)
            track_client_visit(staff_user, cli, facility)

        assert RecentClientVisit.objects.filter(user=staff_user).count() == RECENT_VISIT_MAX_PER_USER


@pytest.mark.django_db
class TestAnonymizationDeletesVisits:
    """Anonymisierung tilgt die Besuchsspuren der Person — sofort, alle User."""

    def test_hard_path_deletes_visits_of_all_users(
        self, staff_user, assistant_user, facility, client_identified, other_client
    ):
        track_client_visit(staff_user, client_identified, facility)
        track_client_visit(assistant_user, client_identified, facility)
        track_client_visit(staff_user, other_client, facility)

        anonymize_client(client_identified, user=staff_user)

        assert not RecentClientVisit.objects.filter(client=client_identified).exists()
        # Die Zeilen anderer Personen bleiben unangetastet.
        assert RecentClientVisit.objects.filter(client=other_client).count() == 1

    def test_widget_no_longer_lists_anonymized_person(self, client, staff_user, facility, client_identified):
        """UI-Nebenwirkung: das „zuletzt besucht"-Widget zeigt die Person nicht mehr."""
        track_client_visit(staff_user, client_identified, facility)
        client.force_login(staff_user)

        before = client.get(reverse("core:account_profile")).content.decode()
        assert client_identified.pseudonym in before

        anonymize_client(client_identified, user=staff_user)

        after = client.get(reverse("core:account_profile")).content.decode()
        assert "Zuletzt besuchte Personen" not in after

    def test_k_anon_bridge_deletes_visits_too(self, staff_user, facility, client_identified):
        """Der K-Anon-Retention-Pfad darf nicht hinter dem Hard-Pfad zurueckbleiben
        (Parity-Drift war die Root Cause von #1750)."""
        from core.retention.anonymization import _k_anonymize_with_cascade

        track_client_visit(staff_user, client_identified, facility)

        _k_anonymize_with_cascade(client_identified)

        assert not RecentClientVisit.objects.filter(client=client_identified).exists()


@pytest.mark.django_db
class TestWidgetEmptyState:
    """Weniger/0 Eintraege: das Widget bleibt korrekt (Leerzustand vorhanden)."""

    def test_profile_renders_without_any_visit(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("core:account_profile"))
        assert response.status_code == 200
        assert "Zuletzt besuchte Personen" not in response.content.decode()

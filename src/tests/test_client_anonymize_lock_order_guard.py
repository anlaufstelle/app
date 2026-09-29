"""Sofort-Anonymisierung: Sperrfolge Person -> Fall -> Ereignis.

Die Kaskade der Sofort-Anonymisierung schreibt die Person, ihre Faelle und
ihre Ereignisse. Die projektweite Ordnung fuer diese drei Tabellen ist
Person -> Fall -> Ereignis — dieselbe, die die Fall-Hold-Anlage und die
Fall-Tilgung nehmen. Eine Zeile gilt dabei als genommen, sobald eine
Anweisung sie sperrt: ein ``SELECT ... FOR UPDATE`` ebenso wie ein ``UPDATE``
oder ``DELETE``, das die Zeile schreibt. Beobachtet wird, welche der drei
Tabellen zuerst erreicht wird — auch ueber schreibende Anweisungen, nicht nur
ueber die erste Sperrabfrage je Tabelle.

Die gesperrten bzw. geschriebenen Tabellen liefert der Plan von PostgreSQL
(``EXPLAIN`` ohne Ausfuehrung), nicht der Wortlaut der Anweisung.
"""

import json
import re

import pytest
from django.db import connection, transaction

from core.models import AuditLog, Case, Client, Event
from core.services.client import anonymize_client_now
from tests._row_lock_helpers import mitschnitt

pytestmark = pytest.mark.django_db

_KETTE = ("core_client", "core_case", "core_event")
_SCHREIBEND = re.compile(r"^\s*(?:UPDATE|DELETE)\b", re.IGNORECASE)


def _geschriebene_tabelle(abfrage):
    """Die Tabelle, deren Zeilen ein ``UPDATE``/``DELETE`` schreibt — laut Plan."""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("EXPLAIN (FORMAT JSON) " + abfrage.sql, abfrage.params)
        (plan,) = cursor.fetchone()
    plan = json.loads(plan) if isinstance(plan, str) else plan
    return plan[0]["Plan"].get("Relation Name")


def _erste_zugriffe(abfragen):
    """Die Tabellen der Kette in der Reihenfolge, in der eine Anweisung erstmals ihre Zeilen nimmt."""
    folge = []
    for abfrage in abfragen:
        if _SCHREIBEND.match(abfrage.sql):
            genommen = [_geschriebene_tabelle(abfrage)]
        else:
            genommen = [tabelle for tabelle in _KETTE if abfrage.sperrt(tabelle)]
        folge.extend(t for t in genommen if t in _KETTE and t not in folge)
    return folge


@pytest.fixture
def trashed_person_with_case_and_event(client_identified, admin_user, case_open, case_event):
    client_identified.soft_delete(user=admin_user)
    return client_identified


class TestSperrfolgeDerSofortAnonymisierung:
    def test_fall_vor_ereignis(self, trashed_person_with_case_and_event, admin_user, case_open, case_event):
        person = trashed_person_with_case_and_event

        with mitschnitt() as abfragen:
            anonymize_client_now(person, admin_user)

        assert _erste_zugriffe(abfragen) == list(_KETTE)
        # Die Kaskade lief vollstaendig durch — die Ordnung kostet keinen Schritt.
        person.refresh_from_db()
        assert person.anonymized_at is not None
        assert Case.objects.filter(pk=case_open.pk).exists()
        assert Event.objects.filter(pk=case_event.pk).exists()
        assert AuditLog.objects.filter(action=AuditLog.Action.CLIENT_ANONYMIZED, target_id=str(person.pk)).count() == 1

    def test_gegenprobe_person_ohne_fall(self, client_identified, admin_user, sample_event):
        client_identified.soft_delete(user=admin_user)

        with mitschnitt() as abfragen:
            anonymize_client_now(client_identified, admin_user)

        folge = _erste_zugriffe(abfragen)
        assert folge[0] == "core_client"
        assert folge.index("core_client") < folge.index("core_event")
        assert Client.objects.get(pk=client_identified.pk).anonymized_at is not None

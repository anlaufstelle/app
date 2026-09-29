"""Anonymisierung einer Person: Sperrfolge Person -> Fall -> Ereignis.

Die Kaskade der Anonymisierung schreibt die Person, ihre Faelle und ihre
Ereignisse — im Sofortpfad ebenso wie in der naechtlichen Tilgung aus dem
Papierkorb und in der Anonymisierung ueber die Aufbewahrungslaeufe (Hart- und
K-Anonymisierungspfad). Die projektweite Ordnung fuer diese drei Tabellen ist
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
from datetime import timedelta

import pytest
from django.db import connection, transaction
from django.utils import timezone

from core.models import AuditLog, Case, Client, Event, Settings
from core.retention.anonymization import anonymize_clients
from core.services.client import anonymize_client_now, anonymize_eligible_soft_deleted_clients
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


@pytest.fixture
def expired_trashed_person(trashed_person_with_case_and_event):
    """Person im Papierkorb, deren Frist abgelaufen ist — Kandidat der naechtlichen Tilgung."""
    person = trashed_person_with_case_and_event
    Client.objects.filter(pk=person.pk).update(deleted_at=timezone.now() - timedelta(days=400))
    person.refresh_from_db()
    return person


class TestSperrfolgeDerNaechtlichenTilgung:
    def test_fall_vor_ereignis(self, expired_trashed_person, facility, settings_obj, case_open, case_event):
        person = expired_trashed_person

        with mitschnitt() as abfragen:
            count = anonymize_eligible_soft_deleted_clients(facility, settings_obj)

        assert _erste_zugriffe(abfragen) == list(_KETTE)
        # Auswahl, Zaehlwert, Kaskade und Audit wie bisher.
        assert count == 1
        person.refresh_from_db()
        assert person.anonymized_at is not None
        assert Case.objects.filter(pk=case_open.pk).exists()
        assert Event.objects.filter(pk=case_event.pk).exists()
        assert AuditLog.objects.filter(action=AuditLog.Action.CLIENT_ANONYMIZED, target_id=str(person.pk)).count() == 1

    def test_gegenprobe_probelauf_sperrt_und_schreibt_nichts(self, expired_trashed_person, facility, settings_obj):
        person = expired_trashed_person

        with mitschnitt() as abfragen:
            count = anonymize_eligible_soft_deleted_clients(facility, settings_obj, dry_run=True)

        assert count == 1
        assert _erste_zugriffe(abfragen) == []
        person.refresh_from_db()
        assert person.anonymized_at is None


@pytest.fixture
def person_without_live_events(client_identified, case_open, case_event):
    """Person mit Fall, deren Ereignisse alle geloescht sind — Kandidat der Aufbewahrungslaeufe."""
    Event.objects.filter(pk=case_event.pk).update(is_deleted=True)
    return client_identified


class TestSperrfolgeDerAufbewahrungslaeufe:
    def test_hartpfad_fall_vor_ereignis(self, person_without_live_events, facility, settings_obj, case_open):
        person = person_without_live_events

        with mitschnitt() as abfragen:
            ergebnis = anonymize_clients(facility, dry_run=False)

        assert _erste_zugriffe(abfragen) == list(_KETTE)
        assert ergebnis == {"count": 1, "skipped_holds": 0}
        person.refresh_from_db()
        assert person.pseudonym.startswith("Gelöscht-")
        assert person.k_anonymized is False
        assert Case.objects.filter(pk=case_open.pk).exists()

    def test_k_anonymisierung_fall_vor_ereignis(self, person_without_live_events, facility, case_open):
        person = person_without_live_events
        Settings.objects.create(facility=facility, retention_use_k_anonymization=True, k_anonymity_threshold=5)
        # Eine ehrlich besetzte Aequivalenzklasse (>= k); ohne Ereignisse sind
        # die Genossen selbst keine Kandidaten.
        for nummer in range(4):
            Client.objects.create(
                facility=facility,
                pseudonym=f"Peer-{nummer}",
                age_cluster=person.age_cluster,
                contact_stage=person.contact_stage,
            )

        with mitschnitt() as abfragen:
            ergebnis = anonymize_clients(facility, dry_run=False)

        assert _erste_zugriffe(abfragen) == list(_KETTE)
        assert ergebnis == {"count": 1, "skipped_holds": 0}
        person.refresh_from_db()
        assert person.k_anonymized is True
        assert Case.objects.filter(pk=case_open.pk).exists()

    def test_gegenprobe_probelauf_sperrt_und_schreibt_nichts(self, person_without_live_events, facility, settings_obj):
        person = person_without_live_events

        with mitschnitt() as abfragen:
            ergebnis = anonymize_clients(facility, dry_run=True)

        assert ergebnis == {"count": 1, "skipped_holds": 0}
        assert _erste_zugriffe(abfragen) == []
        person.refresh_from_db()
        assert not person.pseudonym.startswith("Gelöscht-")

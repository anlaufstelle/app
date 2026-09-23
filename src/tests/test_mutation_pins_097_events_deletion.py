"""Mutation-Pins der Welle J (0.97, Refs #2149) — Zieltreue der Lookups im
Vier-Augen-Loeschworkflow fuer Ereignisse (``core.services.events.deletion``).

Quelle: Prio-1-Survivors des atomaren Messlaufs
(``welle-j-0.97/reports/triage-097-sec.json``, Batch ``sec``). Gepinnt werden
sechs bestaetigte Testluecken in genau zwei Familien — beide Male faellt ein
Bestandteil eines ``get()``-Lookups weg, und beide Male ueberlebt der Mutant
nur, weil jeder Bestandstest genau EINEN Loeschantrag und genau EIN Ereignis
je Einrichtung kennt:

* **Der gesperrte Antrag** (``select_for_update().get(pk=…, facility=…)`` in
  :func:`approve_deletion` und :func:`reject_deletion`) — ohne ``pk`` wird
  irgendein Antrag der Einrichtung gestempelt, ohne ``facility`` faellt die
  Mandantengrenze des Service-Pfads weg. Die DB-seitige RLS-Policy traegt hier
  nichts: die Testsuite setzt kein ``app.current_facility_id`` (das tut erst
  die Middleware, ``core/middleware/facility_scope.py``), der facility-Filter
  im Lookup ist also die einzige Grenze — genau das, was die Kommentare
  ueber beiden Lookups behaupten.
* **Das aufgeloeste Ereignis** (``Event.objects.get(pk=target_id,
  facility=…)`` in :func:`approve_deletion`) — ohne ``pk`` loescht der
  Genehmigungsschritt ein beliebiges Ereignis der Einrichtung, ohne
  ``facility`` ein Ereignis einer FREMDEN Einrichtung.

Gepinnte Mutanten (Mutantenbaum ``/work/anlaufstelle/mutants``, Stand
f5272d3e):

* ``x_approve_deletion__mutmut_14`` — ``get(pk=…, facility=…)`` -> ``get(facility=…)``
* ``x_approve_deletion__mutmut_15`` — ``get(pk=…, facility=…)`` -> ``get(pk=…)``
* ``x_approve_deletion__mutmut_24`` — ``Event.objects.get(pk=…, facility=…)`` -> ``get(facility=…)``
* ``x_approve_deletion__mutmut_25`` — ``Event.objects.get(pk=…, facility=…)`` -> ``get(pk=…)``
* ``x_reject_deletion__mutmut_4``  — ``get(pk=…, facility=…)`` -> ``get(facility=…)``
* ``x_reject_deletion__mutmut_5``  — ``get(pk=…, facility=…)`` -> ``get(pk=…)``

Hinweis zur Numerierung: ``reject_deletion`` hat seit e2b5c276 (Welle J, B1)
den Vier-Augen-Guard VOR der Sperre; im Mutantenbaum steht die Funktion noch
ohne ihn, die Nummern ``4``/``5`` bezeichnen dort dieselbe Lookup-Stelle. Die
Pins sind so gebaut, dass sie in beiden Faellen gelten: Antragsteller und
Reviewer sind durchgehend verschiedene Personen.

Konventionen wie in den 096-Pin-Dateien: eine Testklasse je Funktion, jeder
Test nennt seine Mutanten in der Docstring samt der Abweichung, Zusicherungen
vergleichen exakt (``==``/``is``), keine Fakes — es laufen echte Model-Objekte
durch die echten Services.
"""

from __future__ import annotations

import pytest
from django.utils import timezone

from core.models import DeletionRequest, Event
from core.services.events import approve_deletion, reject_deletion, request_deletion

# ``_encryption_key`` ist Pflicht: ``approve_deletion`` laeuft ueber
# ``soft_delete_event`` in den Anhang-/File-Vault-Pfad, und der Mutantenbaum
# hat keine ``.env`` — ohne die Fixture waeren die Pins dort aus dem falschen
# Grund rot (Erfahrung aus frueheren Pin-Dateien).
pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def _event(facility, client, document_type, user, *, note):
    return Event.objects.create(
        facility=facility,
        client=client,
        document_type=document_type,
        occurred_at=timezone.now(),
        data_json={"dauer": 20, "notiz": note},
        created_by=user,
    )


class TestApproveDeletionLookupZieltreue:
    def test_approve_laedt_genau_den_uebergebenen_antrag_nach(
        self, facility, client_qualified, doc_type_contact, staff_user, lead_user
    ):
        """Pins ``x_approve_deletion__mutmut_14``: ohne ``pk`` im Sperr-Lookup
        entscheidet allein die Einrichtung, welcher Antrag genehmigt wird — bei
        zwei offenen Antraegen derselben Einrichtung trifft es irgendeinen bzw.
        es fliegt ``MultipleObjectsReturned`` statt der Genehmigung."""
        event_a = _event(facility, client_qualified, doc_type_contact, staff_user, note="Antrag A")
        event_b = _event(facility, client_qualified, doc_type_contact, staff_user, note="Antrag B")
        dr_a = request_deletion(event_a, staff_user, "Bleibt offen")
        dr_b = request_deletion(event_b, staff_user, "Wird genehmigt")
        assert dr_a.pk != dr_b.pk

        approve_deletion(dr_b, lead_user)

        dr_b.refresh_from_db()
        assert dr_b.status == DeletionRequest.Status.APPROVED
        assert dr_b.reviewed_by_id == lead_user.pk
        event_b.refresh_from_db()
        assert event_b.is_deleted is True

        dr_a.refresh_from_db()
        assert dr_a.status == DeletionRequest.Status.PENDING
        assert dr_a.reviewed_by_id is None
        assert dr_a.reviewed_at is None
        event_a.refresh_from_db()
        assert event_a.is_deleted is False

    def test_approve_lookup_bleibt_an_die_antrags_einrichtung_gebunden(
        self, facility, other_facility, client_qualified, doc_type_contact, staff_user, lead_user
    ):
        """Pins ``x_approve_deletion__mutmut_15``: faellt die facility-Scopung
        des Sperr-Lookups weg, genuegt die PK — der Antrag wird ohne
        Mandantengrenze nachgeladen und genehmigt. Das Original findet unter
        der fremden Einrichtung nichts und bricht mit ``DoesNotExist`` ab,
        bevor irgendetwas geschrieben wird."""
        event = _event(facility, client_qualified, doc_type_contact, staff_user, note="Mandantengrenze")
        dr = request_deletion(event, staff_user, "Art.-17-Antrag")
        # Nur die IN-MEMORY-Instanz zeigt auf die fremde Einrichtung; die Zeile
        # in der DB bleibt unveraendert. Genau diese Instanz liefert dem Lookup
        # seinen ``facility``-Filter.
        dr.facility = other_facility

        with pytest.raises(DeletionRequest.DoesNotExist):
            approve_deletion(dr, lead_user)

        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.PENDING
        assert dr.reviewed_by_id is None
        event.refresh_from_db()
        assert event.is_deleted is False

    def test_approve_loescht_genau_das_beantragte_ereignis(
        self, facility, client_qualified, doc_type_contact, staff_user, lead_user
    ):
        """Pins ``x_approve_deletion__mutmut_24``: ohne ``pk=target_id`` wird
        das Loeschziel nicht mehr aus dem Antrag bestimmt — der
        Genehmigungsschritt greift sich ein beliebiges Ereignis der Einrichtung
        (bei zweien: ``MultipleObjectsReturned``)."""
        event_a = _event(facility, client_qualified, doc_type_contact, staff_user, note="Bleibt stehen")
        event_b = _event(facility, client_qualified, doc_type_contact, staff_user, note="Wird geloescht")
        dr = request_deletion(event_b, staff_user, "Art.-17-Antrag")

        approve_deletion(dr, lead_user)

        assert Event.objects.get(pk=event_b.pk).is_deleted is True
        assert Event.objects.get(pk=event_a.pk).is_deleted is False

    def test_approve_loest_das_ziel_nur_innerhalb_der_antrags_einrichtung_auf(
        self, facility, second_facility, foreign_event, staff_user, lead_user
    ):
        """Pins ``x_approve_deletion__mutmut_25``: ohne ``facility`` im
        Ereignis-Lookup kann ein Antrag der Einrichtung A ein Ereignis der
        Einrichtung B soft-loeschen (und dessen Spur unter A schreiben)."""
        assert foreign_event.facility_id == second_facility.pk
        dr = DeletionRequest.objects.create(
            facility=facility,
            target_type="Event",
            target_id=foreign_event.pk,
            reason="Einrichtungsfremdes Ziel",
            requested_by=staff_user,
        )

        with pytest.raises(Event.DoesNotExist):
            approve_deletion(dr, lead_user)

        foreign_event.refresh_from_db()
        assert foreign_event.is_deleted is False
        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.PENDING


class TestRejectDeletionLookupZieltreue:
    def test_reject_stempelt_genau_den_uebergebenen_antrag(
        self, facility, client_qualified, doc_type_contact, staff_user, lead_user
    ):
        """Pins ``x_reject_deletion__mutmut_4``: ohne ``pk`` im Sperr-Lookup
        wird irgendein Antrag der Einrichtung auf REJECTED gestempelt — genau
        der Zustands-Ueberschreiber, den Sperre und Re-Check laut Docstring
        verhindern sollen."""
        event_a = _event(facility, client_qualified, doc_type_contact, staff_user, note="Antrag A")
        event_b = _event(facility, client_qualified, doc_type_contact, staff_user, note="Antrag B")
        dr_a = request_deletion(event_a, staff_user, "Bleibt offen")
        dr_b = request_deletion(event_b, staff_user, "Wird abgelehnt")
        assert dr_a.pk != dr_b.pk

        reject_deletion(dr_b, lead_user)

        dr_b.refresh_from_db()
        assert dr_b.status == DeletionRequest.Status.REJECTED
        assert dr_b.reviewed_by_id == lead_user.pk

        dr_a.refresh_from_db()
        assert dr_a.status == DeletionRequest.Status.PENDING
        assert dr_a.reviewed_by_id is None
        assert dr_a.reviewed_at is None

    def test_reject_lookup_bleibt_an_die_antrags_einrichtung_gebunden(
        self, facility, other_facility, client_qualified, doc_type_contact, staff_user, lead_user
    ):
        """Pins ``x_reject_deletion__mutmut_5``: faellt die facility-Scopung
        weg, laesst sich ein Antrag sperren und ablehnen, dessen Einrichtung
        nicht die des uebergebenen Objekts ist."""
        event = _event(facility, client_qualified, doc_type_contact, staff_user, note="Mandantengrenze")
        dr = request_deletion(event, staff_user, "Art.-17-Antrag")
        dr.facility = other_facility

        with pytest.raises(DeletionRequest.DoesNotExist):
            reject_deletion(dr, lead_user)

        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.PENDING
        assert dr.reviewed_by_id is None

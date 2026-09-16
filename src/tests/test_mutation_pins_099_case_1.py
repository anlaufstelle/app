"""Pin-Tests der Welle L (0.99, Refs #2174 / #2193) — ``core.services.case.cases``.

Quelle: Vollmutation #2174, Batch ``case-1``
(``welle-l-0.99/reports/m-scratch/batch-case-1.json``, Triage
``welle-l-0.99/reports/m-scratch/pins-case-1/triage-099-case-1.json``). Die Batch hat 23
Survivor-Mutanten in ``create_case`` (12) und ``assign_event_to_case`` (11) — alle bis auf
einen sind Meldungs-Verlust-/Text-Varianten (Klasse ``test``, Prio 3) an sechs
``raise``-Stellen (``ValidationError``/``ValueError``): je EIN Pin pro Stelle mit exakter
Assertion auf ``exc.value.messages``/``exc.value.args`` tötet dort zugleich die
begleitenden XX-Marker-/Groß-Klein-Varianten desselben Wortlauts (am installierten Django
verifiziert: ``ValidationError(None).messages == ["None"]``;
``ValidationError(_(None))``/``ValueError(_(None))`` werfen beim Auslesen der
Meldung ``AttributeError`` — beides zählt als „rot").

``x_create_case__mutmut_51`` (Verlust von ``status=Case.Status.OPEN`` im
``Case()``-Konstruktor) ist **equivalent**: das Modellfeld trägt
``default=Status.OPEN`` (``core/models/case.py``), daher liefert die Instanziierung mit
oder ohne den expliziten Kwarg denselben Wert. Der bestehende Test
``test_cases.py::TestCaseService.test_create_case`` prüft ``case.status == Case.Status.OPEN``
bereits und tötet die Mutante trotzdem nicht — genau das bestätigt die Äquivalenz empirisch
(kein neuer Pin nötig).
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.models import Client, DocumentType, Event, User
from core.services.case import assign_event_to_case, create_case

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


class TestCreateCaseMessages:
    """Exakte Meldungstexte an den drei ``raise``-Stellen von ``create_case``."""

    def test_client_none_meldet_exakten_text(self, facility, staff_user):
        """Pins ``x_create_case__mutmut_3..7``: fehlt ``client``, muss exakt
        "Fälle müssen einer Person zugeordnet sein." gemeldet werden. mutmut_3 liefert
        ``ValidationError(None)`` (Meldungstext wörtlich "None"), mutmut_4 die kaputte
        ``gettext_lazy``-Hülle ``_(None)`` (``AttributeError`` beim Auslesen von
        ``.messages``), mutmut_5/6/7 sind XX-Marker-/Groß-/Kleinschreibungsvarianten
        desselben Wortlauts."""
        with pytest.raises(ValidationError) as exc:
            create_case(facility=facility, user=staff_user, client=None, title="Ohne Person")
        assert exc.value.messages == ["Fälle müssen einer Person zugeordnet sein."]

    def test_client_falsche_facility_meldet_exakten_text(self, facility, staff_user, other_facility):
        """Pins ``x_create_case__mutmut_11`` (XX-Marker-Variante von "Person gehört
        nicht zur Einrichtung." — die None-/``_(None)``-Varianten an derselben Stelle
        waren vor dieser Welle bereits gepinnt, nur der reine Text-Marker überlebte)."""
        foreign_client = Client.objects.create(
            facility=other_facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym="Fremd-Client-01",
            created_by=staff_user,
        )
        with pytest.raises(ValueError) as exc:
            create_case(facility=facility, user=staff_user, client=foreign_client, title="Fremde Person")
        assert exc.value.args == ("Person gehört nicht zur Einrichtung.",)

    def test_lead_user_falsche_facility_meldet_exakten_text(
        self, facility, staff_user, client_identified, other_facility
    ):
        """Pins ``x_create_case__mutmut_21..25`` (dieselben fünf Varianten wie oben, an
        "Fallverantwortlicher gehört nicht zur Einrichtung.")."""
        foreign_lead = User.objects.create_user(
            username="fremde-leitung-case1",
            role=User.Role.LEAD,
            facility=other_facility,
            is_staff=True,
        )
        with pytest.raises(ValueError) as exc:
            create_case(
                facility=facility,
                user=staff_user,
                client=client_identified,
                title="Mit fremder Leitung",
                lead_user=foreign_lead,
            )
        assert exc.value.args == ("Fallverantwortlicher gehört nicht zur Einrichtung.",)


class TestAssignEventToCaseMessages:
    """Exakte Meldungstexte an den drei ``raise``-Stellen von ``assign_event_to_case``."""

    def test_fremde_facility_meldet_exakten_text(self, facility, staff_user, case_open, other_facility):
        """Pins ``x_assign_event_to_case__mutmut_14`` (XX-Marker-Variante von "Ereignis
        gehört nicht zur selben Einrichtung wie der Fall." — der bestehende
        ``test_cases.py::test_assign_event_validates_facility`` prüft nur die
        Teilzeichenkette ``match="Einrichtung"``, die in dieser Variante unverändert
        weiter vorkommt und sie deshalb nicht tötet)."""
        other_doc = DocumentType.objects.create(facility=other_facility, name="Kontakt-Fremd-Case1")
        other_event = Event.objects.create(
            facility=other_facility,
            document_type=other_doc,
            occurred_at=timezone.now(),
            data_json={},
        )
        with pytest.raises(ValueError) as exc:
            assign_event_to_case(case_open, other_event, staff_user)
        assert exc.value.args == ("Ereignis gehört nicht zur selben Einrichtung wie der Fall.",)

    def test_fuer_rolle_nicht_sichtbar_meldet_exakten_text(
        self, facility, case_open, doc_type_crisis, client_identified, assistant_user
    ):
        """Pins ``x_assign_event_to_case__mutmut_24..28`` an "Ereignis ist für diese
        Rolle nicht sichtbar.": ein Krisenereignis (Sensitivity ELEVATED) ist für eine
        Assistenzkraft (Rollenmaximum NORMAL) nicht sichtbar."""
        doc_type_crisis.system_type = "crisis"
        doc_type_crisis.save()
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_crisis,
            occurred_at=timezone.now(),
            data_json={},
        )
        with pytest.raises(ValidationError) as exc:
            assign_event_to_case(case_open, event, assistant_user)
        assert exc.value.messages == ["Ereignis ist für diese Rolle nicht sichtbar."]

    def test_person_passt_nicht_meldet_exakten_text(
        self, facility, staff_user, case_open, client_qualified, doc_type_contact
    ):
        """Pins ``x_assign_event_to_case__mutmut_38..42`` an "Person des Ereignisses
        passt nicht zur Person des Falls.": ``case_open`` gehört zu ``client_identified``,
        das Ereignis zu einer anderen Person (``client_qualified``) derselben
        Einrichtung."""
        other_event = Event.objects.create(
            facility=facility,
            client=client_qualified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        with pytest.raises(ValidationError) as exc:
            assign_event_to_case(case_open, other_event, staff_user)
        assert exc.value.messages == ["Person des Ereignisses passt nicht zur Person des Falls."]

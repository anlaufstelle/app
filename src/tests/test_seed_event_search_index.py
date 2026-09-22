"""Regression fuer #2271: ``Event.search_text`` muss auch fuer per
``bulk_create`` (Seed) angelegte Ereignisse befuellt sein.

Der Bulk-Pfad (``core.seed.events.seed_events_bulk``) setzte ``search_text``
bislang nicht — das ``pre_save``-Signal ``core.signals.event_search`` feuert
bei ``Event.objects.bulk_create`` nicht (Django-Doku: Signale rund um
``save()`` werden von ``bulk_create`` NICHT ausgeloest). Ein geseedetes
Ereignis mit einem Begriff ausschliesslich in einem zulaessigen Textfeld war
damit ueber die reguläre Suche (``core.services.dashboard.search_clients_and_events``)
nicht auffindbar, obwohl das Model-Feld als „Suchindex“ dokumentiert ist.
"""

import random

import pytest

from core.models import Client, DocumentType, DocumentTypeField, Event, FieldTemplate
from core.seed.events import seed_events_bulk
from core.seed.users import seed_users
from core.services.dashboard import search_clients_and_events

CFG = {"events_per_facility": 40, "zeitraum_days": 60}


@pytest.fixture
def doc_type_searchable(facility):
    """Eigener Event-Dokumentationstyp mit EINEM unverschluesselten Textarea-
    Feld ohne Namensraum-Pool (Refs #2148) — ``random_data`` faellt damit
    zwingend auf den generischen Fallback-Generator zurueck
    (``f"Seed-Notiz {random.randint(1, 9999)}"``), dessen Wert nach dem Fix
    im Suchindex landen muss."""
    doc_type = DocumentType.objects.create(
        facility=facility,
        entity_type=DocumentType.EntityType.EVENT,
        name="Suchindex-Testtyp",
    )
    ft_notiz = FieldTemplate.objects.create(
        facility=facility,
        name="Freitext",
        field_type=FieldTemplate.FieldType.TEXTAREA,
        # sensitivity="" (Default) -> erbt NORMAL vom DocumentType, zaehlt
        # laut compute_event_search_text als indexierbar.
    )
    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_notiz, sort_order=0)
    return doc_type, ft_notiz


@pytest.fixture
def doc_type_with_high_sensitivity_field(facility):
    """Zweiter Typ mit einem NICHT verschluesselten, aber ERHOEHT-sensiblen
    Textfeld — negative Regression: dieser Wert darf trotz Generierung
    NICHT im Suchindex landen (``compute_event_search_text`` schliesst
    ELEVATED/HIGH ueber die ``FieldTemplate``-Metadaten aus).

    ``sensitivity=HIGH`` waere hier UNGEEIGNET: ``FieldTemplate.save()``
    erzwingt bei HIGH ``is_encrypted=True`` als Backstop (Refs #1270 T5,
    ``models/field_template.py``) — der Fall waere dann ununterscheidbar von
    der bereits separat getesteten Verschluesselungs-Ausschlussregel. ELEVATED
    erzwingt das NICHT und pruewft damit gezielt den zweiten,
    unabhaengigen Ausschlusszweig (``sensitivity_rank(...) != 0``)."""
    doc_type = DocumentType.objects.create(
        facility=facility,
        entity_type=DocumentType.EntityType.EVENT,
        name="Suchindex-Testtyp-Sensibel",
    )
    ft_elevated = FieldTemplate.objects.create(
        facility=facility,
        name="Vertraulich",
        field_type=FieldTemplate.FieldType.TEXT,
        sensitivity=DocumentType.Sensitivity.ELEVATED,
    )
    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_elevated, sort_order=0)
    return doc_type, ft_elevated


@pytest.mark.django_db
class TestSeedEventSearchIndex:
    def test_bulk_created_event_is_found_via_regular_search(self, facility, doc_type_searchable):
        """Guard fuer #2271: ein NUR per Suchindex auffindbarer Begriff eines
        geseedeten Ereignisses muss ueber ``search_clients_and_events``
        gefunden werden."""
        doc_type, ft_notiz = doc_type_searchable
        users = seed_users(facility, 0)
        staff_user = users[0]
        client = Client.objects.create(
            facility=facility,
            pseudonym="Such-Test-01",
            contact_stage=Client.ContactStage.IDENTIFIED,
            created_by=staff_user,
        )

        random.seed(2271)
        created = seed_events_bulk(facility, users, [client], CFG, package="default")
        assert created == CFG["events_per_facility"]

        events = list(Event.objects.filter(facility=facility, document_type=doc_type))
        assert events, "Fixture-Doctype muss mindestens ein Ereignis erhalten haben (einziger Typ im Topf)"

        for event in events:
            # Direkter Modellwert (unabhaengig vom Suchpfad): search_text
            # darf nicht mehr leer sein und muss den generierten Freitext
            # enthalten.
            assert event.search_text, f"Ereignis {event.pk}: search_text ist leer"
            assert event.data_json[ft_notiz.slug] in event.search_text

            # Ueber den regulaeren Suchpfad: derselbe Begriff, isoliert aus
            # dem Modellwert genommen (kein Zufalls-Rateversuch), muss das
            # Ereignis liefern.
            term = event.data_json[ft_notiz.slug]
            _, found_events = search_clients_and_events(facility, staff_user, term)
            assert event in list(found_events), f"Ereignis {event.pk} nicht ueber Suche mit Begriff {term!r} gefunden"

    def test_high_sensitivity_field_is_excluded_from_index_and_search(
        self, facility, doc_type_with_high_sensitivity_field
    ):
        """Negative Regression: ein NICHT verschluesseltes, aber HOCH-
        sensibles Feld generiert weiterhin einen Wert (Seed umgeht
        ``build_data_template``s Encrypted-Filter hier nicht, weil das Feld
        gar nicht ``is_encrypted`` ist) — der Wert darf aber NICHT im
        Suchindex landen und darueber NICHT auffindbar sein."""
        users = seed_users(facility, 0)
        staff_user = users[0]
        client = Client.objects.create(
            facility=facility,
            pseudonym="Such-Test-02",
            contact_stage=Client.ContactStage.IDENTIFIED,
            created_by=staff_user,
        )

        random.seed(2271)
        seed_events_bulk(facility, users, [client], CFG, package="default")

        doc_type, ft_high = doc_type_with_high_sensitivity_field
        events = list(Event.objects.filter(facility=facility, document_type=doc_type))
        assert events, "Fixture-Doctype muss mindestens ein Ereignis erhalten haben (einziger Typ im Topf)"

        for event in events:
            value = event.data_json.get(ft_high.slug)
            assert value, f"Ereignis {event.pk}: HIGH-Feld wurde nicht generiert — Testaufbau falsch"
            assert value not in event.search_text, (
                f"Ereignis {event.pk}: HIGH-sensibler Wert {value!r} steckt im Suchindex"
            )
            _, found_events = search_clients_and_events(facility, staff_user, value)
            assert event not in list(found_events), (
                f"Ereignis {event.pk}: HIGH-sensibler Wert {value!r} ueber die reguläre Suche auffindbar"
            )

    def test_encrypted_field_never_generated_and_never_indexed(self, facility):
        """Ergaenzende Kontrollprobe: ein ``is_encrypted``-Feld wird vom Seed
        ueberhaupt nicht befuellt (``build_data_template`` schliesst es aus)
        — der Suchindex kann also gar keinen Klartext-Leak ueber DIESEN Pfad
        haben. Haelt die in #2271 beschriebene Ausschlussregel fest, ohne
        eine neue Zusicherung zu erfinden."""
        users = seed_users(facility, 0)
        staff_user = users[0]
        client = Client.objects.create(
            facility=facility,
            pseudonym="Such-Test-03",
            contact_stage=Client.ContactStage.IDENTIFIED,
            created_by=staff_user,
        )
        doc_type = DocumentType.objects.create(
            facility=facility,
            entity_type=DocumentType.EntityType.EVENT,
            name="Suchindex-Testtyp-Verschluesselt",
        )
        ft_encrypted = FieldTemplate.objects.create(
            facility=facility,
            name="Geheim",
            field_type=FieldTemplate.FieldType.TEXT,
            is_encrypted=True,
        )
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_encrypted, sort_order=0)

        random.seed(2271)
        seed_events_bulk(facility, users, [client], CFG, package="default")

        events = list(Event.objects.filter(facility=facility, document_type=doc_type))
        assert events
        for event in events:
            assert ft_encrypted.slug not in event.data_json
            assert event.search_text == ""

    def test_facility_and_role_filters_stay_effective(self, facility, doc_type_searchable):
        """Facility-Scope bleibt gewahrt: ein geseedetes Ereignis einer
        FREMDEN Einrichtung darf ueber die Suche der Testeinrichtung nicht
        auftauchen, auch wenn der Suchindex jetzt befuellt ist."""
        from core.models import Organization

        doc_type, _ft_notiz = doc_type_searchable
        users = seed_users(facility, 0)
        staff_user = users[0]
        client = Client.objects.create(
            facility=facility,
            pseudonym="Such-Test-04",
            contact_stage=Client.ContactStage.IDENTIFIED,
            created_by=staff_user,
        )

        other_org = Organization.objects.create(name="Fremdorg")
        from core.models import Facility as FacilityModel

        other_facility = FacilityModel.objects.create(organization=other_org, name="Fremdstelle")
        other_doc_type = DocumentType.objects.create(
            facility=other_facility,
            entity_type=DocumentType.EntityType.EVENT,
            name="Suchindex-Testtyp",
        )
        other_ft = FieldTemplate.objects.create(
            facility=other_facility,
            name="Freitext",
            field_type=FieldTemplate.FieldType.TEXTAREA,
        )
        DocumentTypeField.objects.create(document_type=other_doc_type, field_template=other_ft, sort_order=0)
        other_users = seed_users(other_facility, 1)
        other_client = Client.objects.create(
            facility=other_facility,
            pseudonym="Fremd-Client",
            contact_stage=Client.ContactStage.IDENTIFIED,
            created_by=other_users[0],
        )

        random.seed(2271)
        seed_events_bulk(facility, users, [client], CFG, package="default")
        random.seed(2271)
        seed_events_bulk(other_facility, other_users, [other_client], CFG, package="default")

        own_event = Event.objects.filter(facility=facility, document_type=doc_type).first()
        foreign_event = Event.objects.filter(facility=other_facility, document_type=other_doc_type).first()
        assert own_event is not None
        assert foreign_event is not None

        term = foreign_event.data_json[other_ft.slug]
        _, found_events = search_clients_and_events(facility, staff_user, term)
        assert foreign_event not in list(found_events), "fremde Einrichtung ueber die eigene Suche sichtbar"

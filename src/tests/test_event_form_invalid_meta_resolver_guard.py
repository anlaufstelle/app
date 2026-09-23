"""Ereignis-Formular: Personen-Vorbelegung bei ungültigem Meta-Formular (Refs #2365).

Wird das Anlegen-Formular nach einem ungültigen Meta-Formular erneut
angezeigt, darf die Personenzuordnung nur dann vorbelegt bleiben, wenn die
Person auch beim Speichern angenommen würde. Die übrigen Neu-Anzeige-Zweige
(ungültiges Datenformular, Ablehnung beim Speichern) nutzen dafür die bereits
geprüfte Person aus ``EventMetaForm.clean_client``; dieser Zweig muss
denselben Maßstab anlegen: Person der eigenen Einrichtung, weder im
Papierkorb noch anonymisiert noch in der Verarbeitung eingeschränkt.

Die Personen werden über die Fach-Services in den jeweiligen Zustand
gebracht, nicht per Direktschreibung.

Geprüft wird das gerenderte HTML: die Personenauswahl liest ihren
Startzustand ausschließlich aus ``data-initial-client-id`` und
``data-initial-pseudonym`` am ``<form>``.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from core.models import Client, DocumentType
from core.services.client import anonymize_client, approve_client_deletion, request_client_deletion
from core.services.client.restriction import restrict_processing


@pytest.fixture
def doc_type_plain(facility):
    return DocumentType.objects.create(facility=facility, name="Ohne Einschränkung")


@pytest.fixture
def person(facility, staff_user):
    return Client.objects.create(
        facility=facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
        pseudonym="Vorbelegt-01",
        created_by=staff_user,
    )


def _post_invalid_meta(client, client_pk, doc_type=None):
    """Meta-Formular absichtlich ungültig: Zeitpunkt fehlt."""
    return client.post(
        reverse("core:event_create"),
        {
            "document_type": str(doc_type.pk) if doc_type else "",
            "occurred_at": "",
            "client": str(client_pk),
        },
    )


def _assert_not_prefilled(response, person_pk, pseudonym):
    assert response.status_code == 200
    html = response.content.decode()
    assert 'data-initial-client-id=""' in html, "Widerrufene Person darf nicht vorbelegt werden"
    assert 'data-initial-pseudonym=""' in html
    assert str(person_pk) not in html
    assert response.context["client_id"] == ""
    assert response.context["client_pseudonym"] == ""
    if pseudonym:
        assert pseudonym not in html


@pytest.mark.django_db
class TestInvalidMetaFormPrefill:
    def test_valid_person_stays_prefilled(self, client, staff_user, person, doc_type_plain):
        client.force_login(staff_user)

        response = _post_invalid_meta(client, person.pk, doc_type_plain)

        assert response.status_code == 200
        html = response.content.decode()
        assert f'data-initial-client-id="{person.pk}"' in html
        assert f'data-initial-pseudonym="{person.pseudonym}"' in html
        assert response.context["client_pseudonym"] == person.pseudonym

    def test_anonymized_person_is_not_prefilled(self, client, staff_user, person, doc_type_plain):
        pseudonym = person.pseudonym
        anonymize_client(person, user=staff_user)
        person.refresh_from_db()
        assert person.is_active is False
        client.force_login(staff_user)

        response = _post_invalid_meta(client, person.pk, doc_type_plain)

        _assert_not_prefilled(response, person.pk, pseudonym)

    def test_trashed_person_is_not_prefilled(self, client, staff_user, lead_user, person, doc_type_plain):
        pseudonym = person.pseudonym
        deletion = request_client_deletion(person, staff_user, "Doppelt angelegt")
        approve_client_deletion(deletion, lead_user)
        person.refresh_from_db()
        assert person.is_deleted is True
        client.force_login(staff_user)

        response = _post_invalid_meta(client, person.pk, doc_type_plain)

        _assert_not_prefilled(response, person.pk, pseudonym)

    def test_restricted_person_is_not_prefilled(self, client, staff_user, person, doc_type_plain):
        restrict_processing(person, staff_user, reason=Client.RestrictionReason.ACCURACY_DISPUTED)
        client.force_login(staff_user)

        response = _post_invalid_meta(client, person.pk, doc_type_plain)

        _assert_not_prefilled(response, person.pk, person.pseudonym)

    def test_person_of_other_facility_is_not_prefilled(self, client, staff_user, other_facility):
        foreign = Client.objects.create(
            facility=other_facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym="Fremd-01",
        )
        client.force_login(staff_user)

        response = _post_invalid_meta(client, foreign.pk)

        _assert_not_prefilled(response, foreign.pk, foreign.pseudonym)

    @pytest.mark.parametrize("raw", ["kein-uuid", "00000000-0000-0000-0000-000000000000"])
    def test_unknown_or_malformed_id_is_not_prefilled(self, client, staff_user, raw):
        client.force_login(staff_user)

        response = _post_invalid_meta(client, raw)

        assert response.status_code == 200
        assert response.context["client_id"] == ""
        assert response.context["client_pseudonym"] == ""

    def test_without_person_renders_empty_selection(self, client, staff_user, doc_type_plain):
        client.force_login(staff_user)

        response = _post_invalid_meta(client, "", doc_type_plain)

        assert response.status_code == 200
        assert response.context["client_id"] == ""
        assert response.context["client_pseudonym"] == ""

    def test_raw_json_contract_stays_422(self, client, staff_user, person, doc_type_plain):
        anonymize_client(person, user=staff_user)
        client.force_login(staff_user)

        response = client.post(
            reverse("core:event_create"),
            {"document_type": str(doc_type_plain.pk), "occurred_at": "", "client": str(person.pk)},
            HTTP_ACCEPT="application/json",
        )

        assert response.status_code == 422

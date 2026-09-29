"""Loeschantrag stellen: eine Absage des Dienstes endet in einer Meldung, nicht im Serverfehler.

Die Oberflaeche liest die Person vor dem Aufruf des Dienstes (lebend, in der
eigenen Einrichtung). Der Dienst sperrt die Zeile danach und prueft ihren
Zustand erneut: wurde die Person im selben Moment zusammengefuehrt oder in den
Papierkorb gelegt, lehnt er mit einer ``ValidationError`` ab. Die Antwort ist
dann dieselbe Form wie bei den uebrigen Personen-Aktionen: Fehlermeldung und
Weiterleitung — hier auf die Personenliste, weil die Akte selbst nicht mehr
erreichbar ist. Es entsteht kein Antrag.

Den Wettlauf stellt der Test ueber den Vorab-Lookup der View nach: in dem
Moment, in dem die View nach einem laufenden Antrag fragt, aendert sich der
Zustand der Person in der Datenbank (Muster
``test_deletion_request_race_idempotency.py``).
"""

from unittest import mock

import pytest
from django.contrib.messages import constants, get_messages
from django.urls import reverse
from django.utils import timezone

from core.models import Client, DeletionRequest

pytestmark = pytest.mark.django_db


def _parallel(pk, **zustand):
    """Der Vorab-Lookup der View findet keinen Antrag — und die Person aendert sich im selben Moment."""

    def lookup(_client):
        Client.objects.filter(pk=pk).update(**zustand)
        return None

    return lookup


@pytest.fixture
def merge_target(facility, staff_user):
    return Client.objects.create(
        facility=facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
        pseudonym="Ziel-Parallel",
        created_by=staff_user,
    )


class TestAbsageDesDienstes:
    @pytest.mark.parametrize(
        ("zustand", "meldung"),
        [
            ("papierkorb", "Diese Person liegt bereits im Papierkorb."),
            ("zusammengefuehrt", "Diese Person ist bereits zusammengeführt."),
        ],
        ids=["papierkorb", "zusammengefuehrt"],
    )
    def test_meldung_und_weiterleitung_statt_serverfehler(
        self, client, staff_user, client_identified, merge_target, zustand, meldung
    ):
        if zustand == "papierkorb":
            aenderung = {"is_deleted": True, "deleted_at": timezone.now()}
        else:
            aenderung = {"merged_into": merge_target}
        client.force_login(staff_user)

        with mock.patch(
            "core.views.client_deletion.get_pending_deletion_request",
            side_effect=_parallel(client_identified.pk, **aenderung),
        ):
            response = client.post(
                reverse("core:client_delete_request", kwargs={"pk": client_identified.pk}),
                {"reason": "Parallel geaendert"},
            )

        assert response.status_code == 302
        assert response["Location"] == reverse("core:client_list")
        fehler = [m.message for m in get_messages(response.wsgi_request) if m.level == constants.ERROR]
        assert fehler == [meldung]
        assert not DeletionRequest.objects.filter(target_id=client_identified.pk).exists()

    def test_gegenprobe_antrag_wird_gestellt(self, client, staff_user, client_identified):
        client.force_login(staff_user)

        response = client.post(
            reverse("core:client_delete_request", kwargs={"pk": client_identified.pk}),
            {"reason": "Regulaer"},
        )

        assert response.status_code == 302
        assert response["Location"] == reverse("core:client_detail", kwargs={"pk": client_identified.pk})
        assert DeletionRequest.objects.filter(target_id=client_identified.pk).count() == 1

"""Refs #2354: Genehmigen und Ablehnen einer Berichtigung ziehen die Einrichtungsgrenze selbst.

``approve_rectification`` und ``reject_rectification`` teilen sich
``_guard_reviewable`` (Status, Selbstpruefung, Rolle). Der Antrag wird zwar
auf seine eigene Einrichtung gescopt nachgeladen, die Einrichtung des
Reviewers wurde aber nicht dagegen geprueft — dieselbe Luecke, die fuer die
uebrigen Vier-Augen-Services bereits geschlossen ist (Refs #2321, #2338).
Ueber HTTP unerreichbar (die View scopet), in Produktion von RLS getragen;
im Test ist RLS nicht aktiv, der Service ist also die einzige Grenze.

Je Service: eine Leitung aus fremder oder ohne Einrichtung wird mit
derselben Meldung wie in den anderen Vier-Augen-Services abgewiesen; der
Antrag bleibt offen, die Person unveraendert, es entsteht keine Audit-Zeile.
Die Einrichtungsgrenze steht vor den uebrigen Pruefungen — wie dort direkt
nach der Sperre des Antrags. Der Gegen-Check mit der eigenen Leitung geht durch.
"""

import pytest
from django.core.exceptions import ValidationError

from core.models import AuditLog, Client, RectificationRequest, User
from core.services.client.rectification import (
    approve_rectification,
    reject_rectification,
    request_rectification,
)

pytestmark = pytest.mark.django_db

_MELDUNG = "Antrag gehört zu einer anderen Einrichtung."


@pytest.fixture
def foreign_lead(second_facility):
    return User.objects.create_user(
        username="fremdleitung-berichtigung",
        role=User.Role.LEAD,
        facility=second_facility,
        is_staff=True,
    )


@pytest.fixture
def facilityless_lead():
    return User.objects.create_user(
        username="ohneeinrichtung-berichtigung",
        role=User.Role.LEAD,
        facility=None,
        is_staff=True,
    )


@pytest.fixture
def antrag(client_identified, staff_user):
    return request_rectification(client_identified, staff_user, "notes", "Neuer Wert", "Grund")


def _assert_refused(callable_, rr, client_pk):
    notes_vorher = Client.objects.get(pk=client_pk).notes
    audit_vorher = AuditLog.objects.count()
    with pytest.raises(ValidationError) as excinfo:
        callable_()
    assert excinfo.value.messages == [_MELDUNG]
    rr.refresh_from_db()
    assert rr.status == RectificationRequest.Status.PENDING
    assert rr.reviewed_by_id is None
    assert rr.reviewed_at is None
    assert Client.objects.get(pk=client_pk).notes == notes_vorher
    assert AuditLog.objects.count() == audit_vorher


class TestForeignReviewerIsRefused:
    @pytest.mark.parametrize("reviewer_fixture", ["foreign_lead", "facilityless_lead"])
    def test_approve(self, request, antrag, client_identified, reviewer_fixture):
        reviewer = request.getfixturevalue(reviewer_fixture)
        _assert_refused(lambda: approve_rectification(antrag, reviewer), antrag, client_identified.pk)

    @pytest.mark.parametrize("reviewer_fixture", ["foreign_lead", "facilityless_lead"])
    def test_reject(self, request, antrag, client_identified, reviewer_fixture):
        reviewer = request.getfixturevalue(reviewer_fixture)
        _assert_refused(lambda: reject_rectification(antrag, reviewer, "Vermerk"), antrag, client_identified.pk)

    def test_grenze_vor_statuspruefung(self, antrag, lead_user, foreign_lead):
        """Die Grenze sagt einer fremden Leitung nicht, ob der Antrag schon entschieden ist."""
        reject_rectification(antrag, lead_user, "Vermerk")
        with pytest.raises(ValidationError) as excinfo:
            approve_rectification(antrag, foreign_lead)
        assert excinfo.value.messages == [_MELDUNG]


class TestOwnFacilityReviewerStillPasses:
    def test_approve_passes(self, antrag, client_identified, lead_user):
        approve_rectification(antrag, lead_user)
        antrag.refresh_from_db()
        assert antrag.status == RectificationRequest.Status.APPROVED
        assert Client.objects.get(pk=client_identified.pk).notes == "Neuer Wert"

    def test_reject_passes(self, antrag, lead_user):
        reject_rectification(antrag, lead_user, "Vermerk")
        antrag.refresh_from_db()
        assert antrag.status == RectificationRequest.Status.REJECTED

    def test_meldungen_der_normalpfade_unveraendert(self, client_identified, antrag, staff_user, lead_user):
        with pytest.raises(ValidationError) as excinfo:
            approve_rectification(antrag, staff_user)
        assert excinfo.value.messages == [
            "Sie können Ihren eigenen Berichtigungsantrag nicht prüfen (Vier-Augen-Prinzip)."
        ]
        reject_rectification(antrag, lead_user, "Vermerk")
        with pytest.raises(ValidationError) as excinfo:
            approve_rectification(antrag, lead_user)
        assert excinfo.value.messages == ["Dieser Berichtigungsantrag ist bereits abgeschlossen."]

"""Pin-Tests zum Meilenstein release-0.97 — Prio-1-Survivors in ``core.services.client.rectification``.

Quelle: atomic-Messlauf der Welle J (https://github.com/anlaufstelle/app/issues/2149),
Batch ``client``. Gepinnt werden 21 Prio-1-Survivors der Funktionen
:func:`~core.services.client.rectification.request_rectification`,
:func:`~core.services.client.rectification.approve_rectification`,
:func:`~core.services.client.rectification.reject_rectification` und
:func:`~core.services.client.rectification._apply_rectification`. Drei Themen:

* **Zieltreue der Reloads unter der Zeilensperre.** Genehmigung und Ablehnung
  laden Antrag und Person innerhalb der Transaktion per ``select_for_update()``
  neu — jeweils ``pk`` UND ``facility``. Faellt der ``pk``-Filter weg, sperrt und
  entscheidet der Service ueber einen BELIEBIGEN Antrag bzw. eine beliebige
  Person derselben Einrichtung; faellt der ``facility``-Filter weg, laeuft der
  Reload ueber die Mandantengrenze hinweg. Alle Bestandstests arbeiten mit genau
  EINEM Antrag und EINER Person je Einrichtung — deshalb ueberleben die Mutanten.
* **Die handelnde Person im Anwendungspfad.** ``_apply_rectification`` reicht den
  Reviewer an ``update_client`` durch; nur dadurch traegt die angewendete
  Korrektur im ``CLIENT_UPDATE``-Audit die pruefende Person. ``update_client``
  akzeptiert ``user=None`` klaglos (der Rollen-Guard der Herabstufung kommt im
  Berichtigungspfad nie zum Zug, weil ``contact_stage`` nicht berichtigungsfaehig
  ist) — die Zurechnung der Vier-Augen-Entscheidung faellt also lautlos weg.
* **Die Rechenschaftsspur der drei Audit-Zeilen.** ``AuditLog.user``/``facility``
  sind ``null=True`` und ``audit_event`` laesst beides ausdruecklich zu
  (``core/services/audit/helpers.py``); ``detail`` faellt ueber
  ``validate_audit_detail`` still auf ``{}``. Antrag, Genehmigung und Ablehnung
  entstehen also auch ohne Akteurin, ohne Einrichtung und ohne Feld-/Personenbezug
  — sichtbar nur, wenn der Test die Werte exakt vergleicht statt ``exists()``
  zu pruefen.

Konventionen wie ``test_mutation_pins_096_merge.py``: exakte Vergleiche (``==``)
statt Teilstrings, Fixtures aus ``src/tests/conftest.py``, kein Nachbau von
Produktionslogik. Jeder Test nennt in seiner Docstring die gepinnten Mutanten.
"""

import pytest
from django.core.exceptions import ValidationError

from core.models import AuditLog, Client, RectificationRequest
from core.services.client.rectification import (
    approve_rectification,
    reject_rectification,
    request_rectification,
)

# ``_encryption_key``: der Antrag verschluesselt Wunsch-, Alt- und Freitextwerte
# (``encrypt_field``). Der Schluessel kommt im Repo aus ``.env`` — im
# ``mutants/``-Tree gibt es die Datei nicht. Die Fixture macht die Pins vom
# Umgebungszustand unabhaengig (Muster ``test_mutation_pins_096_events_crud.py``).
pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def _second_client(facility, staff_user):
    """Zweite Person DERSELBEN Einrichtung — der Unterschied, den ein Reload
    ohne ``pk``-Filter nicht mehr sieht."""
    return Client.objects.create(
        facility=facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
        pseudonym="Pin097-Rect-Zweite",
        created_by=staff_user,
    )


def _foreign_client(second_facility, second_facility_user):
    return Client.objects.create(
        facility=second_facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
        pseudonym="Pin097-Rect-Fremd",
        created_by=second_facility_user,
    )


def _request_with_foreign_target(facility, second_facility, second_facility_user):
    """Antrag der Einrichtung A auf eine Person der Einrichtung B.

    Der Antrag wird regulaer in B gestellt und anschliessend per Queryset-Update
    auf A umgehaengt: ``target_client`` ist ein FK OHNE Einrichtungsbindung im
    Modell, der ``facility``-Filter des Personen-Reloads ist damit die einzige
    Mandantengrenze dieses Pfads. Sie wird hier von aussen auf die Probe
    gestellt, ohne Produktionslogik nachzubauen.
    """
    foreign = _foreign_client(second_facility, second_facility_user)
    rr = request_rectification(foreign, second_facility_user, "notes", "Fremd berichtigt", "Grund")
    RectificationRequest.objects.filter(pk=rr.pk).update(facility=facility)
    return RectificationRequest.objects.get(pk=rr.pk), foreign


def _audit(action, target_id):
    return AuditLog.objects.get(action=action, target_id=str(target_id))


class TestApproveZieltreue:
    def test_genehmigung_laedt_genau_diesen_antrag(self, facility, client_identified, staff_user, lead_user):
        """Pins ``x_approve_rectification__mutmut_5``: ohne ``pk`` im
        ``select_for_update``-Reload sperrt und genehmigt der Service einen
        beliebigen Antrag DERSELBEN Einrichtung statt des uebergebenen."""
        other = _second_client(facility, staff_user)
        rr_a = request_rectification(client_identified, staff_user, "notes", "Text A", "Grund A")
        rr_b = request_rectification(other, staff_user, "notes", "Text B", "Grund B")

        approve_rectification(rr_a, lead_user)

        rr_a.refresh_from_db()
        rr_b.refresh_from_db()
        assert rr_a.status == RectificationRequest.Status.APPROVED
        assert rr_b.status == RectificationRequest.Status.PENDING
        client_identified.refresh_from_db()
        other.refresh_from_db()
        assert client_identified.notes == "Text A"
        assert other.notes == ""

    def test_antrags_reload_ist_an_die_einrichtung_des_antrags_gebunden(
        self, client_identified, second_facility, staff_user, lead_user
    ):
        """Pins ``x_approve_rectification__mutmut_6``: der ``facility``-Filter des
        Antrags-Reloads ist die Einrichtungsgrenze des Service selbst. Faellt er
        weg, laedt und genehmigt der Service allein anhand der PK — also auch
        einen Antrag, der nicht zur Einrichtung des uebergebenen Vorgangs
        gehoert, und die Grenze haengt nur noch an der DB-seitigen RLS."""
        rr = request_rectification(client_identified, staff_user, "notes", "Neuer Text", "Grund")
        mismatched = RectificationRequest.objects.get(pk=rr.pk)
        mismatched.facility_id = second_facility.pk

        with pytest.raises(RectificationRequest.DoesNotExist):
            approve_rectification(mismatched, lead_user)

        rr.refresh_from_db()
        assert rr.status == RectificationRequest.Status.PENDING
        client_identified.refresh_from_db()
        assert client_identified.notes == ""

    def test_personen_reload_ist_an_die_einrichtung_des_antrags_gebunden(
        self, facility, second_facility, second_facility_user, lead_user
    ):
        """Pins ``x_approve_rectification__mutmut_15``: ohne ``facility`` im
        Personen-Reload schreibt die Genehmigung in die Akte einer Person
        AUSSERHALB der Einrichtung des Antrags."""
        rr, foreign = _request_with_foreign_target(facility, second_facility, second_facility_user)

        with pytest.raises(Client.DoesNotExist):
            approve_rectification(rr, lead_user)

        rr.refresh_from_db()
        assert rr.status == RectificationRequest.Status.PENDING
        foreign.refresh_from_db()
        assert foreign.notes == ""


class TestApproveRechenschaft:
    def test_genehmigung_schreibt_client_update_mit_pruefender_person(
        self, facility, client_identified, staff_user, lead_user
    ):
        """Pins ``x__apply_rectification__mutmut_13`` und
        ``x_approve_rectification__mutmut_46``: beide reichen ``None`` statt des
        Reviewers an ``update_client`` durch. Die angewendete Korrektur entstuende
        dann ohne handelnde Person — die Vier-Augen-Genehmigung waere im
        ``CLIENT_UPDATE``-Audit nicht mehr belegt."""
        rr = request_rectification(client_identified, staff_user, "notes", "Berichtigt", "Grund")
        approve_rectification(rr, lead_user)

        entry = _audit(AuditLog.Action.CLIENT_UPDATE, client_identified.pk)
        assert entry.user_id == lead_user.pk
        assert entry.facility_id == facility.pk
        assert entry.target_type == "Client"
        client_identified.refresh_from_db()
        assert client_identified.notes == "Berichtigt"

    def test_genehmigungs_audit_traegt_person_einrichtung_und_bezug(
        self, facility, client_identified, staff_user, lead_user
    ):
        """Pins ``x_approve_rectification__mutmut_68`` (``user=None``),
        ``…__mutmut_69`` (``facility=None``), ``…__mutmut_72`` und
        ``…__mutmut_78`` (``detail`` faellt weg) sowie ``…__mutmut_87``
        (``target_client='None'``). ``audit_event`` laesst alle vier Faelle
        klaglos zu — der Nachweis der Genehmigung waere ohne Akteurin, ohne
        Einrichtung bzw. ohne Feld- und Personenbezug."""
        rr = request_rectification(client_identified, staff_user, "notes", "Berichtigt", "Grund")
        approve_rectification(rr, lead_user)

        entry = _audit(AuditLog.Action.RECTIFICATION_APPROVED, rr.pk)
        assert entry.user_id == lead_user.pk
        assert entry.facility_id == facility.pk
        assert entry.detail == {"field_path": "notes", "target_client": str(client_identified.pk)}


class TestRejectZieltreue:
    def test_ablehnung_laedt_genau_diesen_antrag(self, facility, client_identified, staff_user, lead_user):
        """Pins ``x_reject_rectification__mutmut_4``: ohne ``pk`` im
        ``select_for_update``-Reload lehnt der Service einen beliebigen Antrag
        derselben Einrichtung ab — mit endgueltiger Statusaenderung."""
        other = _second_client(facility, staff_user)
        rr_a = request_rectification(client_identified, staff_user, "notes", "Text A", "Grund A")
        rr_b = request_rectification(other, staff_user, "notes", "Text B", "Grund B")

        reject_rectification(rr_a, lead_user, "Nicht nachvollziehbar")

        rr_a.refresh_from_db()
        rr_b.refresh_from_db()
        assert rr_a.status == RectificationRequest.Status.REJECTED
        assert rr_b.status == RectificationRequest.Status.PENDING
        assert rr_b.reviewed_by_id is None

    def test_ablehnungs_reload_ist_an_die_einrichtung_des_antrags_gebunden(
        self, client_identified, second_facility, staff_user, lead_user
    ):
        """Pins ``x_reject_rectification__mutmut_5``: ohne ``facility`` im
        Antrags-Reload wird ein Antrag einer FREMDEN Einrichtung geladen,
        abgelehnt und mit einem verschluesselten Pruefvermerk beschrieben."""
        rr = request_rectification(client_identified, staff_user, "notes", "Neuer Text", "Grund")
        mismatched = RectificationRequest.objects.get(pk=rr.pk)
        mismatched.facility_id = second_facility.pk

        with pytest.raises(RectificationRequest.DoesNotExist):
            reject_rectification(mismatched, lead_user, "Nicht nachvollziehbar")

        rr.refresh_from_db()
        assert rr.status == RectificationRequest.Status.PENDING
        assert rr.review_note is None

    def test_ablehnung_prueft_den_widerruf_der_richtigen_person(
        self, facility, client_identified, staff_user, lead_user
    ):
        """Pins ``x_reject_rectification__mutmut_13``: ohne ``pk`` im
        Personen-Reload haengt der Widerruf-Guard an einer FREMDEN Person. Eine
        Ablehnung schriebe dann einen frischen verschluesselten Pruefvermerk auf
        eine bereits redigierte Zeile."""
        _second_client(facility, staff_user)
        rr = request_rectification(client_identified, staff_user, "notes", "Neuer Text", "Grund")
        client_identified.is_deleted = True
        client_identified.save(update_fields=["is_deleted"])

        with pytest.raises(ValidationError):
            reject_rectification(rr, lead_user, "Nicht nachvollziehbar")

        rr.refresh_from_db()
        assert rr.status == RectificationRequest.Status.PENDING
        assert rr.review_note is None

    def test_personen_reload_der_ablehnung_ist_an_die_einrichtung_gebunden(
        self, facility, second_facility, second_facility_user, lead_user
    ):
        """Pins ``x_reject_rectification__mutmut_14``: ohne ``facility`` im
        Personen-Reload sperrt die Ablehnung eine Person AUSSERHALB der
        Einrichtung des Antrags."""
        rr, foreign = _request_with_foreign_target(facility, second_facility, second_facility_user)

        with pytest.raises(Client.DoesNotExist):
            reject_rectification(rr, lead_user, "Nicht nachvollziehbar")

        rr.refresh_from_db()
        assert rr.status == RectificationRequest.Status.PENDING


class TestRejectUndRequestRechenschaft:
    def test_ablehnungs_audit_traegt_person_einrichtung_und_bezug(
        self, facility, client_identified, staff_user, lead_user
    ):
        """Pins ``x_reject_rectification__mutmut_36`` (``user=None``),
        ``…__mutmut_37`` (``facility=None``), ``…__mutmut_40`` und
        ``…__mutmut_46`` (``detail`` faellt weg) sowie ``…__mutmut_55``
        (``target_client='None'``). Gerade die Ablehnung dokumentiert die
        fachliche Abwaegung (ADR-013/041) — ohne Akteurin, Einrichtung und
        Bezug ist sie inhaltsleer."""
        rr = request_rectification(client_identified, staff_user, "notes", "Neuer Text", "Grund")
        reject_rectification(rr, lead_user, "Nicht nachvollziehbar")

        entry = _audit(AuditLog.Action.RECTIFICATION_REJECTED, rr.pk)
        assert entry.user_id == lead_user.pk
        assert entry.facility_id == facility.pk
        assert entry.detail == {"field_path": "notes", "target_client": str(client_identified.pk)}

    def test_antrags_audit_traegt_antragstellende_person_und_bezug(self, facility, client_identified, staff_user):
        """Pins ``x_request_rectification__mutmut_41`` (``user=None``) und
        ``…__mutmut_60`` (``target_client='None'``). Da es laut ADR-013 keinen
        Klientel-Self-Service gibt, ist genau diese Zurechnung der Nachweis, wer
        die Korrektur angestossen hat — und welche Person sie betrifft (die
        Werte selbst liegen bewusst nur verschluesselt am Antrag)."""
        rr = request_rectification(client_identified, staff_user, "notes", "Neuer Text", "Grund")

        entry = _audit(AuditLog.Action.RECTIFICATION_REQUESTED, rr.pk)
        assert entry.user_id == staff_user.pk
        assert entry.facility_id == facility.pk
        assert entry.detail == {"field_path": "notes", "target_client": str(client_identified.pk)}

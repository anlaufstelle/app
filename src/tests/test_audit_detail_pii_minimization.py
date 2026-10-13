"""Write-Time-Minimierung von Klienten-PII im append-only AuditLog (Refs #1751, Punkt 5).

Das AuditLog ist append-only (Trigger ``auditlog_immutable``, Migration 0024)
und per HMAC verkettet (Refs #1070) — eine nachtraegliche Redaktion ist dort
strukturell unmoeglich. **Vorab-Minimierung beim Schreiben ist deshalb der
einzige Hebel**: was nie ins ``detail`` geschrieben wird, ueberlebt auch keine
Anonymisierung.

Diese Suite nagelt die drei Schreibstellen fest, die Klienten-PII im Klartext
fuehrten. Bestehende Alt-Zeilen bleiben by design unangetastet (Hash-Kette).

Bewusst NICHT umgestellt (und daher hier auch nicht gepinnt): der ``username``
bei ``LOGIN_FAILED`` und bei Rollenwechsel/Deaktivierung/Loeschbestaetigung.
Ersterer ist der einzige Nachweis, *was* versucht wurde (bei Angriffen
existiert der User gar nicht, ``user`` ist dann NULL) und in
``tests/_residue_expectations.py`` als bewusste Sicherheits-Forensik
dokumentiert; letzterer ist wegen ``AuditLog.user = SET_NULL`` keine
target_id-Dublette, sondern die einzige dauerhafte Zuschreibung einer
Rechte-Eskalation (dieselbe Begruendung wie fuer ``actor_username``,
Refs #1369). Beides sind Beschaeftigten-, keine Klientendaten.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from core.models import AuditLog
from core.services.audit.hash import hmac_hash_value
from core.services.case.episodes import episode_basis_token

pytestmark = pytest.mark.django_db


class _OwnerStub:
    """Stand-in fuer den Pre-Save-Reject.

    ``log_attachment_violation`` liest vom Owner nur ``pk`` und — seit der
    Owner-Generik (AP2, Refs #1738) — ``_meta.model_name`` fuer das
    ``owner_type`` im Detail-Schema.
    """

    class _Meta:
        model_name = "event"

    _meta = _Meta()
    pk = None


class TestAttachmentViolationFilenameMinimized:
    """Der Original-Dateiname eines abgewiesenen Uploads darf nicht im Klartext
    ins ``detail``.

    Besonders scharf, weil ``SECURITY_VIOLATION`` in ``PRUNE_EXEMPT_ACTIONS``
    steht: diese Zeilen werden **nie** gepruned, auch nicht nach
    ``auditlog_retention_months``. Ein Dateiname wie
    „Bericht-Max-Mustermann.pdf" bliebe damit fuer immer stehen.

    Forensik-Erhalt: der HMAC korreliert Wiederholungsversuche derselben Datei,
    die Endung bleibt als Typmarker lesbar (der Kern-Befund bei
    ``extension_not_allowed``).
    """

    def test_violation_detail_carries_hash_and_extension_not_the_name(self, facility, staff_user):
        from core.services.file_vault.audit import log_attachment_violation

        entry = log_attachment_violation(
            facility,
            staff_user,
            _OwnerStub(),
            reason="extension_not_allowed",
            filename="Bericht-Klartext-Person.exe",
        )
        entry.refresh_from_db()

        assert "filename" not in entry.detail, "Klartext-Dateiname darf nicht im append-only AuditLog landen"
        assert entry.detail["filename_hash"] == hmac_hash_value("Bericht-Klartext-Person.exe")
        assert entry.detail["filename_ext"] == "exe"
        assert entry.detail["reason"] == "extension_not_allowed"

    def test_hash_is_stable_and_distinguishes_files(self, facility, staff_user):
        """Forensik-Vertrag: gleicher Name -> gleicher Hash, anderer Name -> anderer."""
        from core.services.file_vault.audit import log_attachment_violation

        def _hash_for(name):
            return log_attachment_violation(
                facility, staff_user, _OwnerStub(), reason="virus_detected", filename=name
            ).detail["filename_hash"]

        first = _hash_for("a.pdf")
        again = _hash_for("a.pdf")
        other = _hash_for("b.pdf")

        assert first == again
        assert first != other

    def test_missing_extension_yields_empty_marker(self, facility, staff_user):
        from core.services.file_vault.audit import log_attachment_violation

        entry = log_attachment_violation(
            facility, staff_user, _OwnerStub(), reason="mime_mismatch", filename="ohne-endung"
        )
        assert entry.detail["filename_ext"] == ""
        assert entry.detail["filename_hash"] == hmac_hash_value("ohne-endung")

    def test_empty_filename_stays_empty(self, facility, staff_user):
        """Pre-Save-Rejects ohne Dateinamen duerfen keinen Pseudo-Hash erzeugen."""
        from core.services.file_vault.audit import log_attachment_violation

        entry = log_attachment_violation(facility, staff_user, _OwnerStub(), reason="mime_mismatch", filename="")
        assert entry.detail["filename_hash"] == ""
        assert entry.detail["filename_ext"] == ""


class TestExtensionMarkerIsBounded:
    """Refs #1755 (Review): ``_extension`` schnitt frueher ALLES nach dem letzten
    Punkt ab — ohne Laengen-/Zeichengrenze. Bei Dateinamen ohne echte Endung
    landete so ein beliebig langer Namensrest im append-only ``detail`` (genau
    die Klienten-PII, die die Hash-Minimierung entfernen soll). Der Marker ist
    jetzt auf einen kurzen, rein alphanumerischen Typmarker begrenzt.
    """

    def test_real_extensions_are_preserved(self):
        from core.services.file_vault.audit import _extension

        assert _extension("bericht.pdf") == "pdf"
        assert _extension("foto.JPEG") == "jpeg"
        assert _extension("scan.PNG") == "png"
        assert _extension("vorlage.docx") == "docx"
        assert _extension("schaden.exe") == "exe"

    def test_name_remainder_after_dot_is_dropped(self):
        """Die im Review genannten Beispiele: kein Namensfragment mehr im Marker."""
        from core.services.file_vault.audit import _extension

        # Langer, zeichenhaltiger Rest (Bindestriche) -> raus.
        assert _extension(".Bericht-Max-Mustermann") == ""
        assert _extension("Foto von Familie Mueller-Schmidt") == ""
        # Kurz, aber laenger als ein realer Typmarker -> ebenfalls raus (5 Zeichen).
        assert _extension("Attest Dr.Meier") == ""

    def test_no_dot_stays_empty(self):
        from core.services.file_vault.audit import _extension

        assert _extension("ohne-endung") == ""
        assert _extension("") == ""


class TestMilestoneDeleteDetailMinimized:
    """``MILESTONE_DELETE`` schrieb den Meilenstein-Titel woertlich ins ``detail``.

    Der Titel ist derselbe klientenbezogene Freitext, den die Anonymisierung an
    ``Milestone.title`` tilgt (Refs #1751, Punkt 1) — im append-only Log
    ueberlebte er sie. Die Zuordnung bleibt ueber ``target_id``
    (Meilenstein-PK) und ``case_id`` vollstaendig erhalten.
    """

    def test_detail_has_no_title(self, staff_user, case_open):
        from core.models.outcome import Milestone, OutcomeGoal
        from core.services.case import delete_milestone

        goal = OutcomeGoal.objects.create(case=case_open, title="Ziel", created_by=staff_user)
        milestone = Milestone.objects.create(goal=goal, title="Termin bei Klartext-Amt")
        pk = milestone.pk

        delete_milestone(milestone, staff_user)

        log = AuditLog.objects.get(target_id=str(pk))
        assert log.action == AuditLog.Action.MILESTONE_DELETE
        assert "title" not in log.detail, "Meilenstein-Titel darf nicht im append-only AuditLog landen"
        assert log.detail["case_id"] == str(case_open.pk)
        assert log.target_type == "Milestone"


class TestClientExportAuditDetailMinimized:
    """Der Art.-15-/20-Export schrieb das Klartext-Pseudonym ins ``detail``.

    Es ist eine reine Dublette von ``target_id`` (Client-PK) — dieselbe
    Begruendung, mit der Refs #1093 das Pseudonym aus dem Loeschpfad entfernt
    hat. ``SUDO_MODE_ENABLED`` ist in ``settings/test.py`` aus, der Aufruf
    braucht daher keine Re-Auth.
    """

    @pytest.mark.parametrize(
        ("url_name", "expected_type"),
        [("core:client_export_json", "Client-JSON"), ("core:client_export_pdf", "Client-PDF")],
    )
    def test_export_audit_has_no_pseudonym(self, client, lead_user, client_identified, url_name, expected_type):
        client.force_login(lead_user)

        response = client.get(reverse(url_name, kwargs={"pk": client_identified.pk}))
        assert response.status_code == 200

        log = AuditLog.objects.filter(action=AuditLog.Action.EXPORT, target_type=expected_type).latest("timestamp")
        assert "pseudonym" not in log.detail, "Pseudonym ist eine target_id-Dublette und gehoert nicht ins AuditLog"
        assert log.detail["format"] in ("JSON", "PDF")
        assert log.target_id == str(client_identified.pk)


class TestEventUpdateDetailMinimized:
    """``EVENT_UPDATE`` (Refs #1958) fuehrt den Feld-Diff einer Ereignis-Bearbeitung.

    Neue Schreibstelle, deshalb hier mit gepinnt: das ``detail`` traegt nur
    Feld-*Namen* (Slugs) plus den Namen des Dokumentationstyps — nie einen
    Feldwert. Der Wert ist bei einem Ereignis der Beratungsinhalt und faellt
    unter dieselbe Minimierung wie der Meilenstein-Titel oben; bei einem
    verschluesselten Feld waere er zusaetzlich genau der Klartext, den der
    File-Vault aus der Datenbank heraushaelt.
    """

    def test_detail_traegt_nur_slugs_keine_werte(self, staff_user, facility, doc_type_crisis, client_identified):
        from django.utils import timezone

        from core.models import Event
        from core.services.events import update_event

        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_crisis,
            occurred_at=timezone.now(),
            data_json={"notiz-krise": "Alter Klartext"},
            created_by=staff_user,
        )

        update_event(event, staff_user, {"notiz-krise": "Neuer Klartext mit Klientenbezug"})

        log = AuditLog.objects.get(action=AuditLog.Action.EVENT_UPDATE, target_id=str(event.pk))
        assert log.detail["changed_fields"] == ["notiz-krise"]
        assert set(log.detail) == {"document_type", "changed_fields"}
        assert "Klartext" not in str(log.detail), "Feldwerte gehoeren nicht ins append-only AuditLog"
        assert log.target_type == "Event"


class TestEpisodeAndGoalUpdateDetailMinimized:
    """``EPISODE_UPDATE``/``GOAL_UPDATE`` (Refs #1959) fuehren nur Feldnamen.

    Beide Schreibstellen sind mit #1959 neu und laufen deshalb hier mit: Titel
    und Beschreibung einer Episode bzw. eines Wirkungsziels sind derselbe
    klientenbezogene Freitext, den die Anonymisierung an ``Episode.description``
    tilgt (Refs #1751) — im append-only Log ueberlebte er sie. Die Zuordnung
    bleibt ueber ``target_id`` (Episoden-/Ziel-PK) und ``case_id`` vollstaendig.
    """

    def test_episode_detail_traegt_nur_feldnamen(self, staff_user, case_open, episode):
        from core.services.case import update_episode

        update_episode(
            episode,
            staff_user,
            basis_token=episode_basis_token(episode),
            title="Klartext-Titel",
            description="Klartext-Verlauf",
        )

        log = AuditLog.objects.get(action=AuditLog.Action.EPISODE_UPDATE, target_id=str(episode.pk))
        assert set(log.detail) == {"case_id", "changed_fields"}
        assert log.detail["changed_fields"] == ["title", "description"]
        assert "Klartext" not in str(log.detail), "Feldwerte gehoeren nicht ins append-only AuditLog"
        assert log.target_type == "Episode"

    def test_goal_detail_traegt_nur_feldnamen(self, staff_user, case_open, outcome_goal):
        from core.services.case import update_goal

        update_goal(outcome_goal, staff_user, title="Klartext-Ziel", description="Klartext-Weg")

        log = AuditLog.objects.get(action=AuditLog.Action.GOAL_UPDATE, target_id=str(outcome_goal.pk))
        assert set(log.detail) == {"case_id", "changed_fields"}
        assert "Klartext" not in str(log.detail)
        assert log.target_type == "OutcomeGoal"


class TestCaseEventAssignmentDetailMinimized:
    """``CASE_EVENT_ASSIGNED``/``CASE_EVENT_REMOVED`` (Refs #1959) fuehren nur UUIDs.

    Der Falltitel ist Freitext (die Anonymisierung ueberschreibt ihn), das
    ``detail`` traegt deshalb ausschliesslich Fall-UUIDs — dieselbe Einordnung
    wie ``case_id`` beim Meilenstein-Loeschen.
    """

    def test_assignment_detail_traegt_nur_uuids(self, staff_user, case_open, sample_event):
        from core.services.case import assign_event_to_case, remove_event_from_case

        assign_event_to_case(case_open, sample_event, staff_user)
        remove_event_from_case(sample_event, staff_user)

        for action in (AuditLog.Action.CASE_EVENT_ASSIGNED, AuditLog.Action.CASE_EVENT_REMOVED):
            log = AuditLog.objects.get(action=action)
            assert set(log.detail) == {"case_id"}
            assert case_open.title not in str(log.detail)

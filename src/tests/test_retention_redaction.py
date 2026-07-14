"""DSGVO-Regressionstests fuer beide Soft-Delete-Pfade (Refs #714).

Sowohl der manuelle ``soft_delete_event``-Pfad als auch der
automatische ``retention._soft_delete_events``-Pfad muessen identische
redaktierte EventHistory(DELETE)-Eintraege schreiben — kein Klartext
in JSONB persistieren, weil EventHistory append-only ist.
"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from core.models import DocumentType, Event, EventAttachment, EventHistory, FieldTemplate
from core.retention import enforcement as enforcement_mod
from core.services.events import build_redacted_delete_history, soft_delete_event
from core.services.file_vault import store_encrypted_file
from core.services.file_vault.storage import get_attachment_path
from core.services.retention import _soft_delete_events

# Minimal gueltiges PDF, das die libmagic-Pruefung in ``store_encrypted_file`` passiert.
PDF_HEADER = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
    b"xref\n0 3\n0000000000 65535 f\n"
    b"trailer<</Size 3/Root 1 0 R>>\n"
    b"startxref\n9\n%%EOF\n"
)


@pytest.fixture
def normal_doc_type(facility):
    return DocumentType.objects.create(
        facility=facility,
        name="Kontakt",
        category=DocumentType.Category.CONTACT,
        sensitivity=DocumentType.Sensitivity.NORMAL,
    )


@pytest.fixture
def event_with_pii(facility, normal_doc_type, admin_user, client_identified):
    return Event.objects.create(
        facility=facility,
        document_type=normal_doc_type,
        client=client_identified,
        occurred_at=timezone.now() - timedelta(days=200),
        data_json={
            "freitext": "psychische Krise, hat konsumiert",
            "ort": "Cafe X",
            "intervention": "Krisengespraech 30 min",
        },
        created_by=admin_user,
    )


@pytest.mark.django_db
class TestBuildRedactedDeleteHistory:
    """Helper baut nur Slugs in {_redacted: True, fields: [...]}."""

    def test_redacted_payload_contains_no_values(self, event_with_pii):
        payload = build_redacted_delete_history(event_with_pii)
        assert payload["_redacted"] is True
        assert sorted(payload["fields"]) == sorted(["freitext", "ort", "intervention"])
        # Ein einziger zusaetzlicher Test, der explizit die Klartext-Werte
        # negativ pruefen koennte: alle dict-Repr-Strings duerfen die
        # Originalwerte nicht enthalten.
        repr_str = str(payload)
        assert "psychische Krise" not in repr_str
        assert "Cafe X" not in repr_str
        assert "Krisengespraech" not in repr_str

    def test_empty_data_json_yields_empty_fields(self, facility, normal_doc_type, admin_user):
        event = Event.objects.create(
            facility=facility,
            document_type=normal_doc_type,
            occurred_at=timezone.now(),
            data_json={},
            created_by=admin_user,
        )
        payload = build_redacted_delete_history(event)
        assert payload == {"_redacted": True, "fields": []}


@pytest.mark.django_db
class TestSoftDeleteEventRedacts:
    """Der manuelle Loesch-Pfad redaktiert (war schon so, Regression-Schutz)."""

    def test_manual_soft_delete_writes_redacted_history(self, event_with_pii, admin_user):
        soft_delete_event(event_with_pii, admin_user)
        history = EventHistory.objects.filter(event=event_with_pii, action=EventHistory.Action.DELETE).first()
        assert history is not None
        assert history.data_before == {
            "_redacted": True,
            "fields": ["freitext", "ort", "intervention"],
        } or (
            history.data_before["_redacted"] is True
            and sorted(history.data_before["fields"]) == sorted(["freitext", "ort", "intervention"])
        )
        # Klartext darf nirgends in der EventHistory landen.
        assert "psychische Krise" not in str(history.data_before)


@pytest.mark.django_db
class TestRetentionSoftDeleteRedacts:
    """Der automatische Retention-Pfad redaktiert ebenfalls — fix Refs #714."""

    def test_retention_soft_delete_writes_redacted_history(self, event_with_pii, facility):
        qs = Event.objects.filter(pk=event_with_pii.pk)
        _soft_delete_events(qs, facility, category="anonymous", retention_days=90)

        history = EventHistory.objects.filter(event=event_with_pii, action=EventHistory.Action.DELETE).first()
        assert history is not None
        assert history.data_before["_redacted"] is True
        assert sorted(history.data_before["fields"]) == sorted(["freitext", "ort", "intervention"])

        # Klartext-Werte duerfen NICHT in der EventHistory landen.
        repr_str = str(history.data_before)
        assert "psychische Krise" not in repr_str
        assert "Cafe X" not in repr_str
        assert "Krisengespraech" not in repr_str

    def test_retention_soft_delete_clears_search_text(self, event_with_pii, facility):
        # Refs #1092: ``_soft_delete_events`` muss ``search_text`` in
        # ``update_fields`` aufnehmen, sonst persistiert Django den vom
        # ``pre_save``-Signal geleerten Wert nicht und der Klartext-PII bleibt
        # in der search_text-Spalte stehen (DSGVO-Residue).
        #
        # search_text deterministisch befuellen, ohne Slug-Mechanik: ``.update()``
        # umgeht das pre_save-Signal und schreibt echten Klartext in die Spalte —
        # genau die Residue, die der Soft-Delete tilgen muss (Stil analog
        # test_client_anonymize_characterization.py).
        Event.objects.filter(pk=event_with_pii.pk).update(search_text="Klartext-Krise")

        qs = Event.objects.filter(pk=event_with_pii.pk)
        _soft_delete_events(qs, facility, category="anonymous", retention_days=90)

        event_with_pii.refresh_from_db()
        assert event_with_pii.is_deleted is True
        assert event_with_pii.data_json == {}
        assert event_with_pii.search_text == ""

    def test_retention_soft_delete_includes_field_metadata(self, event_with_pii, facility):
        # Refs #714: bisheriger Retention-Pfad schrieb kein field_metadata,
        # was die Audit-Spur beim Restore unvollstaendig machte. Jetzt
        # konsistent mit soft_delete_event.
        qs = Event.objects.filter(pk=event_with_pii.pk)
        _soft_delete_events(qs, facility, category="anonymous", retention_days=90)
        history = EventHistory.objects.filter(event=event_with_pii, action=EventHistory.Action.DELETE).first()
        assert history is not None
        assert isinstance(history.field_metadata, dict)


@pytest.mark.django_db
class TestRetentionSoftDeleteAtomicOnCrash:
    """Refs #1344: Ein Crash mitten im Loesch-Loop darf keine
    DSGVO-Nachweisluecke hinterlassen.

    Frueher wurden Events einzeln geleert, die ``EventHistory``-Zeilen aber
    erst NACH der Schleife gesammelt per ``bulk_create`` geschrieben. Bricht
    der Lauf mitten drin ab, bleiben geleerte Events OHNE DELETE-Nachweis
    zurueck (und Re-Runs ueberspringen sie wegen ``is_deleted``-Filter).
    Der Fix macht jedes Event atomar: geleert genau dann, wenn seine
    ``EventHistory(DELETE)``-Zeile committet ist.
    """

    def _make_event(self, facility, doc_type, admin_user, client_identified, occurred_at):
        return Event.objects.create(
            facility=facility,
            document_type=doc_type,
            client=client_identified,
            occurred_at=occurred_at,
            data_json={"freitext": "sensibler Klartext", "ort": "Ort X"},
            created_by=admin_user,
        )

    def test_crash_mid_loop_leaves_no_evidence_gap(self, facility, normal_doc_type, admin_user, client_identified):
        ft = FieldTemplate.objects.create(facility=facility, name="Anhang", field_type="file")

        now = timezone.now()
        # Deterministische Reihenfolge ueber occurred_at (die qs ist danach sortiert).
        e1 = self._make_event(facility, normal_doc_type, admin_user, client_identified, now - timedelta(days=300))
        e2 = self._make_event(facility, normal_doc_type, admin_user, client_identified, now - timedelta(days=200))
        e3 = self._make_event(facility, normal_doc_type, admin_user, client_identified, now - timedelta(days=100))

        att1 = store_encrypted_file(
            facility, SimpleUploadedFile("a.pdf", PDF_HEADER, content_type="application/pdf"), ft, e1, admin_user
        )
        att2 = store_encrypted_file(
            facility, SimpleUploadedFile("b.pdf", PDF_HEADER, content_type="application/pdf"), ft, e2, admin_user
        )

        qs = Event.objects.filter(pk__in=[e1.pk, e2.pk, e3.pk]).order_by("occurred_at")

        # Crash beim 2. verarbeiteten Event (e2) simulieren — das erste Event
        # (e1) muss vollstaendig verarbeitet UND nachgewiesen sein.
        real_build = enforcement_mod.build_redacted_delete_history
        state = {"n": 0}

        def flaky(event):
            state["n"] += 1
            if state["n"] == 2:
                raise RuntimeError("simulierter Crash mitten im Retention-Loop")
            return real_build(event)

        with patch.object(enforcement_mod, "build_redacted_delete_history", side_effect=flaky):
            with pytest.raises(RuntimeError):
                _soft_delete_events(qs, facility, category="identified", retention_days=365)

        e1.refresh_from_db()
        e2.refresh_from_db()
        e3.refresh_from_db()

        # e1: committet — geleert UND mit genau einer DELETE-Nachweiszeile.
        assert e1.is_deleted is True
        assert e1.data_json == {}
        assert EventHistory.objects.filter(event=e1, action=EventHistory.Action.DELETE).count() == 1
        # Attachment-Datei des committeten Events wird nach dem Commit entfernt.
        assert not get_attachment_path(att1).exists()

        # e2: Crash-Item — VOLLSTAENDIG unangetastet, kein Halb-Zustand.
        assert e2.is_deleted is False
        assert e2.data_json == {"freitext": "sensibler Klartext", "ort": "Ort X"}
        assert not EventHistory.objects.filter(event=e2, action=EventHistory.Action.DELETE).exists()
        assert get_attachment_path(att2).exists()

        # e3: nie erreicht.
        assert e3.is_deleted is False
        assert e3.data_json == {"freitext": "sensibler Klartext", "ort": "Ort X"}

        # DSGVO-Kern: Anzahl geleerter Events == Anzahl EventHistory(DELETE).
        pks = [e1.pk, e2.pk, e3.pk]
        emptied = Event.objects.filter(pk__in=pks, is_deleted=True).count()
        history_deletes = EventHistory.objects.filter(event__pk__in=pks, action=EventHistory.Action.DELETE).count()
        assert emptied == history_deletes == 1


@pytest.mark.django_db
class TestRetentionAttachmentCleanupSelfHeals:
    """Refs #1344: Ein Crash WAEHREND des Anhang-Cleanups darf keine dauerhafte
    DSGVO-Anhang-Residue hinterlassen.

    Der physische ``.enc``-Unlink ist nicht rollback-bar. Lag der Cleanup
    AUSSERHALB der Event/History-Transaktion, war das Event bei einem Crash
    mitten im Unlink bereits ``is_deleted=True`` committet: Re-Runs uebersprangen
    es (``is_deleted``-Filter), die ``.enc``-Datei blieb MIT ihrem
    ``EventAttachment``-Record liegen, und ``cleanup_orphan_storage_files``
    ueberspringt Dateien MIT Record -> PII bliebe dauerhaft auf Platte. Der Fix
    zieht den Cleanup in dieselbe Transaktion: ein Crash rollt das Event zurueck
    (``is_deleted=False``), der naechste Lauf raeumt selbstheilend auf
    (``delete_attachment_file`` ist ``unlink(missing_ok)`` -> idempotent).
    """

    def test_crash_during_attachment_cleanup_self_heals(self, facility, normal_doc_type, admin_user, client_identified):
        ft = FieldTemplate.objects.create(facility=facility, name="Anhang", field_type="file")
        event = Event.objects.create(
            facility=facility,
            document_type=normal_doc_type,
            client=client_identified,
            occurred_at=timezone.now() - timedelta(days=200),
            data_json={"freitext": "sensibler Klartext", "ort": "Ort X"},
            created_by=admin_user,
        )
        att = store_encrypted_file(
            facility, SimpleUploadedFile("a.pdf", PDF_HEADER, content_type="application/pdf"), ft, event, admin_user
        )
        path = get_attachment_path(att)
        assert path.exists()

        real_delete = enforcement_mod.delete_event_attachments
        calls = {"n": 0}

        def flaky_delete(ev):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("simulierter Crash beim Anhang-Unlink")
            return real_delete(ev)

        # Produktions-Filter: die ``enforce_*``-Wrapper bauen die qs stets mit
        # ``is_deleted=False`` — ein Re-Run erreicht ein schon geleertes Event nicht.
        def prod_qs():
            return Event.objects.filter(pk=event.pk, is_deleted=False)

        with patch.object(enforcement_mod, "delete_event_attachments", side_effect=flaky_delete):
            with pytest.raises(RuntimeError):
                _soft_delete_events(prod_qs(), facility, category="identified", retention_days=365)

            # Crash-Zustand: KEIN committeter Halb-Zustand. Solange die Anhaenge
            # (mit Record) noch auf Platte liegen, darf das Event nicht als
            # geleert+geloescht committet sein — sonst dauerhafte PII-Residue.
            event.refresh_from_db()
            assert event.is_deleted is False
            assert event.data_json == {"freitext": "sensibler Klartext", "ort": "Ort X"}
            assert EventHistory.objects.filter(event=event, action=EventHistory.Action.DELETE).count() == 0
            assert EventAttachment.objects.filter(event=event).exists()
            assert path.exists()

            # Selbstheilung: der naechste Lauf (2. Aufruf ist real) raeumt vollstaendig auf.
            _soft_delete_events(prod_qs(), facility, category="identified", retention_days=365)

        event.refresh_from_db()
        assert event.is_deleted is True
        assert event.data_json == {}
        assert not EventAttachment.objects.filter(event=event).exists()
        assert not path.exists()
        # Genau EIN DELETE-Nachweis trotz Crash + Re-Run.
        assert EventHistory.objects.filter(event=event, action=EventHistory.Action.DELETE).count() == 1


@pytest.mark.django_db
class TestRetentionSaveHistoryAtomic:
    """Refs #1344: ``event.save`` und die ``EventHistory(DELETE)``-Zeile bilden
    EIN atomares Paar — schlaegt der Nachweis fehl, wird die Event-Mutation
    zurueckgerollt (kein geleertes Event ohne Nachweis)."""

    def test_history_create_failure_rolls_back_event(self, event_with_pii, facility):
        original = {
            "freitext": "psychische Krise, hat konsumiert",
            "ort": "Cafe X",
            "intervention": "Krisengespraech 30 min",
        }
        with patch.object(enforcement_mod.EventHistory.objects, "create", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError):
                _soft_delete_events(
                    Event.objects.filter(pk=event_with_pii.pk, is_deleted=False),
                    facility,
                    category="anonymous",
                    retention_days=90,
                )

        event_with_pii.refresh_from_db()
        # Kern-Atomaritaet: der Nachweis-Fehler rollt die Event-Mutation zurueck.
        assert event_with_pii.is_deleted is False
        assert event_with_pii.data_json == original
        assert EventHistory.objects.filter(event=event_with_pii, action=EventHistory.Action.DELETE).count() == 0

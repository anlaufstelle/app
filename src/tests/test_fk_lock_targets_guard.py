"""Vorsperre der Fremdschluessel-Ziele: aus dem Modell abgeleitet, in beiden Pfaden (Refs #2337).

Eine neue Zeile traegt Fremdschluessel; deren Pruefung laeuft auf PostgreSQL
aufgeschoben erst zum COMMIT und nimmt dort auf jede referenzierte Zeile eine
``FOR KEY SHARE``-Sperre — hinter dem bereits gehaltenen Ketten-Lock der
Einrichtung, wenn niemand sie vorher genommen hat (ADR-050 D5.5, Refs
#2259/#2260). Der Importpfad (``_lock_job_fk_rows``) leitete die Ziele aus
dem Modell ab; der Anhangspfad (``lock_batch_attachment_rows``) sperrte nur
Owner- und Personenzeile — Einrichtung, Feldvorlage und anlegende Person der
neuen Anhangszeile fielen erst beim COMMIT an, und ein spaeter hinzukommender
Fremdschluessel waere dort ebenfalls unbemerkt nachgerutscht.

Der Guard haengt beiden Modellen einen kuenstlichen Fremdschluessel an (auf
``Organization``, mit Default) und verlangt, dass seine Zielzeile in der
Vorsperre BEIDER Pfade erscheint — in der festen Reihenfolge Tabelle, dann PK.
"""

import re

import pytest
from django.db import connection, models, transaction

from core.models import DocumentType, DocumentTypeField, Event, FieldTemplate, ImportJob, Organization
from core.models.attachment import Attachment

pytestmark = pytest.mark.django_db

_KEY_SHARE_RE = re.compile(r'FROM "(?P<tabelle>\w+)" WHERE .* FOR KEY SHARE', re.IGNORECASE | re.DOTALL)


class _Mitschnitt(list):
    def __call__(self, execute, sql, params, many, context):
        self.append((sql, params))
        return execute(sql, params, many, context)

    def key_share(self):
        """``(tabelle, wert)`` je ``FOR KEY SHARE``-Abfrage, in Ausfuehrungsreihenfolge."""
        treffer = []
        for sql, params in self:
            match = _KEY_SHARE_RE.search(sql)
            if match:
                treffer.append((match.group("tabelle"), str(params[0])))
        return treffer


def _haenge_fk_an(monkeypatch, model, default):
    """Ein kuenstlicher Fremdschluessel auf ``Organization`` — nur in ``get_fields()`` sichtbar."""
    feld = models.ForeignKey(Organization, on_delete=models.CASCADE, null=True, default=default, related_name="+")
    feld.set_attributes_from_name("pruefziel")
    feld.model = model
    original = model._meta.get_fields

    def _get_fields(include_parents=True, include_hidden=False):
        return (*original(include_parents=include_parents, include_hidden=include_hidden), feld)

    monkeypatch.setattr(model._meta, "get_fields", _get_fields)
    return feld


def _file_doc_type(facility):
    dt = DocumentType.objects.create(facility=facility, name="FK-Vorsperre", entity_type=DocumentType.EntityType.EVENT)
    ft = FieldTemplate.objects.create(
        facility=facility, name="Anhang FK-Vorsperre", field_type=FieldTemplate.FieldType.FILE
    )
    DocumentTypeField.objects.create(document_type=dt, field_template=ft, sort_order=0)
    return dt, ft


def _ist_sortiert(treffer):
    return treffer == sorted(treffer)


class TestImportpfad:
    def test_kuenstlicher_fk_erscheint_in_der_vorsperre(self, monkeypatch, facility, staff_user, doc_type_contact):
        from core.services.import_engine import _lock_job_fk_rows

        _haenge_fk_an(monkeypatch, ImportJob, None)
        job = ImportJob(
            facility=facility,
            created_by=staff_user,
            target_type=ImportJob.TargetType.EVENT,
            document_type=doc_type_contact,
        )
        job.pruefziel_id = facility.organization_id

        mitschnitt = _Mitschnitt()
        with transaction.atomic(), connection.execute_wrapper(mitschnitt):
            _lock_job_fk_rows(job)

        treffer = mitschnitt.key_share()
        assert treffer == [
            ("core_documenttype", str(doc_type_contact.pk)),
            ("core_facility", str(facility.pk)),
            ("core_organization", str(facility.organization_id)),
            ("core_user", str(staff_user.pk)),
        ]


class TestAnhangspfad:
    def test_kuenstlicher_fk_erscheint_in_der_vorsperre(self, monkeypatch, facility, staff_user, client_identified):
        from core.services.attachments.crud import lock_attachment_batch_before_audit

        dt, ft = _file_doc_type(facility)
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=dt,
            occurred_at="2026-09-01T10:00:00+02:00",
            data_json={},
            created_by=staff_user,
        )
        _haenge_fk_an(monkeypatch, Attachment, facility.organization_id)

        mitschnitt = _Mitschnitt()
        with transaction.atomic(), connection.execute_wrapper(mitschnitt):
            lock_attachment_batch_before_audit(event, staff_user, {}, {ft.slug: ["gestagt"]}, dt, data_json={})

        treffer = mitschnitt.key_share()
        assert ("core_organization", str(facility.organization_id)) in treffer, treffer
        # Die Fremdschluessel, die der Insert der neuen Anhangszeile traegt —
        # nicht nur der Owner.
        assert ("core_facility", str(facility.pk)) in treffer, treffer
        assert ("core_fieldtemplate", str(ft.pk)) in treffer, treffer
        assert ("core_user", str(staff_user.pk)) in treffer, treffer
        assert ("core_event", str(event.pk)) in treffer, treffer
        assert _ist_sortiert(treffer), f"Vorsperre nicht in Tabelle-dann-PK-Ordnung: {treffer}"

    def test_ohne_neue_zeile_keine_fk_vorsperre(self, facility, staff_user, client_identified):
        """Gegenprobe: ein Batch, der keine Zeile anlegt, sperrt keine FK-Ziele."""
        from core.services.attachments.crud import lock_attachment_batch_before_audit

        dt, _ft = _file_doc_type(facility)
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=dt,
            occurred_at="2026-09-01T10:00:00+02:00",
            data_json={},
            created_by=staff_user,
        )

        mitschnitt = _Mitschnitt()
        with transaction.atomic(), connection.execute_wrapper(mitschnitt):
            lock_attachment_batch_before_audit(event, staff_user, {}, {}, dt, data_json={})

        assert mitschnitt.key_share() == []

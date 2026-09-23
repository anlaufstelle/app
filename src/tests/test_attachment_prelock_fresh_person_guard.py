"""Anhangs-Vorsperre nach einer Personen-Zusammenfuehrung (Refs #2379).

Die Vorsperre eines Anhangs-Batches (``lock_owner_rows_in_facility_order``)
nimmt Stufe 1 der kanonischen Ordnung: erst die Personenzeile, dann die
Owner-Zeile (ADR-050 D5.5, ``core_client`` -> ``core_case``/``core_event``).
Fall- und Ereignis-Owner tragen ihre Person als ``client_id``.

Die Owner-Instanz stammt aus einem Lesevorgang VOR der Transaktion. Fuehrt
die Leitung in der Zwischenzeit die Person P mit Q zusammen, haengen Fall und
Ereignis danach an Q — die Instanz zeigt aber weiterhin auf P. Die Vorsperre
muss deshalb die Person sperren, an der die Owner-Zeile JETZT haengt, und die
Instanz nachziehen, damit die folgenden Schritte desselben Vorgangs
(Art.-18-Gate im ``update_*``-Service, ``apply_attachment_changes``) dieselbe
Person treffen. Andernfalls sperrt der Vorgang die abgegebene Person P, und
die tatsaechliche Person Q faellt — sobald sie jemand nimmt — hinter die
Owner-Zeile.

Die Guards beobachten nur die Sperrfolge per ``CaptureQueriesContext`` —
kein Thread, kein Warten. UUIDs stehen im Mitschnitt ohne Bindestriche,
deshalb wird ueber ``.hex`` verglichen.
"""

from __future__ import annotations

import re
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from core.models import Case, Client, DocumentType, DocumentTypeField, Event, FieldTemplate
from core.services.attachments import crud
from core.services.case.state_contract import CASE_WRITE_GONE_MESSAGE, EVENT_WRITE_GONE_MESSAGE
from core.services.client.merge import merge_clients

pytestmark = pytest.mark.postgres

PDF_MINIMAL = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
    b"xref\n0 3\n0000000000 65535 f\n"
    b"trailer<</Size 3/Root 1 0 R>>\n"
    b"startxref\n9\n%%EOF\n"
)

_FOR_UPDATE_RE = re.compile(r'FROM "(?P<table>core_\w+)"[^;]*FOR UPDATE', re.IGNORECASE | re.DOTALL)


def _zeilensperren(queries) -> list[tuple[str, str]]:
    """``(tabelle, sql)`` jeder ``SELECT … FOR UPDATE``-Abfrage in Reihenfolge."""
    sperren = []
    for query in queries:
        match = _FOR_UPDATE_RE.search(query["sql"])
        if match:
            sperren.append((match.group("table"), query["sql"]))
    return sperren


def _person(facility, staff_user, pseudonym):
    return Client.objects.create(
        facility=facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
        pseudonym=pseudonym,
        created_by=staff_user,
    )


def _assert_frische_person_zuerst(sperren, *, alt, neu, owner_table):
    """Erste Sperre = Q, danach die Owner-Zeile; P wird nie gesperrt."""
    assert sperren, "Die Vorsperre muss ueberhaupt sperren"
    erste_tabelle, erstes_sql = sperren[0]
    assert erste_tabelle == "core_client", sperren
    assert neu.pk.hex in erstes_sql, "Erste Sperre muss die aktuelle Person Q treffen"
    assert alt.pk.hex not in erstes_sql
    assert [sql for _t, sql in sperren if alt.pk.hex in sql] == [], "Keine Sperre auf der abgegebenen Person P"
    owner_index = next(i for i, (tabelle, _sql) in enumerate(sperren) if tabelle == owner_table)
    assert owner_index > 0
    personen_hinter_owner = [
        sql for tabelle, sql in sperren[owner_index:] if tabelle == "core_client" and neu.pk.hex not in sql
    ]
    assert personen_hinter_owner == [], "Hinter der Owner-Zeile darf keine andere Person gesperrt werden"


class TestVorsperreNachZusammenfuehrung:
    """Service-Ebene: die Owner-Instanz ist vor dem Merge geladen."""

    def test_fall_owner_sperrt_die_aktuelle_person(self, facility, staff_user, lead_user):
        alt = _person(facility, staff_user, "Vorsperre-P")
        neu = _person(facility, staff_user, "Vorsperre-Q")
        case = Case.objects.create(facility=facility, client=alt, title="Fall", created_by=staff_user)
        veraltet = Case.objects.get(pk=case.pk)

        merge_clients(alt, neu, lead_user)
        assert Case.objects.get(pk=case.pk).client_id == neu.pk
        assert veraltet.client_id == alt.pk

        with CaptureQueriesContext(connection) as ctx, transaction.atomic():
            crud.lock_owner_rows_in_facility_order(veraltet)

        _assert_frische_person_zuerst(_zeilensperren(ctx.captured_queries), alt=alt, neu=neu, owner_table="core_case")
        assert veraltet.client_id == neu.pk, "Instanz muss auf die aktuelle Person nachgezogen sein"

    def test_ereignis_owner_sperrt_die_aktuelle_person(self, facility, staff_user, lead_user, doc_type_contact):
        from django.utils import timezone

        alt = _person(facility, staff_user, "Vorsperre-P")
        neu = _person(facility, staff_user, "Vorsperre-Q")
        event = Event.objects.create(
            facility=facility,
            client=alt,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        veraltet = Event.objects.get(pk=event.pk)

        merge_clients(alt, neu, lead_user)
        assert Event.objects.get(pk=event.pk).client_id == neu.pk

        with CaptureQueriesContext(connection) as ctx, transaction.atomic():
            crud.lock_owner_rows_in_facility_order(veraltet)

        _assert_frische_person_zuerst(_zeilensperren(ctx.captured_queries), alt=alt, neu=neu, owner_table="core_event")
        assert veraltet.client_id == neu.pk

    def test_unveraenderte_bindung_sperrt_wie_bisher(self, facility, staff_user):
        """Ohne Zusammenfuehrung: Person, dann Fall — je genau eine Sperre."""
        person = _person(facility, staff_user, "Vorsperre-X")
        case = Case.objects.create(facility=facility, client=person, title="Fall", created_by=staff_user)

        with CaptureQueriesContext(connection) as ctx, transaction.atomic():
            crud.lock_owner_rows_in_facility_order(case)

        sperren = _zeilensperren(ctx.captured_queries)
        assert [tabelle for tabelle, _sql in sperren] == ["core_client", "core_case"]
        assert person.pk.hex in sperren[0][1]
        assert case.client_id == person.pk

    def test_unsichtbare_owner_zeile_sperrt_die_person_der_instanz(self, facility, staff_user):
        """Ohne sichtbare Owner-Zeile bleibt es bei der Person der Instanz."""
        person = _person(facility, staff_user, "Vorsperre-Y")
        case = Case.objects.create(facility=facility, client=person, title="Fall", created_by=staff_user)
        veraltet = Case.objects.get(pk=case.pk)
        Case.objects.filter(pk=case.pk).delete()

        with CaptureQueriesContext(connection) as ctx, transaction.atomic():
            crud.lock_owner_rows_in_facility_order(veraltet)

        sperren = _zeilensperren(ctx.captured_queries)
        assert [tabelle for tabelle, _sql in sperren] == ["core_client", "core_case"]
        assert person.pk.hex in sperren[0][1]
        assert veraltet.client_id == person.pk


class _BindungWechseltVorDerPersonensperre:
    """Haengt den Owner um, unmittelbar bevor die erste Personensperre laeuft.

    Bildet den Ablauf nach, in dem ein paralleles Zusammenfuehren zwischen
    der frischen Bindungslesung und der Personensperre committet: die
    gelesene Person ist beim Sperren bereits nicht mehr die Person des Owners.
    """

    def __init__(self, owner_model, owner_pk, andere_person):
        self.owner_model = owner_model
        self.owner_pk = owner_pk
        self.andere_person = andere_person
        self.ausgeloest = False

    def __call__(self, execute, sql, params, many, context):
        if not self.ausgeloest and 'FROM "core_client"' in sql and "FOR UPDATE" in sql.upper():
            self.ausgeloest = True
            self.owner_model.objects.filter(pk=self.owner_pk).update(client=self.andere_person)
        return execute(sql, params, many, context)


class TestBindungswechselWaehrendDerVorsperre:
    """Wechsel zwischen Lesung und Personensperre: ablehnen, nicht nachsperren."""

    @pytest.mark.parametrize("owner_art", ["fall", "ereignis"])
    def test_wechsel_wird_abgelehnt_ohne_zweite_personensperre(self, owner_art, facility, staff_user, doc_type_contact):
        from django.utils import timezone

        gelesen = _person(facility, staff_user, "Wechsel-P")
        andere = _person(facility, staff_user, "Wechsel-R")
        if owner_art == "fall":
            owner = Case.objects.create(facility=facility, client=gelesen, title="Fall", created_by=staff_user)
            meldung = CASE_WRITE_GONE_MESSAGE
        else:
            owner = Event.objects.create(
                facility=facility,
                client=gelesen,
                document_type=doc_type_contact,
                occurred_at=timezone.now(),
                data_json={},
                created_by=staff_user,
            )
            meldung = EVENT_WRITE_GONE_MESSAGE
        wechsel = _BindungWechseltVorDerPersonensperre(type(owner), owner.pk, andere)

        with CaptureQueriesContext(connection) as ctx, transaction.atomic():
            with connection.execute_wrapper(wechsel), pytest.raises(ValidationError) as abgelehnt:
                crud.lock_owner_rows_in_facility_order(owner)

        assert wechsel.ausgeloest, "Die Personensperre muss gelaufen sein"
        assert str(meldung) in abgelehnt.value.messages
        personensperren = [sql for tabelle, sql in _zeilensperren(ctx.captured_queries) if tabelle == "core_client"]
        assert len(personensperren) == 1, "Keine zweite Person in derselben Transaktion nachsperren"
        assert gelesen.pk.hex in personensperren[0]
        assert andere.pk.hex not in personensperren[0]


class TestZaehlprobeStabileBindung:
    """Stabile Bindung: dieselben Zeilensperren wie ohne Frischlesung, eine Lesung mehr."""

    def test_zwei_sperren_und_eine_lesung(self, facility, staff_user):
        person = _person(facility, staff_user, "Zaehl-X")
        case = Case.objects.create(facility=facility, client=person, title="Fall", created_by=staff_user)

        with CaptureQueriesContext(connection) as ctx, transaction.atomic():
            crud.lock_owner_rows_in_facility_order(case)

        sqls = [q["sql"] for q in ctx.captured_queries if "SAVEPOINT" not in q["sql"]]
        sperren = _zeilensperren(ctx.captured_queries)
        assert [tabelle for tabelle, _sql in sperren] == ["core_client", "core_case"]
        lesungen = [sql for sql in sqls if "FOR UPDATE" not in sql.upper()]
        assert len(lesungen) == 1, lesungen
        assert lesungen[0].startswith('SELECT "core_case"."client_id"')
        assert 'FROM "core_case"' in lesungen[0]
        assert len(sqls) == 3


class TestFallBearbeitungNachZusammenfuehrung:
    """HTTP-Weg: Merge nach dem Laden des Falls, vor der Transaktion der View."""

    def test_erste_sperre_ist_die_aktuelle_person(self, client, facility, staff_user, lead_user):
        alt = _person(facility, staff_user, "Fall-P")
        neu = _person(facility, staff_user, "Fall-Q")
        doc_type = DocumentType.objects.create(
            facility=facility, name="Fall mit Anhang", entity_type=DocumentType.EntityType.CASE
        )
        ft_file = FieldTemplate.objects.create(
            facility=facility, name="Anhang Fall", field_type=FieldTemplate.FieldType.FILE
        )
        ft_text = FieldTemplate.objects.create(
            facility=facility, name="Notiz Fall", field_type=FieldTemplate.FieldType.TEXT
        )
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_file, sort_order=0)
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_text, sort_order=1)
        case = Case.objects.create(
            facility=facility,
            client=alt,
            title="Sperr-Sonde Fall",
            description="alt",
            document_type=doc_type,
            data_json={},
            created_by=staff_user,
        )
        from core.services.file_vault import store_encrypted_file

        upload = SimpleUploadedFile("probe.pdf", PDF_MINIMAL, content_type="application/pdf")
        with patch("core.services.file_vault.storage.run_virus_scan", lambda *a, **kw: None):
            attachment = store_encrypted_file(facility, upload, ft_file, case, staff_user)
        case.data_json = {ft_file.slug: {"__files__": True, "entries": [{"id": str(attachment.pk), "sort": 0}]}}
        case.save(update_fields=["data_json"])

        import core.views.cases as cases_view

        original = cases_view.stage_attachment_uploads
        ab_index: list[int] = []

        with CaptureQueriesContext(connection) as ctx:

            def _merge_dann_stagen(*args, **kwargs):
                # Laeuft in der View nach dem Laden des Falls und vor ihrer
                # Transaktion: die Instanz der View haelt danach noch P.
                merge_clients(alt, neu, lead_user)
                ab_index.append(len(ctx.captured_queries))
                return original(*args, **kwargs)

            client.force_login(staff_user)
            with patch.object(cases_view, "stage_attachment_uploads", _merge_dann_stagen):
                resp = client.post(
                    reverse("core:case_update", kwargs={"pk": case.pk}),
                    {
                        "title": "Sperr-Sonde Fall",
                        "description": "geaendert",
                        "lead_user": "",
                        "document_type": str(doc_type.pk),
                        ft_text.slug: "geaendert",
                        f"{ft_file.slug}__remove": str(attachment.entry_id),
                    },
                )

        # Vorbedingungen: Merge lief im Speichervorgang, Fall haengt an Q,
        # Speichern gelang, der Anhang ist entfernt (die Vorsperre lief).
        assert ab_index, "Der Merge-Hook muss gelaufen sein"
        assert resp.status_code == 302, resp.content
        assert Case.objects.get(pk=case.pk).client_id == neu.pk
        attachment.refresh_from_db()
        assert attachment.deleted_at is not None

        sperren = _zeilensperren(ctx.captured_queries[ab_index[0] :])
        assert any(tabelle == "core_attachment" for tabelle, _sql in sperren)
        _assert_frische_person_zuerst(sperren, alt=alt, neu=neu, owner_table="core_case")


class TestEndgueltigesLoeschenBeiBindungswechsel:
    """HTTP-Weg ``attachment_delete``: der Bindungswechsel in der Vorsperre endet kontrolliert.

    Die Vorsperre lehnt einen Wechsel der Person zwischen Lesung und Sperre
    mit ``ValidationError`` ab. Der Loeschpfad ruft sie innerhalb seiner
    Transaktion auf; die Ablehnung muss dort als Meldung mit Weiterleitung
    ankommen — kein Serverfehler, keine Datei weg, kein Loesch- und kein
    Aufbewahrungssperren-Protokoll.
    """

    @pytest.mark.parametrize("owner_art", ["fall", "ereignis"])
    def test_wechsel_endet_mit_meldung_statt_serverfehler(self, owner_art, client, facility, staff_user, lead_user):
        from django.utils import timezone

        from core.models import AuditLog
        from core.models.attachment import Attachment
        from core.services.file_vault import get_attachment_path, store_encrypted_file

        gelesen = _person(facility, staff_user, "Loeschen-P")
        andere = _person(facility, staff_user, "Loeschen-R")
        ft_file = FieldTemplate.objects.create(
            facility=facility, name="Anhang Loeschen", field_type=FieldTemplate.FieldType.FILE
        )
        if owner_art == "fall":
            doc_type = DocumentType.objects.create(
                facility=facility, name="Fall Loeschen", entity_type=DocumentType.EntityType.CASE
            )
            owner = Case.objects.create(
                facility=facility,
                client=gelesen,
                title="Loesch-Sonde Fall",
                document_type=doc_type,
                data_json={},
                created_by=staff_user,
            )
            meldung = CASE_WRITE_GONE_MESSAGE
        else:
            doc_type = DocumentType.objects.create(
                facility=facility, name="Ereignis Loeschen", entity_type=DocumentType.EntityType.EVENT
            )
            owner = Event.objects.create(
                facility=facility,
                client=gelesen,
                document_type=doc_type,
                occurred_at=timezone.now(),
                data_json={},
                created_by=staff_user,
            )
            meldung = EVENT_WRITE_GONE_MESSAGE
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_file, sort_order=0)
        upload = SimpleUploadedFile("loeschen.pdf", PDF_MINIMAL, content_type="application/pdf")
        with patch("core.services.file_vault.storage.run_virus_scan", lambda *a, **kw: None):
            attachment = store_encrypted_file(facility, upload, ft_file, owner, staff_user)
        owner.data_json = {ft_file.slug: {"__files__": True, "entries": [{"id": str(attachment.pk), "sort": 0}]}}
        owner.save(update_fields=["data_json"])
        pfad = get_attachment_path(attachment)
        protokoll_vorher = set(AuditLog.objects.values_list("pk", flat=True))
        wechsel = _BindungWechseltVorDerPersonensperre(type(owner), owner.pk, andere)

        client.force_login(lead_user)
        with connection.execute_wrapper(wechsel):
            resp = client.post(reverse("core:attachment_delete", kwargs={"pk": attachment.pk}))

        assert wechsel.ausgeloest, "Die Personensperre der Vorsperre muss gelaufen sein"
        assert resp.status_code == 302, resp.status_code
        assert resp.url == reverse("core:attachment_list")
        meldungen = [str(m) for m in list(resp.wsgi_request._messages)]
        assert meldungen == [str(meldung)]
        assert pfad.exists(), "Die Datei darf nicht verschwinden"
        assert Attachment.objects.filter(entry_id=attachment.entry_id).count() == 1
        neue_protokolle = AuditLog.objects.exclude(pk__in=protokoll_vorher)
        assert not neue_protokolle.filter(
            action__in=[AuditLog.Action.ATTACHMENT_DELETE, AuditLog.Action.LEGAL_HOLD]
        ).exists()

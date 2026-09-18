"""Einrichtungsweite Sperr-Reihenfolge der anhangsmutierenden Pfade.

Die AuditLog-Hashkette nimmt je Einrichtung einen transaktionsweiten
Advisory-Lock (``core/services/audit/chain.py``). Anhangs-Pfade sperren
``Attachment``-Zeilen. Nehmen zwei Pfade beide Sperren in verkehrter
Reihenfolge, entsteht ein Zyklus, den PostgreSQL mit SQLSTATE ``40P01``
aufloest — ein Vorgang bricht ab.

Die verbindliche Ordnung fuer JEDEN anhangsmutierenden Pfad lautet:

1. Owner-/Personen-/Ereignis-Zeilen,
2. Attachment-Zeilen in aufsteigender PK-Reihenfolge,
3. Ketten-Advisory-Lock (Audit-Schreiben) zuletzt.

Das ist die Ausdehnung des Batch-Vertrags, den der Kontingent-Backstop fuer
die regulaeren Batch-Einstiege eingefuehrt hat („alle Batch-Zeilen vor dem
ersten Advisory-Lock sperren"), auf die uebrigen Pfade — nicht dessen
Umkehrung.

Zwei Sorten Guards:

* **Sequenz-Guards** pinnen die Reihenfolge am ausgefuehrten SQL
  (Query-Mitschnitt, ein Prozess, kein Timing) — dieselbe Disziplin wie in
  ``test_restriction_lock_order.py``.
* **Rendezvous-Guards** stellen je Gegenrichtung die zweite Transaktion
  tatsaechlich daneben (eigene Verbindung, Barriere mit Deadline) und
  beobachten, ob der Zyklus entsteht. Sie warten nie unbegrenzt und geben
  ihre Verbindungen am Testende sicher frei.

Der Advisory-Lock ist auf SQLite ein No-op; die Beobachtung braucht
PostgreSQL (wie ``test_audit_lock_scan_release.py``).
"""

from __future__ import annotations

import re
import threading
import time
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import OperationalError, connection, connections, transaction
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import Case, Client, DocumentType, DocumentTypeField, Event, FieldTemplate

pytestmark = pytest.mark.postgres

PDF_MINIMAL = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
    b"xref\n0 3\n0000000000 65535 f\n"
    b"trailer<</Size 3/Root 1 0 R>>\n"
    b"startxref\n9\n%%EOF\n"
)

#: Pseudo-Tabellenname fuer den Ketten-Advisory-Lock im Sequenz-Mitschnitt.
CHAIN = "audit_chain"

#: ``FOR UPDATE`` verraet die Zeilensperre; der Tabellenname steht im ``FROM``.
#: Bewusst grob, damit die Erkennung nicht an Django-Formatierungsdetails haengt.
_FOR_UPDATE_RE = re.compile(r'FROM "(?P<table>core_\w+)"[^;]*FOR UPDATE', re.IGNORECASE | re.DOTALL)

#: Eine nach PK geordnete Vorsperre auf den Anhangszeilen. PostgreSQL bekommt
#: die Ordnung je nach Select-Liste als Spaltenname oder als Positionsangabe
#: (``ORDER BY 1``) — beides ist dieselbe Spalte.
_ORDERED_ATTACHMENT_LOCK_RE = re.compile(
    r'FROM "core_attachment"[^;]*ORDER BY (?:1|"core_attachment"\."id") ASC[^;]*FOR UPDATE',
    re.IGNORECASE | re.DOTALL,
)

#: Obergrenze fuer jedes Warten in den Rendezvous-Guards (Sekunden). Kein Test
#: wartet laenger; laeuft sie ab, gilt der beobachtete Zustand.
RENDEZVOUS_DEADLINE = 4.0

#: PostgreSQL-SQLSTATE fuer „deadlock detected".
DEADLOCK = "40P01"

#: SQLSTATEs der DB-seitigen Zeitgrenzen (``lock_not_available``,
#: ``query_canceled``). Sie sind der Rettungsanker, falls die Ordnung verletzt
#: ist und der Deadlock-Detektor den Zyklus nicht aufloest: kein Test haengt,
#: er faellt.
TIMEOUT_SQLSTATES = frozenset({"55P03", "57014"})

#: Jede Testverbindung bekommt ihre Zeitgrenzen DB-seitig — die Deadline im
#: Python-Code begrenzt nur die Poll-Schleife, nicht ein blockierendes
#: ``pg_advisory_xact_lock``. Die Sperrgrenze liegt ueber ``deadlock_timeout``
#: (1 s), damit ein echter Zyklus als ``40P01`` sichtbar wird und nicht als
#: Zeitgrenze.
GEGEN_LOCK_TIMEOUT = "3000ms"
WORKER_LOCK_TIMEOUT = "6000ms"
WORKER_STATEMENT_TIMEOUT = "9000ms"


def _lock_sequence(captured) -> list[str]:
    """Reihenfolge der gesperrten Tabellen bzw. des Ketten-Locks im Mitschnitt."""
    sequence: list[str] = []
    for query in captured.captured_queries:
        sql = query["sql"]
        if "pg_advisory_xact_lock" in sql:
            sequence.append(CHAIN)
            continue
        match = _FOR_UPDATE_RE.search(sql)
        if match:
            sequence.append(match.group("table"))
    return sequence


def _first_index(sequence, table):
    return sequence.index(table) if table in sequence else None


def _statement_index(captured, predicate):
    """Index der ersten Abfrage, auf die ``predicate`` passt (oder ``None``)."""
    for index, query in enumerate(captured.captured_queries):
        if predicate(query["sql"]):
            return index
    return None


def _ordered_attachment_lock_index(captured):
    return _statement_index(captured, lambda sql: bool(_ORDERED_ATTACHMENT_LOCK_RE.search(sql)))


# ---------------------------------------------------------------- Fixtures --


def _doc_type(facility, *, entity_type, name):
    """Typ mit genau einem FILE- und einem TEXT-Feld.

    Das TEXT-Feld gibt dem Update-Pfad ein echtes Delta — ohne das schriebe
    ``update_*`` keine Audit-Zeile und der Ketten-Lock kaeme im Mitschnitt gar
    nicht vor.
    """
    dt = DocumentType.objects.create(facility=facility, name=name, entity_type=entity_type)
    ft_file = FieldTemplate.objects.create(
        facility=facility,
        name=f"Anhang {name}",
        field_type=FieldTemplate.FieldType.FILE,
    )
    ft_text = FieldTemplate.objects.create(
        facility=facility,
        name=f"Notiz {name}",
        field_type=FieldTemplate.FieldType.TEXT,
    )
    DocumentTypeField.objects.create(document_type=dt, field_template=ft_file, sort_order=0)
    DocumentTypeField.objects.create(document_type=dt, field_template=ft_text, sort_order=1)
    return dt, ft_file, ft_text


def _attach(facility, owner, field_template, user):
    """Legt eine echte Anhangszeile an (Scan gemockt) und setzt den Marker."""
    from core.services.file_vault import store_encrypted_file

    upload = SimpleUploadedFile("probe.pdf", PDF_MINIMAL, content_type="application/pdf")
    with patch("core.services.file_vault.storage.run_virus_scan", lambda *a, **kw: None):
        attachment = store_encrypted_file(facility, upload, field_template, owner, user)
    data = dict(owner.data_json or {})
    data[field_template.slug] = {"__files__": True, "entries": [{"id": str(attachment.pk), "sort": 0}]}
    owner.data_json = data
    owner.save(update_fields=["data_json"])
    return attachment


def _ersetze(facility, owner, field_template, user, vorgaenger):
    """Haengt eine zweite Version an dieselbe Kette (Replace-Pfad)."""
    from core.services.file_vault import store_encrypted_file

    upload = SimpleUploadedFile("probe-neu.pdf", PDF_MINIMAL, content_type="application/pdf")
    with patch("core.services.file_vault.storage.run_virus_scan", lambda *a, **kw: None):
        return store_encrypted_file(facility, upload, field_template, owner, user, supersedes=vorgaenger)


# ------------------------------------------------------- Sequenz-Guards ----


@pytest.mark.django_db
class TestSequenzReihenfolge:
    """Die Ordnung am ausgefuehrten SQL — deterministisch, ohne Timing."""

    def _assert_reihenfolge(self, sequence, *, owner_table):
        """Die VOLLSTAENDIGE Folge je Owner-Typ: Person -> Owner -> Anhang -> Protokoll.

        Bei einem Personen-Owner faellt Stufe 1 mit der Owner-Zeile zusammen —
        eine zweite Stufe gibt es dort nicht.
        """
        attachment_at = _first_index(sequence, "core_attachment")
        chain_at = _first_index(sequence, CHAIN)
        owner_at = _first_index(sequence, owner_table)
        assert attachment_at is not None, f"Anhangszeilen wurden nicht gesperrt: {sequence}"
        assert chain_at is not None, f"kein Ketten-Lock im Pfad: {sequence}"
        assert owner_at is not None, f"{owner_table}-Zeile wurde nicht gesperrt: {sequence}"
        if owner_table != "core_client":
            person_at = _first_index(sequence, "core_client")
            assert person_at is not None, f"Personenzeile wurde nicht gesperrt: {sequence}"
            assert person_at < owner_at, f"Personenzeile gehoert vor die Owner-Zeile: {sequence}"
        assert owner_at < attachment_at, f"Owner-Zeile gehoert vor die Anhangszeilen: {sequence}"
        assert attachment_at < chain_at, f"Anhangszeilen gehoeren vor den Ketten-Lock: {sequence}"

    def test_ereignis_update_sperrt_zeilen_vor_dem_ketten_lock(self, client, staff_user, facility, client_identified):
        doc_type, ft_file, ft_text = _doc_type(
            facility, entity_type=DocumentType.EntityType.EVENT, name="Ereignis mit Anhang"
        )
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        attachment = _attach(facility, event, ft_file, staff_user)
        client.force_login(staff_user)

        with CaptureQueriesContext(connection) as captured:
            resp = client.post(
                reverse("core:event_update", kwargs={"pk": event.pk}),
                {
                    "document_type": str(doc_type.pk),
                    "occurred_at": event.occurred_at.strftime("%Y-%m-%dT%H:%M"),
                    ft_text.slug: "geaendert",
                    f"{ft_file.slug}__remove": str(attachment.entry_id),
                },
            )
        assert resp.status_code == 302, resp.content
        self._assert_reihenfolge(_lock_sequence(captured), owner_table="core_event")

    def test_personen_update_sperrt_zeilen_vor_dem_ketten_lock(self, client, staff_user, facility):
        doc_type, ft_file, ft_text = _doc_type(
            facility, entity_type=DocumentType.EntityType.CLIENT, name="Person mit Anhang"
        )
        person = Client.objects.create(
            facility=facility,
            pseudonym="Sperr-Sonde",
            contact_stage=Client.ContactStage.IDENTIFIED,
            document_type=doc_type,
            data_json={},
            notes="alt",
            created_by=staff_user,
        )
        attachment = _attach(facility, person, ft_file, staff_user)
        client.force_login(staff_user)

        with CaptureQueriesContext(connection) as captured:
            resp = client.post(
                reverse("core:client_update", kwargs={"pk": person.pk}),
                {
                    "pseudonym": "Sperr-Sonde",
                    "contact_stage": Client.ContactStage.IDENTIFIED,
                    "age_cluster": Client.AgeCluster.UNKNOWN,
                    "notes": "geaendert",
                    "document_type": str(doc_type.pk),
                    ft_text.slug: "geaendert",
                    f"{ft_file.slug}__remove": str(attachment.entry_id),
                },
            )
        assert resp.status_code == 302, resp.content
        self._assert_reihenfolge(_lock_sequence(captured), owner_table="core_client")

    def test_fall_update_sperrt_zeilen_vor_dem_ketten_lock(self, client, staff_user, facility, client_identified):
        doc_type, ft_file, ft_text = _doc_type(
            facility, entity_type=DocumentType.EntityType.CASE, name="Fall mit Anhang"
        )
        case = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="Sperr-Sonde Fall",
            description="alt",
            document_type=doc_type,
            data_json={},
            created_by=staff_user,
        )
        attachment = _attach(facility, case, ft_file, staff_user)
        client.force_login(staff_user)

        with CaptureQueriesContext(connection) as captured:
            resp = client.post(
                reverse("core:case_update", kwargs={"pk": case.pk}),
                {
                    "client": str(client_identified.pk),
                    "title": "Sperr-Sonde Fall",
                    "description": "geaendert",
                    "lead_user": "",
                    "document_type": str(doc_type.pk),
                    ft_text.slug: "geaendert",
                    f"{ft_file.slug}__remove": str(attachment.entry_id),
                },
            )
        assert resp.status_code == 302, resp.content
        self._assert_reihenfolge(_lock_sequence(captured), owner_table="core_case")

    def test_endgueltiges_loeschen_sperrt_zeilen_vor_dem_ketten_lock(
        self, client, admin_user, facility, client_identified
    ):
        """Der Vernichtungspfad sperrte die Owner-Zeilen bisher ERST im Service
        — also hinter seinem eigenen Audit-Write und damit hinter dem
        Ketten-Lock."""
        doc_type, ft_file, _ft_text = _doc_type(
            facility, entity_type=DocumentType.EntityType.EVENT, name="Ereignis zum Vernichten"
        )
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={},
            created_by=admin_user,
        )
        attachment = _attach(facility, event, ft_file, admin_user)
        client.force_login(admin_user)

        with CaptureQueriesContext(connection) as captured:
            resp = client.post(reverse("core:attachment_delete", kwargs={"pk": attachment.pk}))
        assert resp.status_code == 302, resp.content

        self._assert_reihenfolge(_lock_sequence(captured), owner_table="core_event")

    def test_typwechsel_sperrt_alle_ketten_geordnet_vor_dem_ersten_update(
        self, staff_user, facility, client_identified
    ):
        """Der Typwechsel soft-loescht ALLE Ketten eines Owners in einer
        Schleife. Ohne vorgezogene Sperre legte die Reihenfolge der Ketten die
        Sperrfolge fest — und zwei gleichzeitige Typwechsel desselben Owners
        koennten sich darueber verschraenken.

        Erwartet wird GENAU EINE gebuendelte, nach ``pk`` geordnete Abfrage:
        eine Vorsperre je Kette waere lineares Wachstum und damit der
        Query-Count-Regress, den der Bulk-REMOVE-Pin ausschliesst.
        """
        from core.services.attachments import discard_type_switch_file_chains

        doc_type, ft_file, _ft_text = _doc_type(
            facility, entity_type=DocumentType.EntityType.EVENT, name="Ereignis Typwechsel"
        )
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        _attach(facility, event, ft_file, staff_user)
        zweites_feld = FieldTemplate.objects.create(
            facility=facility, name="Zweiter Anhang Typwechsel", field_type=FieldTemplate.FieldType.FILE
        )
        DocumentTypeField.objects.create(document_type=doc_type, field_template=zweites_feld, sort_order=2)
        _attach(facility, event, zweites_feld, staff_user)

        with transaction.atomic(), CaptureQueriesContext(connection) as captured:
            assert discard_type_switch_file_chains(event, staff_user) == 2

        vorsperren = [
            index
            for index, query in enumerate(captured.captured_queries)
            if _ORDERED_ATTACHMENT_LOCK_RE.search(query["sql"])
        ]
        update_at = _statement_index(captured, lambda sql: sql.startswith('UPDATE "core_attachment"'))
        chain_at = _statement_index(captured, lambda sql: "pg_advisory_xact_lock" in sql)
        assert len(vorsperren) == 1, f"genau eine gebuendelte Vorsperre erwartet, gemessen: {len(vorsperren)}"
        assert update_at is not None, "kein Soft-Delete-UPDATE im Pfad"
        assert chain_at is not None, "kein Ketten-Lock im Pfad"
        assert vorsperren[0] < update_at, "die Vorsperre gehoert vor die erste Aenderung"
        assert vorsperren[0] < chain_at, "die Vorsperre gehoert vor den Ketten-Lock"

    def test_owner_cleanup_sperrt_die_zeilen_geordnet_vor_dem_delete(self, staff_user, facility, client_identified):
        """Der physische Cleanup der Loesch-/Anonymisierungskaskade loeschte
        die Anhangszeilen bisher ohne geordnete Vorsperre."""
        from core.services.file_vault import delete_attachments_for

        doc_type, ft_file, _ft_text = _doc_type(
            facility, entity_type=DocumentType.EntityType.EVENT, name="Ereignis Kaskade"
        )
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        _attach(facility, event, ft_file, staff_user)

        with transaction.atomic(), CaptureQueriesContext(connection) as captured:
            delete_attachments_for(event)

        lock_at = _ordered_attachment_lock_index(captured)
        delete_at = _statement_index(captured, lambda sql: sql.startswith('DELETE FROM "core_attachment"'))
        assert lock_at is not None, "keine nach PK geordnete Vorsperre vor dem physischen Cleanup"
        assert delete_at is not None, "kein Attachment-DELETE im Pfad"
        assert lock_at < delete_at, "die Vorsperre gehoert vor das DELETE"

    def test_personen_update_mit_typentfernung_sperrt_die_person_zuerst(self, client, staff_user, facility):
        """Das Entfernen des Dokumentationstyps ist derselbe Schreibpfad.

        Er verwirft ALLE Ketten der Person (``discard_type_switch_file_chains``)
        und schreibt dafuer eine Bulk-Audit-Zeile — also den Ketten-Lock. Die
        Personenzeile sperrte er bis dahin erst danach, im ``update_client``.
        """
        doc_type, ft_file, _ft_text = _doc_type(
            facility, entity_type=DocumentType.EntityType.CLIENT, name="Person Typentfernung"
        )
        person = Client.objects.create(
            facility=facility,
            pseudonym="Sperr-Sonde Typ",
            contact_stage=Client.ContactStage.IDENTIFIED,
            document_type=doc_type,
            data_json={},
            notes="alt",
            created_by=staff_user,
        )
        _attach(facility, person, ft_file, staff_user)
        client.force_login(staff_user)

        with CaptureQueriesContext(connection) as captured:
            resp = client.post(
                reverse("core:client_update", kwargs={"pk": person.pk}),
                {
                    "pseudonym": "Sperr-Sonde Typ",
                    "contact_stage": Client.ContactStage.IDENTIFIED,
                    "age_cluster": Client.AgeCluster.UNKNOWN,
                    "notes": "geaendert",
                    "document_type": "",
                },
            )
        assert resp.status_code == 302, resp.content
        assert not person.attachments.filter(deleted_at__isnull=True).exists(), "der Typwechsel hat nichts verworfen"
        self._assert_reihenfolge(_lock_sequence(captured), owner_table="core_client")

    def test_fall_update_mit_typentfernung_sperrt_person_und_fall_zuerst(
        self, client, staff_user, facility, client_identified
    ):
        """Dieselbe Luecke am Fall-Pfad — hier mit beiden Stufen der ersten Ordnung."""
        doc_type, ft_file, _ft_text = _doc_type(
            facility, entity_type=DocumentType.EntityType.CASE, name="Fall Typentfernung"
        )
        case = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="Sperr-Sonde Fall Typ",
            description="alt",
            document_type=doc_type,
            data_json={},
            created_by=staff_user,
        )
        _attach(facility, case, ft_file, staff_user)
        client.force_login(staff_user)

        with CaptureQueriesContext(connection) as captured:
            resp = client.post(
                reverse("core:case_update", kwargs={"pk": case.pk}),
                {
                    "client": str(client_identified.pk),
                    "title": "Sperr-Sonde Fall Typ",
                    "description": "geaendert",
                    "lead_user": "",
                    "document_type": "",
                },
            )
        assert resp.status_code == 302, resp.content
        assert not case.attachments.filter(deleted_at__isnull=True).exists(), "der Typwechsel hat nichts verworfen"
        self._assert_reihenfolge(_lock_sequence(captured), owner_table="core_case")

    def test_direkter_soft_delete_sperrt_die_kette_geordnet_vor_dem_audit(
        self, staff_user, facility, client_identified
    ):
        """Eine Kette ist nicht EINE Zeile.

        Der direkte Service-Einstieg markierte alle Versionen mit einem
        ungeordneten ``UPDATE``; dessen Sperrfolge legt der Ausfuehrungsplan
        fest. Ein gleichzeitiger Pfad mit nach ``pk`` geordneter Vorsperre
        (Vernichtung, Batch) kann sich damit verschraenken.
        """
        from core.services.file_vault import soft_delete_attachment_chain

        doc_type, ft_file, _ft_text = _doc_type(
            facility, entity_type=DocumentType.EntityType.EVENT, name="Ereignis Kette"
        )
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        erste_version = _attach(facility, event, ft_file, staff_user)
        _ersetze(facility, event, ft_file, staff_user, erste_version)

        with transaction.atomic(), CaptureQueriesContext(connection) as captured:
            assert soft_delete_attachment_chain(event, erste_version.entry_id, staff_user) == 2

        lock_at = _ordered_attachment_lock_index(captured)
        update_at = _statement_index(captured, lambda sql: sql.startswith('UPDATE "core_attachment"'))
        chain_at = _statement_index(captured, lambda sql: "pg_advisory_xact_lock" in sql)
        assert lock_at is not None, "keine nach PK geordnete Vorsperre vor dem Soft-Delete"
        assert update_at is not None, "kein Soft-Delete-UPDATE im Pfad"
        assert chain_at is not None, "kein Ketten-Lock im Pfad"
        assert lock_at < update_at, "die Vorsperre gehoert vor die Aenderung"
        assert lock_at < chain_at, "die Vorsperre gehoert vor den Ketten-Lock"

    def test_batch_remove_behaelt_genau_eine_gebuendelte_vorsperre(self, staff_user, facility, client_identified):
        """Die Gegenprobe zum Einzelpfad: der Batch sperrt EINMAL gebuendelt.

        Eine Vorsperre je Kette im Einzelpfad waere lineares Wachstum — genau
        der Regress, den der Query-Count-Pin des Bulk-REMOVE ausschliesst.
        """
        from core.services.attachments import apply_attachment_changes

        doc_type, ft_file, _ft_text = _doc_type(
            facility, entity_type=DocumentType.EntityType.EVENT, name="Ereignis Batch"
        )
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        erste = _attach(facility, event, ft_file, staff_user)
        zweite = _attach(facility, event, ft_file, staff_user)
        event.data_json = {
            ft_file.slug: {
                "__files__": True,
                "entries": [{"id": str(erste.pk), "sort": 0}, {"id": str(zweite.pk), "sort": 1}],
            }
        }
        event.save(update_fields=["data_json"])
        entfernen = f"{erste.entry_id},{zweite.entry_id}"

        with transaction.atomic(), CaptureQueriesContext(connection) as captured:
            apply_attachment_changes(
                event,
                staff_user,
                {f"{ft_file.slug}__remove": entfernen},
                {},
                {},
                doc_type,
                staged_by_key={},
            )

        vorsperren = [
            index
            for index, query in enumerate(captured.captured_queries)
            if _ORDERED_ATTACHMENT_LOCK_RE.search(query["sql"])
        ]
        assert len(vorsperren) == 1, f"genau eine gebuendelte Vorsperre erwartet, gemessen: {len(vorsperren)}"

    def test_direkter_replace_sperrt_die_personenzeile_vor_dem_ketten_lock(self, staff_user, facility):
        """Der Replace legt eine NEUE Anhangszeile mit Owner-FK an.

        Deren Fremdschluesselpruefung nimmt eine Sperre auf der Owner-/Personen-
        zeile — beim aufgeschobenen Schluessel erst zum Commit, also HINTER dem
        Ketten-Lock. Ein Pfad, der die Personenzeile haelt und danach den
        Ketten-Lock will (Art.-18-Einschraenkung), schliesst damit den Zyklus.
        """
        doc_type, ft_file, _ft_text = _doc_type(
            facility, entity_type=DocumentType.EntityType.CLIENT, name="Person Replace"
        )
        person = Client.objects.create(
            facility=facility,
            pseudonym="Sperr-Sonde Replace",
            contact_stage=Client.ContactStage.IDENTIFIED,
            document_type=doc_type,
            data_json={},
            created_by=staff_user,
        )
        vorgaenger = _attach(facility, person, ft_file, staff_user)

        # BEWUSST ohne aeussere Transaktion: genau so nimmt dieser Einstieg den
        # Ketten-Lock selbst und als ersten seiner Transaktion — nur davor
        # laesst sich die Owner-Stufe ueberhaupt legen. Mit Aufrufer-
        # Transaktion gilt der Batch-Vertrag (siehe die Guards der Views).
        with CaptureQueriesContext(connection) as captured:
            _ersetze(facility, person, ft_file, staff_user, vorgaenger)

        self._assert_reihenfolge(_lock_sequence(captured), owner_table="core_client")

    def test_direkter_upload_sperrt_die_owner_zeilen_vor_dem_ketten_lock(self, staff_user, facility, client_identified):
        """Dasselbe beim reinen Hinzufuegen: auch die neue Zeile traegt den Owner-FK."""
        doc_type, ft_file, _ft_text = _doc_type(
            facility, entity_type=DocumentType.EntityType.EVENT, name="Ereignis Upload"
        )
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )

        # Ohne aeussere Transaktion — siehe Replace-Guard.
        with CaptureQueriesContext(connection) as captured:
            _attach(facility, event, ft_file, staff_user)

        sequence = _lock_sequence(captured)
        person_at = _first_index(sequence, "core_client")
        owner_at = _first_index(sequence, "core_event")
        chain_at = _first_index(sequence, CHAIN)
        assert person_at is not None, f"Personenzeile wurde nicht gesperrt: {sequence}"
        assert owner_at is not None, f"Ereigniszeile wurde nicht gesperrt: {sequence}"
        assert chain_at is not None, f"kein Ketten-Lock im Pfad: {sequence}"
        assert person_at < owner_at, f"Personenzeile gehoert vor die Ereigniszeile: {sequence}"
        assert owner_at < chain_at, f"die Owner-Zeilen gehoeren vor den Ketten-Lock: {sequence}"

    def test_leerer_batch_sperrt_keine_zeile(self, staff_user, facility):
        """Ein reines Text-Update fasst keine Anhangszeile an — und sperrt nichts.

        Die Vorsperre ist die Ausdehnung des Batch-Vertrags, keine neue
        Konkurrenz: wer nichts ersetzt, loescht oder hinzufuegt, zahlt weiterhin
        nichts.
        """
        from core.services.attachments.crud import lock_attachment_batch_before_audit

        doc_type, _ft_file, _ft_text = _doc_type(
            facility, entity_type=DocumentType.EntityType.CLIENT, name="Person leerer Batch"
        )
        person = Client.objects.create(
            facility=facility,
            pseudonym="Sperr-Sonde leer",
            contact_stage=Client.ContactStage.IDENTIFIED,
            document_type=doc_type,
            data_json={},
            created_by=staff_user,
        )

        with transaction.atomic(), CaptureQueriesContext(connection) as captured:
            lock_attachment_batch_before_audit(person, staff_user, {}, {}, doc_type, data_json={})

        gesperrt = [q["sql"] for q in captured.captured_queries if "FOR UPDATE" in q["sql"].upper()]
        assert not gesperrt, f"der leere Batch hat gesperrt: {gesperrt}"

    def test_batch_mit_neuer_datei_sperrt_die_owner_zeile(self, staff_user, facility):
        """Gegenprobe zum leeren Batch: ein Batch MIT Upload ist nicht leer.

        Er legt eine Anhangszeile mit Owner-FK an (siehe Replace-Guard) und
        braucht die erste Stufe deshalb weiterhin vor dem Ketten-Lock des
        ``update_*``.
        """
        from core.services.attachments.crud import lock_attachment_batch_before_audit

        doc_type, ft_file, _ft_text = _doc_type(
            facility, entity_type=DocumentType.EntityType.CLIENT, name="Person Batch mit Datei"
        )
        person = Client.objects.create(
            facility=facility,
            pseudonym="Sperr-Sonde ADD",
            contact_stage=Client.ContactStage.IDENTIFIED,
            document_type=doc_type,
            data_json={},
            created_by=staff_user,
        )

        with transaction.atomic(), CaptureQueriesContext(connection) as captured:
            # Der Staging-Schluessel eines Zugangs ist der reine Feld-Slug
            # (Ersetzen benutzt ``replace_upload_key``); der Wert spielt fuer
            # die Sperrmenge keine Rolle.
            lock_attachment_batch_before_audit(
                person, staff_user, {}, {ft_file.slug: ["gestagt"]}, doc_type, data_json={}
            )

        assert _first_index(_lock_sequence(captured), "core_client") is not None, (
            "der Batch mit neuer Datei hat die Personenzeile nicht gesperrt"
        )


# ---------------------------------------------------- Rendezvous-Guards ----


def _setze_zeitgrenzen(dbconnection, *, lock_timeout, statement_timeout=None) -> None:
    """Gibt der Verbindung DB-seitige Zeitgrenzen (muss im Autocommit laufen).

    ``set_config(..., is_local=False)`` gilt fuer die Sitzung — im Autocommit
    ueberlebt das jede spaetere Transaktion und jedes ``ROLLBACK`` der
    Sondenschleife. Eine Zeitgrenze im Python-Code kann ein blockierendes
    ``SELECT`` nicht beenden; diese hier kann es.
    """
    assert dbconnection.get_autocommit(), "Zeitgrenzen vor der ersten Transaktion setzen"
    with dbconnection.cursor() as cur:
        cur.execute("SELECT set_config('lock_timeout', %s, false)", [lock_timeout])
        if statement_timeout is not None:
            cur.execute("SELECT set_config('statement_timeout', %s, false)", [statement_timeout])


def _sqlstate(exc):
    """SQLSTATE der DB-Ausnahme hinter einem Django-``OperationalError``."""
    return getattr(getattr(exc, "__cause__", None), "sqlstate", None)


def _bewerte_db_fehler(fehler, *, kontext) -> None:
    """Jeder Fehler wird am SQLSTATE bewertet — nichts gilt stillschweigend als Erfolg."""
    if fehler is None:
        return
    zustand = _sqlstate(fehler)
    if zustand == DEADLOCK:
        pytest.fail(f"Sperr-Zyklus ({kontext}): SQLSTATE {DEADLOCK} — {fehler!r}")
    if zustand in TIMEOUT_SQLSTATES:
        pytest.fail(f"Warten lief in die Zeitgrenze ({kontext}): SQLSTATE {zustand} — Zyklusverdacht, {fehler!r}")
    pytest.fail(f"unerwarteter Fehler ({kontext}): SQLSTATE {zustand} — {fehler!r}")


def _warte_auf_gegenseite(sonde_connection, facility_id, worker_pid, deadline_ends_at) -> str:
    """Barriere fuer die zweite Transaktion — ohne unbegrenztes Warten.

    Die Sonde laeuft auf einer EIGENEN Verbindung und probt genau zwei
    Zustaende, beide praezise statt „irgendein Lock":

    * ``"kette"`` — der Ketten-Advisory-Lock GENAU DIESER Einrichtung ist
      vergeben. Der beobachtete Pfad ist also schon beim Audit-Write und will
      danach die Zeile, die wir halten: die Gegenrichtung, der Zyklus steht.
      Geprobt wird mit ``pg_try_advisory_xact_lock`` auf demselben Schluessel,
      den ``assign_chain_fields`` nimmt; die Probe-Transaktion wird sofort
      zurueckgerollt und haelt selbst nichts.
    * ``"blockiert"`` — GENAU das Backend des Workers wartet auf die
      Transaktion eines anderen (``locktype='transactionid'``), haengt also an
      der Zeile, die wir halten, BEVOR es den Ketten-Lock genommen hat: die
      geforderte Richtung. Die Probe ist an ``worker_pid`` gebunden; eine
      beliebige andere Sitzung im Cluster darf den Test nicht gruen faerben.

    ``"deadline"`` heisst „nichts davon beobachtet" — der Aufrufer behandelt das
    als unschluessig und laesst den Test nicht stillschweigend gruen werden.
    """
    from core.services.audit.chain import advisory_lock_name

    while time.monotonic() < deadline_ends_at:
        with sonde_connection.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_xact_lock(hashtext(%s))", [advisory_lock_name(facility_id)])
            frei = bool(cur.fetchone()[0])
            cur.execute(
                "SELECT count(*) FROM pg_locks WHERE NOT granted AND locktype = 'transactionid' AND pid = %s",
                [worker_pid],
            )
            wartet = cur.fetchone()[0]
        sonde_connection.rollback()
        if not frei:
            return "kette"
        if wartet:
            return "blockiert"
        time.sleep(0.05)
    return "deadline"


class _Gegenrichtung:
    """Zweite Transaktion auf eigener Verbindung: haelt eine Zeile und will
    danach den Ketten-Lock — die Gegenrichtung zur geforderten Ordnung."""

    def __init__(self, facility_id):
        self.facility_id = facility_id
        self.conn = connections.create_connection("default")
        _setze_zeitgrenzen(self.conn, lock_timeout=GEGEN_LOCK_TIMEOUT)
        self.conn.set_autocommit(False)
        self.sonde = connections.create_connection("default")
        _setze_zeitgrenzen(self.sonde, lock_timeout=GEGEN_LOCK_TIMEOUT)
        self.sonde.set_autocommit(False)
        self.fehler = None

    def halte_zeile(self, tabelle, pk):
        with self.conn.cursor() as cur:
            cur.execute(f'SELECT id FROM "{tabelle}" WHERE id = %s FOR UPDATE', [str(pk)])  # noqa: S608 — feste Namen
            cur.fetchall()

    def nimm_ketten_lock(self):
        from core.services.audit.chain import advisory_lock_name

        try:
            with self.conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", [advisory_lock_name(self.facility_id)])
                cur.fetchall()
        except OperationalError as exc:
            self.fehler = exc

    def warte(self, worker_pid, deadline_ends_at):
        return _warte_auf_gegenseite(self.sonde, self.facility_id, worker_pid, deadline_ends_at)

    def schliesse(self):
        """Gibt Zeilensperre und Ketten-Lock sicher frei — auch nach Fehlern."""
        for conn in (self.conn, self.sonde):
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001 — die Verbindung wird ohnehin verworfen
                pass
            conn.close()


def _im_thread(fn):
    """Startet ``fn`` als Thread und sammelt Ergebnis bzw. Ausnahme ein.

    Der Thread meldet zuerst seine Backend-PID (die Warteprobe der Sonde
    braucht sie) und setzt seine DB-seitigen Zeitgrenzen; erst danach laeuft
    ``fn``. Der Thread ist ein Daemon: bleibt er trotz aller Grenzen haengen,
    faellt der Test am ``is_alive``-Check, haelt aber den Testlauf nicht auf.
    """
    ergebnis: dict = {}
    bereit = threading.Event()

    def runner():
        try:
            _setze_zeitgrenzen(
                connection,
                lock_timeout=WORKER_LOCK_TIMEOUT,
                statement_timeout=WORKER_STATEMENT_TIMEOUT,
            )
            with connection.cursor() as cur:
                cur.execute("SELECT pg_backend_pid()")
                ergebnis["pid"] = cur.fetchone()[0]
        except Exception as exc:  # noqa: BLE001 — der Test bewertet die Ausnahme
            ergebnis["fehler"] = exc
            connections.close_all()
            return
        finally:
            bereit.set()
        try:
            ergebnis["wert"] = fn()
        except Exception as exc:  # noqa: BLE001 — der Test bewertet die Ausnahme
            ergebnis["fehler"] = exc
        finally:
            connections.close_all()

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    assert bereit.wait(RENDEZVOUS_DEADLINE), "Worker-Thread hat seine Backend-PID nicht gemeldet"
    assert "pid" in ergebnis, f"Worker-Thread kam nicht bis zur Backend-PID: {ergebnis.get('fehler')!r}"
    return thread, ergebnis


@pytest.mark.django_db(transaction=True)
def test_ereignis_update_bildet_keinen_zyklus_mit_gehaltener_anhangszeile(
    client, staff_user, facility, client_identified
):
    """Gegenrichtung 1: eine fremde Transaktion haelt eine Anhangszeile und
    will danach den Ketten-Lock.

    Nimmt der Update-Pfad seinerseits erst den Ketten-Lock (Audit) und wartet
    danach auf die Anhangszeile, schliesst sich der Zyklus und PostgreSQL
    beendet einen der beiden mit ``40P01``.
    """
    doc_type, ft_file, ft_text = _doc_type(
        facility, entity_type=DocumentType.EntityType.EVENT, name="Ereignis Rendezvous"
    )
    event = Event.objects.create(
        facility=facility,
        client=client_identified,
        document_type=doc_type,
        occurred_at=timezone.now(),
        data_json={},
        created_by=staff_user,
    )
    attachment = _attach(facility, event, ft_file, staff_user)
    client.force_login(staff_user)

    gegen = _Gegenrichtung(facility.pk)
    thread = None
    ergebnis: dict = {}
    try:
        gegen.halte_zeile("core_attachment", attachment.pk)

        def pfad():
            return client.post(
                reverse("core:event_update", kwargs={"pk": event.pk}),
                {
                    "document_type": str(doc_type.pk),
                    "occurred_at": event.occurred_at.strftime("%Y-%m-%dT%H:%M"),
                    ft_text.slug: "geaendert",
                    f"{ft_file.slug}__remove": str(attachment.entry_id),
                },
            )

        thread, ergebnis = _im_thread(pfad)
        beobachtung = gegen.warte(ergebnis["pid"], time.monotonic() + RENDEZVOUS_DEADLINE)
        gegen.nimm_ketten_lock()
    finally:
        gegen.schliesse()
        if thread is not None:
            thread.join(timeout=RENDEZVOUS_DEADLINE)

    assert thread is not None and not thread.is_alive(), "Worker-Thread laeuft nach der Deadline weiter"
    assert beobachtung != "deadline", "Rendezvous unschluessig — weder Ketten-Lock noch Zeilenwarten beobachtet"
    _bewerte_db_fehler(gegen.fehler, kontext="fremde Transaktion")
    _bewerte_db_fehler(ergebnis.get("fehler"), kontext="Update-Pfad")
    assert ergebnis.get("wert") is not None and ergebnis["wert"].status_code == 302, (
        f"Update-Pfad ist nicht erfolgreich durchgelaufen: {ergebnis.get('wert')!r}"
    )


@pytest.mark.django_db(transaction=True)
def test_endgueltiges_loeschen_bildet_keinen_zyklus_mit_gehaltener_personenzeile(
    client, admin_user, facility, client_identified
):
    """Gegenrichtung 2: eine fremde Transaktion haelt die Personenzeile und
    will danach den Ketten-Lock.

    Der Vernichtungspfad schrieb bisher erst sein Audit (Ketten-Lock) und
    sperrte die Personenzeile erst danach im Service — genau die Umkehrung.
    """
    doc_type, ft_file, _ft_text = _doc_type(
        facility, entity_type=DocumentType.EntityType.EVENT, name="Ereignis Rendezvous Purge"
    )
    event = Event.objects.create(
        facility=facility,
        client=client_identified,
        document_type=doc_type,
        occurred_at=timezone.now(),
        data_json={},
        created_by=admin_user,
    )
    attachment = _attach(facility, event, ft_file, admin_user)
    client.force_login(admin_user)

    gegen = _Gegenrichtung(facility.pk)
    thread = None
    ergebnis: dict = {}
    try:
        gegen.halte_zeile("core_client", client_identified.pk)

        def pfad():
            return client.post(reverse("core:attachment_delete", kwargs={"pk": attachment.pk}))

        thread, ergebnis = _im_thread(pfad)
        beobachtung = gegen.warte(ergebnis["pid"], time.monotonic() + RENDEZVOUS_DEADLINE)
        gegen.nimm_ketten_lock()
    finally:
        gegen.schliesse()
        if thread is not None:
            thread.join(timeout=RENDEZVOUS_DEADLINE)

    assert thread is not None and not thread.is_alive(), "Worker-Thread laeuft nach der Deadline weiter"
    assert beobachtung != "deadline", "Rendezvous unschluessig — weder Ketten-Lock noch Zeilenwarten beobachtet"
    _bewerte_db_fehler(gegen.fehler, kontext="fremde Transaktion")
    _bewerte_db_fehler(ergebnis.get("fehler"), kontext="Vernichtungspfad")
    assert ergebnis.get("wert") is not None and ergebnis["wert"].status_code == 302, (
        f"Vernichtungspfad ist nicht erfolgreich durchgelaufen: {ergebnis.get('wert')!r}"
    )

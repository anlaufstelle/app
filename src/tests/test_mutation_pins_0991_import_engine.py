"""Pin-Tests des Mutations-Delta-Checkpoints der Welle M (release-0.99.1) zu
``core.services.import_engine``.

Quelle: Ticket #2322 (#2322),
Survivor-Triage des Checkpoint-Deltas ``6435c18e`` (BASE v0.99.0). Gepinnt
werden die fuenf Survivor dieses Moduls: die FK-Vorsperre der Jobzeile
(``_lock_job_fk_rows``, Refs #2259/#2260 — ``continue``-vs-``break`` beim
Ueberspringen einer leeren FK und die Sortierreihenfolge Tabelle-vor-PK) sowie
zwei Stellen in ``store_import_upload`` (die Akteurs-Weitergabe an den
Kontingent-Backstop und ``missing_ok`` beim Cleanup einer bereits entfernten
Ausgabedatei im Backstop-Fehlerpfad).

Konvention wie ``src/tests/test_mutation_pins_094_attachments_crud.py``:
echte Model-Objekte, Zusicherungen exakt; die Kontingent-Ausnahme wird per
Monkeypatch des modul-lokalen Alias ausgeloest (Muster
``src/tests/test_mutation_pins_099_neu_1.py::TestCommitStagedUploadQuotaBackstopAttribution``)
statt ueber ein echtes Kontingent-Limit — das haelt den Test schnell und
deterministisch, ohne den Backstop-Mechanismus selbst neu zu pruefen (der ist
anderswo gepinnt).
"""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection

from core.models import DocumentType, Facility, ImportJob
from core.services import import_engine
from core.services.file_vault.policy import StorageQuotaBackstopError
from core.services.file_vault.quota import StorageUsage
from core.services.import_engine import _import_dir, _lock_job_fk_rows

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def _upload(content, name="import.csv"):
    return SimpleUploadedFile(name, content.encode("utf-8"), content_type="text/csv")


class _Mitschnitt(list):
    def wrapper(self, execute, sql, params, many, context):
        self.append((sql, params))
        return execute(sql, params, many, context)


def _key_share_queries(mitschnitt):
    return [(sql, params) for sql, params in mitschnitt if "FOR KEY SHARE" in sql.upper()]


@pytest.fixture(autouse=True)
def medienordner(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    return tmp_path


class TestLockJobFkRowsSkipVsAbort:
    def test_leere_fk_wird_uebersprungen_nicht_die_pruefung_abgebrochen(self, facility, doc_type_contact):
        """Pin: ``x__lock_job_fk_rows__mutmut_13`` — bei einem leeren FK
        (``wert is None``) wird die Schleife per ``break`` abgebrochen statt
        nur dieses eine Feld per ``continue`` zu ueberspringen.

        ``job`` hat ``created_by=None`` (zulaessig, nullable), aber ein
        gesetztes ``document_type``. Feldreihenfolge im Modell: ``facility``,
        ``created_by``, ``document_type``. Original: ``facility`` UND
        ``document_type`` werden gesperrt (``created_by`` uebersprungen).
        Mutant: die Schleife bricht bei ``created_by`` ab — ``document_type``
        wird NIE gesperrt.
        """
        job = ImportJob.objects.create(
            facility=facility,
            created_by=None,
            target_type=ImportJob.TargetType.EVENT,
            document_type=doc_type_contact,
        )

        mitschnitt = _Mitschnitt()
        with connection.execute_wrapper(mitschnitt.wrapper):
            _lock_job_fk_rows(job)

        treffer = _key_share_queries(mitschnitt)
        tabellen = {sql for sql, _params in treffer}
        assert len(treffer) == 2
        assert any("core_facility" in sql for sql in tabellen)
        assert any("core_documenttype" in sql for sql in tabellen)


class TestLockJobFkRowsSortOrder:
    def test_sperrreihenfolge_richtet_sich_nach_der_tabelle_nicht_nach_der_pk(self, organization, staff_user):
        """Pin: ``x__lock_job_fk_rows__mutmut_21`` — der Sortierschluessel
        wechselt von ``(tabelle, str(wert))`` auf ``(spalte, str(wert))``. Die
        FK-Zielspalte heisst bei JEDEM referenzierten Modell ``id`` — sortiert
        nach Spalte, entscheidet effektiv nur noch der rohe PK-Wert ueber die
        Tabellen-Grenzen hinweg statt des Tabellennamens.

        Feste PKs bewusst GEGENLAEUFIG gewaehlt: ``facility`` traegt die
        LEXIKALISCH KLEINERE PK, ``document_type`` die GROESSERE — nach
        Tabellenname kommt ``core_documenttype`` vor ``core_facility``
        (``d`` < ``f``), nach PK-Wert waere es umgekehrt.

        Abweichung beim Mutanten: die ERSTE ausgefuehrte ``FOR KEY
        SHARE``-Abfrage betrifft ``core_facility`` statt ``core_documenttype``.
        """
        import uuid

        facility_klein_pk = Facility.objects.create(
            id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            organization=organization,
            name="Pins-0991-Facility",
        )
        doc_type_gross_pk = DocumentType.objects.create(
            id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
            facility=facility_klein_pk,
            name="Pins-0991-DocType",
        )
        job = ImportJob.objects.create(
            facility=facility_klein_pk,
            created_by=None,
            target_type=ImportJob.TargetType.EVENT,
            document_type=doc_type_gross_pk,
        )

        mitschnitt = _Mitschnitt()
        with connection.execute_wrapper(mitschnitt.wrapper):
            _lock_job_fk_rows(job)

        treffer = _key_share_queries(mitschnitt)
        assert len(treffer) == 2
        erste_sql, _params = treffer[0]
        assert "core_documenttype" in erste_sql


class TestStoreImportUploadQuotaBackstop:
    def test_akteur_fliesst_in_den_kontingent_backstop(self, monkeypatch, facility, staff_user):
        """Pin: ``x_store_import_upload__mutmut_77`` — der an
        ``check_import_storage_quota_at_commit`` weitergereichte ``user``
        wird zu ``None``. Laut Docstring der Funktion geht ``user`` NUR in die
        Audit-Attribution der ``StorageQuotaBackstopError``-Ausnahme ein —
        mit ``None`` verliert die ``ATTACHMENT_QUOTA_EXCEEDED``-Zeile ihre
        handelnde Person.
        """
        calls = []
        monkeypatch.setattr(
            import_engine,
            "check_import_storage_quota_at_commit",
            lambda facility_arg, user_arg, size: calls.append(user_arg),
        )

        job = import_engine.store_import_upload(
            facility,
            staff_user,
            _upload("Pseudonym;Notizen\nIMP-01;Testzeile\n"),
            target_type=ImportJob.TargetType.CLIENT,
        )

        assert calls == [staff_user]
        assert job.pk is not None

    def _boom_mit_bereits_entfernter_datei(self, facility, user, additional_bytes):
        """Simuliert den Backstop-Fehlerpfad MIT bereits entfernter Ausgabedatei
        (Ticket-Pin-Idee: "eine bereits entfernte Ausgabedatei simulieren")."""
        for pfad in _import_dir(facility).glob("*.enc"):
            pfad.unlink()
        raise StorageQuotaBackstopError(
            facility=facility,
            user=user,
            owner=None,
            usage=StorageUsage(used_bytes=10_000_000, trash_bytes=0, limit_bytes=1_000_000),
            requested_bytes=additional_bytes,
            target_type="ImportJob",
        )

    def test_unlink_maskiert_die_backstop_ausnahme_nicht(self, monkeypatch, facility, staff_user):
        """Pin: ``x_store_import_upload__mutmut_82`` (``missing_ok=None``) und
        ``…__mutmut_83`` (``missing_ok=False``).

        Die verschluesselte Ausgabedatei ist zum Zeitpunkt des
        ``except StorageQuotaBackstopError``-Blocks bereits entfernt
        (Konkurrenz-/Retry-Fall). Original: ``unlink(missing_ok=True)``
        bleibt still, ``StorageQuotaBackstopError`` propagiert unveraendert.
        Mutant: ``unlink`` mit ``missing_ok`` falsy wirft
        ``FileNotFoundError`` und ERSETZT die fachliche Ausnahme.
        """
        monkeypatch.setattr(
            import_engine, "check_import_storage_quota_at_commit", self._boom_mit_bereits_entfernter_datei
        )

        with pytest.raises(StorageQuotaBackstopError):
            import_engine.store_import_upload(
                facility,
                staff_user,
                _upload("Pseudonym;Notizen\nIMP-01;Testzeile\n"),
                target_type=ImportJob.TargetType.CLIENT,
            )

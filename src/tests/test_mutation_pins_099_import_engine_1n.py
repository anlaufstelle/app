"""Pin-Tests der Welle L (0.99) — Vollmutations-Nachtrag in
``core.services.import_engine`` (Batch ``import_engine-1n``).

Refs https://github.com/anlaufstelle/app/issues/2174.
Refs https://github.com/anlaufstelle/app/issues/2193.

Quelle: Re-Messung nach Harness-Fix auf main@3a43e8cd (frischer Mutantenbaum),
Batch ``import_engine-1n`` (EIN Survivor: ``x_abort_import__mutmut_14``).

``abort_import``s finaler Save lief in der VORANGEGANGENEN Klassifikation
(``test_mutation_pins_099_import_engine_1.py``) noch als ``equivalent``, weil
der Save INNERHALB der ``with transaction.atomic(): _lock_job(job); ...``
-Klammer steht und ``updated_at`` in der Feldliste enthalten ist. Diese
Begründung übersieht jedoch, dass ``_lock_job`` NUR eine feste Teilmenge der
Spalten synchronisiert (``status``, ``result_json``, ``storage_filename``,
``original_filename_encrypted``, ``mapping_json``, ``document_type_id``,
``target_type`` — siehe Docstring von ``_lock_job``); alle ÜBRIGEN Spalten der
Instanz — allen voran ``created_by`` — bleiben der View-Snapshot des
Aufrufers, selbst UNTER dem Zeilen-Lock. Die Regel fuer nicht-frisch
geladene Zeilen greift damit:
die Zeile ist zwar gesperrt, aber NICHT vollständig frisch geladen. Ein
voller Save (``update_fields=None``) der Mutante schreibt diese veralteten
Spalten zurück und annulliert eine währenddessen erfolgte Neuzuordnung des
Laufs (Lost Update) — Klasse ``test``, Prio 2 (fachliches Verhalten,
Bearbeiter-Zuordnung).

Konventionen wie ``test_mutation_pins_099_import_engine_1.py``: Zusicherungen
vergleichen EXAKT (``==``); ``pytestmark`` setzt ``django_db`` +
``_encryption_key`` modulweit (der Mutantenbaum hat keine ``.env`` und damit
keinen ``ENCRYPTION_KEY``); Fixtures aus ``src/tests/conftest.py``.
"""

import pytest

from core.models import ImportJob
from core.services import import_engine

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


class TestAbortImportUpdateFields:
    """Der finale ``.save(update_fields=[...])`` in ``abort_import`` listet
    bewusst nur die vier tatsaechlich geänderten Spalten. ``_lock_job``
    synchronisiert ``created_by`` NICHT — ein voller Save schriebe den
    In-Memory-Stand des Aufrufers (den Zeitpunkt VOR dem Aufruf) auf diese
    Spalte zurueck.
    """

    def test_abort_does_not_clobber_a_concurrently_reassigned_owner(self, import_job, admin_user, staff_user):
        """Pin: ``x_abort_import__mutmut_14`` — ``update_fields=["storage_filename",
        "original_filename_encrypted", "status", "updated_at"]`` -> ``update_fields=None``.

        Waehrend/vor dem Abbruch wird der Lauf nebenlaeufig einem anderen
        Bearbeiter zugeordnet (``created_by``-Spalte, von ``_lock_job`` NICHT
        synchronisiert). Original: der Save listet nur die vier Felder, die
        Neuzuordnung bleibt erhalten. Mutant: der volle Save schreibt die
        In-Memory-Instanz (``created_by_id`` noch der urspruengliche Anleger)
        zurueck und annulliert die nebenlaeufige Neuzuordnung.
        """
        assert import_job.created_by_id == admin_user.pk

        ImportJob.objects.filter(pk=import_job.pk).update(created_by=staff_user)

        import_engine.abort_import(import_job, admin_user)

        import_job.refresh_from_db()
        assert import_job.created_by_id == staff_user.pk

"""Follow-Up-Tests fuer Mutation-Survivors in ``core.services.import_engine`` (Refs #1998).

Die 12 Posten aus #1998 haben ein gemeinsames systemisches Muster: mehrere
Funktionen im Modul entscheiden je Schleifendurchlauf zwischen "diesen
Eintrag ueberspringen" (``continue``) und "verarbeiten/uebernehmen". Ein
Mutant, der ``continue`` durch ``break`` ersetzt, faellt nur auf, wenn der
zu ueberspringende Eintrag NICHT der letzte in der Iteration ist - stand er
am Ende, wirken ``continue`` und ``break`` identisch. Die Tests hier mischen
deshalb bewusst uebersprungene und zu verarbeitende Eintraege, statt den
Skip-Fall ans Ende zu sortieren.

Stil-/Fixture-Vorbild: ``test_import_engine.py``. Jeder Test benennt die
konkret gekillte Mutation.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from core.constants import DATA_JSON_PREFIX
from core.models import AuditLog, Client, Event, EventHistory, FieldTemplate, ImportJob
from core.services import import_engine


def _upload(content, name="import.csv", encoding="utf-8"):
    """CSV als hochgeladene Datei — Bytes wie sie der Browser schickt."""
    raw = content.encode(encoding) if isinstance(content, str) else content
    return SimpleUploadedFile(name, raw, content_type="text/csv")


@pytest.fixture
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# Posten 1 — _coerce_boolean Negativpfad
# ---------------------------------------------------------------------------


class TestCoerceBooleanNegativePath:
    @pytest.mark.parametrize(
        "raw",
        ["falsch", "no", "0", "nein", "N", "False"],
        ids=["falsch", "no", "0", "nein", "N-gross", "False-gemischt"],
    )
    def test_recognized_false_values_return_false(self, raw):
        """Pinnt mutmut_5 (``in``/``not in``-Vertauschung bei ``_FALSE_VALUES``)
        und mutmut_6 (``return False`` -> ``return True``). Bislang deckte nur
        der Ja-Pfad (``test_yes_no_becomes_boolean`` in ``test_import_engine.py``)
        ueberhaupt einen Boolean-Wert ab — der Nein-Pfad war ungeprueft.

        Refs #1998.
        """
        assert import_engine._coerce_boolean(raw, "Einwilligung") is False

    def test_garbage_value_raises_invalid_boolean(self):
        """Weder Ja- noch Nein-Wortschatz muss ``_CellError('invalid_boolean')``
        werfen — Gegenstueck zum Negativpfad oben. Refs #1998.
        """
        with pytest.raises(import_engine._CellError) as exc_info:
            import_engine._coerce_boolean("vielleicht", "Einwilligung")
        assert exc_info.value.code == "invalid_boolean"
        assert exc_info.value.column == "Einwilligung"


# ---------------------------------------------------------------------------
# Posten 2 — _coerce_option: continue-vs-break + is_active-Default + Slug
# ---------------------------------------------------------------------------


class TestCoerceOption:
    @staticmethod
    def _template(options):
        return SimpleNamespace(options_json=options)

    def test_match_survives_a_preceding_inactive_option(self):
        """options_json = [inaktiv 'a', aktiv 'b'] und Zellwert 'b' — muss
        trotz vorangehender inaktiver Option matchen (continue-vs-break,
        mutmut_11). Ein ``break`` an der ``is_active``-Pruefung wuerde den
        Scan bei 'a' abbrechen und 'b' nie erreichen. Refs #1998.
        """
        template = self._template(
            [
                {"slug": "a", "is_active": False},
                {"slug": "b", "label": "B", "is_active": True},
            ]
        )
        assert import_engine._coerce_option(template, "b", "Zugang") == "b"

    def test_inactive_option_is_rejected_even_by_its_exact_slug(self):
        """Eine WIRKLICH inaktive Option darf nicht matchen, auch nicht per
        exaktem Slug (is_active-Schluessel, mutmut_8/_9 — ``not`` an der
        Aktiv-Pruefung entfernt wuerde Aktiv/Inaktiv vertauschen). Refs #1998.
        """
        template = self._template([{"slug": "d", "label": "D-Label", "is_active": False}])
        with pytest.raises(import_engine._CellError) as exc_info:
            import_engine._coerce_option(template, "d", "Zugang")
        assert exc_info.value.code == "invalid_option"

    def test_default_is_active_true_when_key_is_absent(self):
        """Optionen ohne ``is_active``-Schluessel gelten als aktiv (Default
        ``True``, mutmut_4). Reihenfolge gemischt: die schluessellose Option
        steht NACH einer explizit aktiven, damit ein falscher Default nicht
        zufaellig durch die erste Option verdeckt wird. Refs #1998.
        """
        template = self._template(
            [
                {"slug": "vorab", "is_active": True},
                {"slug": "e"},
            ]
        )
        assert import_engine._coerce_option(template, "e", "Zugang") == "e"

    def test_exact_slug_matches_even_when_it_differs_from_the_label(self):
        """Zellwert = exakter SLUG (nicht Label) muss matchen, auch wenn der
        Slug ``casefold``-verschieden vom Label ist. Refs #1998.
        """
        template = self._template([{"slug": "c-slug", "label": "Ganz anderer Anzeigename", "is_active": True}])
        assert import_engine._coerce_option(template, "c-slug", "Zugang") == "c-slug"


# ---------------------------------------------------------------------------
# Posten 3 — _collect_data_json: or/and-Vertauschung + continue-vs-break
# ---------------------------------------------------------------------------


class TestCollectDataJson:
    def test_first_empty_target_is_skipped_and_second_value_is_kept(self, monkeypatch):
        """Zwei optionale data_json-Ziele in einer Zeile — das ERSTE
        (Spaltenreihenfolge) leer, das ZWEITE mit Wert. Ausserdem steht VOR
        beiden ein Nicht-data_json-Ziel (``pseudonym``), damit ein
        faelschliches ``break`` bei ``target.startswith(DATA_JSON_PREFIX)``
        (mutmut_13) den kompletten Rest der Schleife verschluckt — nicht nur
        das erste data_json-Ziel.

        Ein ``or``->``and``-Tausch bei ``template is None or raw == ""``
        (mutmut_9/_12) wuerde beim ersten (leeren) Ziel NICHT ueberspringen,
        weil ``template is None`` dort False ist — die Koerzierung liefe mit
        einem Leerstring und der Rueckgabewert triege einen zusaetzlichen,
        falschen Schluessel. Refs #1998.
        """
        templates = {
            "wohnsituation": SimpleNamespace(slug="wohnsituation", field_type=FieldTemplate.FieldType.TEXT),
            "betrag": SimpleNamespace(slug="betrag", field_type=FieldTemplate.FieldType.NUMBER),
        }
        monkeypatch.setattr(import_engine, "_dynamic_templates", lambda job: templates)

        values = {
            "pseudonym": "P-1",
            f"{DATA_JSON_PREFIX}wohnsituation": "",
            f"{DATA_JSON_PREFIX}betrag": "12,50",
        }
        columns = {
            "pseudonym": "Pseudonym",
            f"{DATA_JSON_PREFIX}wohnsituation": "Wohnsituation",
            f"{DATA_JSON_PREFIX}betrag": "Betrag",
        }

        data = import_engine._collect_data_json(job=object(), values=values, columns=columns)

        assert data == {"betrag": 12.5}


# ---------------------------------------------------------------------------
# Posten 4 — apply_mapping und _row_values: continue-vs-break
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestApplyMappingAndRowValuesSkipUnmappedColumns:
    def test_apply_mapping_keeps_a_later_target_after_an_unmapped_column(self, facility, admin_user, media_root):
        """Die nicht zugeordnete Spalte 'Kontaktstufe' steht bewusst ZWISCHEN
        zwei zugeordneten Spalten (nicht am Ende!) — ein ``break`` statt
        ``continue`` bei leerem Zielwert (mutmut_17) wuerde die Zuordnung von
        'Notizen' verschlucken, obwohl kein Fehler auftritt. Refs #1998.
        """
        job = import_engine.store_import_upload(
            facility,
            admin_user,
            _upload("Pseudonym;Kontaktstufe;Notizen\nMAP-01;irrelevant;Eine Notiz\n"),
            target_type=ImportJob.TargetType.CLIENT,
        )

        import_engine.apply_mapping(job, {"Pseudonym": "pseudonym", "Kontaktstufe": "", "Notizen": "notes"})

        job.refresh_from_db()
        assert job.mapping_json["columns"] == {"Pseudonym": "pseudonym", "Notizen": "notes"}

    def test_row_values_keeps_a_later_target_after_an_unmapped_column(self):
        """Gleiches Muster fuer ``_row_values`` (mutmut_7, reiner Helfer ohne
        DB): 'Kontaktstufe' ist NICHT in ``columns_map`` und steht vor
        'Notizen'. Refs #1998.
        """
        header = ["Pseudonym", "Kontaktstufe", "Notizen"]
        row = ["MAP-02", "wird-nicht-gemappt", "Zweite Notiz"]
        columns_map = {"Pseudonym": "pseudonym", "Notizen": "notes"}

        values, columns = import_engine._row_values(header, row, columns_map)

        assert values == {"pseudonym": "MAP-02", "notes": "Zweite Notiz"}
        assert columns == {"pseudonym": "Pseudonym", "notes": "Notizen"}


# ---------------------------------------------------------------------------
# Posten 5 — _lock_job / commit_import / abort_import: pk-Filter
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestLockJobScopesToTheGivenPrimaryKey:
    @staticmethod
    def _client_job(facility, admin_user, pseudonym):
        job = import_engine.store_import_upload(
            facility,
            admin_user,
            _upload(f"Pseudonym\n{pseudonym}\n"),
            target_type=ImportJob.TargetType.CLIENT,
        )
        import_engine.apply_mapping(job, {"Pseudonym": "pseudonym"})
        return job

    def test_abort_targets_only_the_second_job_not_the_first(self, facility, admin_user, media_root):
        """Zwei ImportJob-Zeilen in DERSELBEN Facility — ``abort_import`` auf
        der ZWEITEN darf die ERSTE nicht anfassen (fehlender pk-Filter in
        ``_lock_job``, mutmut_4). Bislang legte kein Test zwei Laeufe pro
        Facility an. Refs #1998.
        """
        job_first = self._client_job(facility, admin_user, "LOCK-01")
        job_second = self._client_job(facility, admin_user, "LOCK-02")

        import_engine.abort_import(job_second, admin_user)

        job_first.refresh_from_db()
        job_second.refresh_from_db()
        assert job_second.status == ImportJob.Status.ABORTED
        assert job_first.status == ImportJob.Status.MAPPED

    def test_commit_targets_only_the_second_job_not_the_first(self, facility, admin_user, media_root):
        """Gleiches Muster fuer ``commit_import``: die ZWEITE Zeile wird
        geprueft, gesperrt und uebernommen — die ERSTE bleibt unveraendert.
        Refs #1998.
        """
        job_first = self._client_job(facility, admin_user, "LOCK-03")
        job_second = self._client_job(facility, admin_user, "LOCK-04")
        import_engine.run_import(job_second, admin_user, commit=False)

        import_engine.commit_import(job_second, admin_user)

        job_first.refresh_from_db()
        job_second.refresh_from_db()
        assert job_second.status == ImportJob.Status.COMMITTED
        assert job_first.status == ImportJob.Status.MAPPED
        assert Client.objects.filter(pseudonym="LOCK-04").exists()


# ---------------------------------------------------------------------------
# Posten 6 — _read_upload: kein Voll-Puffern bei Ueberlaenge
# ---------------------------------------------------------------------------


class TestReadUploadDoesNotBufferOversizedFiles:
    class _RefusingRead:
        """Fake-Upload: korrekte ``.size``, ``.read()`` darf NIE aufgerufen werden."""

        def __init__(self, size):
            self.size = size
            self.read_called = False

        def read(self, *args, **kwargs):
            self.read_called = True
            raise AssertionError("`.read()` haette wegen der Groessenpruefung nie aufgerufen werden duerfen")

    def test_oversized_file_is_rejected_before_being_read_into_memory(self):
        """Isolierter Spion-Test: der Memory-DoS-Schutz muss VOR dem
        Voll-Puffern greifen — sonst kann er stillschweigend verschwinden,
        ohne dass ein Test es merkt (bisherige Tests pruefen nur den
        ValidationError, nicht ob ``.read()`` ueberhaupt vermieden wird).
        Refs #1998.
        """
        fake = self._RefusingRead(size=import_engine.MAX_BYTES + 1)

        with pytest.raises(ValidationError):
            import_engine._read_upload(fake)

        assert fake.read_called is False


# ---------------------------------------------------------------------------
# Posten 7 — cleanup_stale_import_jobs: Rueckgabewert = echte Anzahl
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCleanupStaleImportJobsReturnValue:
    @staticmethod
    def _stale_job(facility, admin_user, pseudonym):
        job = import_engine.store_import_upload(
            facility,
            admin_user,
            _upload(f"Pseudonym\n{pseudonym}\n"),
            target_type=ImportJob.TargetType.CLIENT,
        )
        import_engine.apply_mapping(job, {"Pseudonym": "pseudonym"})
        ImportJob.objects.filter(pk=job.pk).update(
            created_at=timezone.now() - timedelta(days=import_engine.STALE_DAYS + 1)
        )
        job.refresh_from_db()
        return job

    def test_return_value_counts_every_cleaned_up_job_not_just_one(self, facility, admin_user, media_root):
        """ZWEI veraltete ImportJobs in einem Facility-Lauf — Rueckgabewert
        muss 2 sein (``count += 1`` vs. ``count = 1``). Bisherige Tests legten
        je Lauf immer nur EINEN veralteten ImportJob an; ein ``count = 1``
        waere dort nie aufgefallen. Refs #1998.
        """
        job_a = self._stale_job(facility, admin_user, "STALE-A")
        job_b = self._stale_job(facility, admin_user, "STALE-B")

        result = import_engine.cleanup_stale_import_jobs(facility)

        assert result == 2
        job_a.refresh_from_db()
        job_b.refresh_from_db()
        assert job_a.status == ImportJob.Status.ABORTED
        assert job_b.status == ImportJob.Status.ABORTED


# ---------------------------------------------------------------------------
# Posten 8 — uebergebener User wird tatsaechlich persistiert
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestActingUserIsActuallyPersistedNotJustExistence:
    """Bisherige Tests pruefen nach commit_import/abort_import/Zeilen-Import
    nur Existenz bzw. Summen (z. B. ``AuditLog.objects.filter(...).exists()``).
    Eine vertauschte Feldzuweisung — etwa ``user=job.created_by`` statt dem
    tatsaechlich uebergebenen ``user`` — waere so nie aufgefallen. Jeder Test
    hier ruft die Funktion mit einem User auf, der NICHT der Job-Ersteller
    ist, und prueft das konkrete Feld. Refs #1998.
    """

    def test_run_import_client_row_attributes_to_the_passed_in_user(
        self, facility, admin_user, staff_user, media_root
    ):
        job = import_engine.store_import_upload(
            facility,
            admin_user,
            _upload("Pseudonym\nACT-01\n"),
            target_type=ImportJob.TargetType.CLIENT,
        )
        import_engine.apply_mapping(job, {"Pseudonym": "pseudonym"})

        import_engine.run_import(job, staff_user, commit=True)

        client = Client.objects.get(facility=facility, pseudonym="ACT-01")
        assert client.created_by == staff_user
        assert client.created_by != admin_user
        entry = AuditLog.objects.get(action=AuditLog.Action.CLIENT_CREATE, target_id=str(client.pk))
        assert entry.user == staff_user
        assert entry.facility == facility

    def test_run_import_event_row_attributes_to_the_passed_in_user(
        self, facility, admin_user, staff_user, doc_type_contact, client_identified, media_root
    ):
        job = import_engine.store_import_upload(
            facility,
            admin_user,
            _upload("Datum;Person\n01.03.2026 09:30;Test-ID-01\n"),
            target_type=ImportJob.TargetType.EVENT,
            document_type=doc_type_contact,
        )
        import_engine.apply_mapping(job, {"Datum": "occurred_at", "Person": "client_pseudonym"})

        import_engine.run_import(job, staff_user, commit=True)

        event = Event.objects.get(client=client_identified, document_type=doc_type_contact)
        assert event.created_by == staff_user
        history = EventHistory.objects.get(event=event, action=EventHistory.Action.CREATE)
        assert history.changed_by == staff_user
        entry = AuditLog.objects.get(action=AuditLog.Action.EVENT_CREATE, target_id=str(event.pk))
        assert entry.user == staff_user
        assert entry.facility == facility

    def test_commit_import_audit_entry_carries_the_passed_in_user(self, facility, admin_user, staff_user, media_root):
        job = import_engine.store_import_upload(
            facility,
            admin_user,
            _upload("Pseudonym\nACT-02\n"),
            target_type=ImportJob.TargetType.CLIENT,
        )
        import_engine.apply_mapping(job, {"Pseudonym": "pseudonym"})
        import_engine.run_import(job, staff_user, commit=False)

        import_engine.commit_import(job, staff_user)

        entry = AuditLog.objects.filter(action=AuditLog.Action.IMPORT_COMMITTED).latest("timestamp")
        assert entry.user == staff_user
        assert entry.facility == facility

    def test_abort_import_audit_entry_carries_the_passed_in_user(self, facility, admin_user, staff_user, media_root):
        job = import_engine.store_import_upload(
            facility,
            admin_user,
            _upload("Pseudonym\nACT-03\n"),
            target_type=ImportJob.TargetType.CLIENT,
        )

        import_engine.abort_import(job, staff_user)

        entry = AuditLog.objects.filter(action=AuditLog.Action.IMPORT_ABORTED).latest("timestamp")
        assert entry.user == staff_user
        assert entry.facility == facility

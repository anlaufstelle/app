"""Pin-Tests der Welle L (0.99, Refs #2174 / #2193) — ``core.services.case.handover_note``.

Quelle: Vollmutation #2174, Batch ``handover_note-a``
(``welle-l-0.99/reports/m-scratch/batch-handover_note-a.json``, Triage
``welle-l-0.99/reports/m-scratch/pins-handover_note-a/triage-099-handover_note-a.json``).
66 Survivor-Mutanten in zwei Funktionen: ``save_note`` (50) und
``_ist_schichtkonflikt`` (16). Die anderen Funktionen des Moduls
(``current_shift``/``next_shift``/``editable_until``/``_build_block``/
``build_handover_note_context`` u.a.) liegen in den Batches
``handover_note-b``/``-c`` und werden hier NICHT gepinnt.

**``save_note`` — zwei Gruppen:**

1. Reine Meldungstext-Mutationen (``PermissionDenied``/``HandoverNoteConflictError``-
   Argumente: Groß-/Kleinschreibung, ``XX…XX``-Marker, ganze Meldung durch
   ``None`` ersetzt) sind ``skip`` — kein Test und kein Aufrufer vergleicht
   diesen Text (die View prüft nur den Marker ``handover-note-conflict`` im
   HTML, nie die Meldung selbst), er fließt nirgends in Hash/Validator/
   Vergleich ein (24 Mutanten, siehe Triage).
2. Echte Verhaltens-Mutationen: die ``ab_schichtbeginn``-Weiche beider
   ``_window_is_open``-Aufrufe (Neuanlage vs. Bestandszeile — nur EINE
   Kombination ist äquivalent, siehe unten), die Audit-Einträge aller drei
   Aktionen (``HANDOVER_NOTE_CREATED``/``_UPDATED``/``_DELETED`` verlieren
   ``user``/``facility``/``target_type``/``target_id``/``changed_fields``),
   der in der DB persistierte ``updated_by`` beim zweiten Schreiben (das
   in-memory-Objekt wird direkt danach separat gesetzt und verdeckt eine
   kaputte ``.update()``-Zeile) und der exakte Revisions-Mindestzuwachs.

**Äquivalenz (2 Fälle, je mit Diff-Beleg UND Testlauf unter der Mutante
verifiziert):**

- ``xǁx_save_noteǁ__mutmut_62``: ``ab_schichtbeginn=False -> None`` im
  Bestandszeile-Zweig. ``_window_is_open`` prüft den Parameter nur über
  ``if ab_schichtbeginn and …`` — ``False`` und ``None`` sind beide falsy
  und nehmen exakt denselben Zweig; der Wert wird nirgends gespeichert oder
  weitergereicht. Unter der Mutante bleibt die komplette Bestandsklasse
  ``TestFensterGrenzenSindPersistiert`` (Bestandsdatei) grün.
- ``xǁx__ist_schichtkonfliktǁ__mutmut_11``: ``getattr(exc, "__cause__",
  None)`` verliert sein Default (2-Arg-Form). Jede Exception-Instanz trägt
  ``__cause__`` als von CPython garantiertes Standardattribut (Default
  ``None``, nicht löschbar) — die 2-Arg-Form kann für kein ``exc`` jemals
  ``AttributeError`` werfen.

**``_ist_schichtkonflikt`` hat keine eigene Testdatei:** die Bestandssuite
erreicht sie nur indirekt über ``save_note``s ``except``-Zweig bzw. den
DB-Renn-Test (``test_handover_note_race.py``) — und dort mit einem ECHTEN
Postgres-Fehler, dessen ``str()`` den Constraint-Namen ohnehin enthält. Der
strukturierte ``diag``-Pfad und der String-Fallback liefern in der Praxis
also dasselbe Ergebnis; jede Verwechslung der beiden Pfade (oder ein Absturz
im ``diag``-losen Fall) bleibt unbeobachtet. Hier werden beide Pfade mit
direkt konstruierten Fehlerobjekten getrennt geprüft (Muster
``_FakeDeadlockCause`` aus ``test_eventhistory_retention.py``).

Zusicherungen EXAKT (``==``/``is``), nie per Teilstring (mutmut erzeugt
``"XX<text>XX"``-Varianten, die eine ``in``-Prüfung übersehen würde;
``pytest.raises(match=…)`` ist ebenfalls eine Teilstring-Suche).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.utils import timezone

from core.models import AuditLog, ShiftHandoverNote, TimeFilter
from core.services.case.handover_note import (
    _SHIFT_UNIQUE_CONSTRAINT,
    Shift,
    _ist_schichtkonflikt,
    decrypt_note,
    save_note,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def _berlin(year, month, day, hour, minute=0):
    """Aware Berliner Wanduhrzeit (Muster ``test_handover_note.py``)."""
    naive = datetime(year, month, day, hour, minute)
    return naive.replace(tzinfo=timezone.get_current_timezone())


@pytest.fixture
def tf_frueh(facility):
    return TimeFilter.objects.create(
        facility=facility, label="Frühdienst", start_time=time(6, 0), end_time=time(14, 0), sort_order=0
    )


@pytest.fixture
def tf_spaet(facility):
    return TimeFilter.objects.create(
        facility=facility, label="Spätdienst", start_time=time(14, 0), end_time=time(22, 0), sort_order=1
    )


@pytest.fixture
def tf_nacht(facility):
    return TimeFilter.objects.create(
        facility=facility, label="Nachtdienst", start_time=time(22, 0), end_time=time(6, 0), sort_order=2
    )


@pytest.fixture
def schichten(tf_frueh, tf_spaet, tf_nacht):
    return tf_frueh, tf_spaet, tf_nacht


class TestSchichtNochNichtBegonnenVerhindertDieErstanlage:
    """Pins ``xǁx_save_noteǁ__mutmut_52``/``__mutmut_56``.

    Neuanlage prüft ``ab_schichtbeginn=True`` — eine Schicht, die um ``now``
    noch nicht begonnen hat, darf keine Notiz bekommen. Die Bestandsdatei
    testet das nur für ``is_shift_editable`` (Anzeige), nie für ``save_note``
    selbst.
    """

    def test_anlegen_vor_schichtbeginn_wird_abgelehnt(self, facility, staff_user, schichten):
        _, tf_spaet, _ = schichten
        with pytest.raises(ValidationError):
            save_note(
                facility,
                Shift(date(2026, 6, 15), tf_spaet),
                staff_user,
                "Zu früh",
                now=_berlin(2026, 6, 15, 7, 0),
            )
        assert ShiftHandoverNote.objects.count() == 0


class TestBestandsnotizIgnoriertDenEingefrorenenSchichtbeginn:
    """Pin ``xǁx_save_noteǁ__mutmut_66``.

    Gemäß Schichtfenster-Regel: für eine BESTEHENDE Notiz zählt ausschließlich
    ``editable_until`` — nicht (erneut) ``window_start``. Ein rückdatiertes
    ``now`` vor dem eingefrorenen ``window_start`` darf eine laufende
    Bearbeitung nicht blockieren; die Bearbeitung bleibt offen.
    """

    def test_bearbeitung_bleibt_offen_mit_now_vor_dem_eingefrorenen_schichtbeginn(
        self, facility, staff_user, schichten
    ):
        tf_frueh, _, _ = schichten
        schicht = Shift(date(2026, 6, 15), tf_frueh)
        save_note(facility, schicht, staff_user, "Erste Fassung", now=_berlin(2026, 6, 15, 7, 0))

        aktualisiert = save_note(facility, schicht, staff_user, "Rückdatierte Änderung", now=_berlin(2026, 6, 15, 5, 0))

        assert aktualisiert is not None
        assert decrypt_note(aktualisiert) == "Rückdatierte Änderung"


class TestLoeschAuditTraegtVollstaendigeIdentitaet:
    """Pins ``xǁx_save_noteǁ__mutmut_71/72/74/75/76/77/82/83/85/86/87/95/96``.

    Der Lösch-Audit-Eintrag ist die einzige verbleibende Spur der gelöschten
    Zeile — ``user``/``facility``/``target_type``/``target_id`` und
    ``detail`` müssen exakt stimmen.
    """

    def test_loeschen_schreibt_user_facility_zieltyp_id_und_detail_exakt(self, facility, staff_user, schichten):
        tf_frueh, _, _ = schichten
        schicht = Shift(date(2026, 6, 15), tf_frueh)
        angelegt = save_note(facility, schicht, staff_user, "Wird gelöscht", now=_berlin(2026, 6, 15, 7, 0))
        erwartete_id = str(angelegt.pk)

        save_note(facility, schicht, staff_user, "", now=_berlin(2026, 6, 15, 8, 0))

        eintrag = AuditLog.objects.filter(action=AuditLog.Action.HANDOVER_NOTE_DELETED).order_by("-timestamp").first()
        assert eintrag is not None
        assert eintrag.user_id == staff_user.pk
        assert eintrag.facility_id == facility.pk
        assert eintrag.target_type == "ShiftHandoverNote"
        assert eintrag.target_id == erwartete_id
        assert eintrag.detail == {
            "shift_date": "2026-06-15",
            "time_filter": "Frühdienst",
            "changed_fields": ["note"],
        }


class TestAnlageAuditTraegtHandelndePersonUndEinrichtung:
    """Pins ``xǁx_save_noteǁ__mutmut_145/146``."""

    def test_anlegen_schreibt_user_und_facility_exakt(self, facility, staff_user, schichten):
        tf_frueh, _, _ = schichten
        save_note(facility, Shift(date(2026, 6, 15), tf_frueh), staff_user, "Neu", now=_berlin(2026, 6, 15, 7, 0))

        eintrag = AuditLog.objects.filter(action=AuditLog.Action.HANDOVER_NOTE_CREATED).order_by("-timestamp").first()
        assert eintrag is not None
        assert eintrag.user_id == staff_user.pk
        assert eintrag.facility_id == facility.pk


class TestAktualisierungPersistiertUndProtokolliertVollstaendig:
    """Pins ``xǁx_save_noteǁ__mutmut_176/179`` (persistierter ``updated_by``)
    und ``xǁx_save_noteǁ__mutmut_187/188/189/194`` (Änderungs-Audit-Eintrag).
    """

    def test_zweiter_write_persistiert_updated_by_frisch_aus_der_db(self, facility, staff_user, lead_user, schichten):
        tf_frueh, _, _ = schichten
        schicht = Shift(date(2026, 6, 15), tf_frueh)
        save_note(facility, schicht, staff_user, "Eins", now=_berlin(2026, 6, 15, 7, 0))
        save_note(facility, schicht, lead_user, "Zwei", now=_berlin(2026, 6, 15, 8, 0))

        # Frischer Fetch statt des zurückgegebenen Objekts: die Codezeile
        # NACH dem ``.update()``-Aufruf setzt ``bestand.updated_by`` ohnehin
        # im Python-Objekt — nur die DB-Zeile selbst zeigt, ob das
        # ``.update()``-Keyword tatsächlich mitgeschrieben wurde.
        frisch = ShiftHandoverNote.objects.get(shift_date=date(2026, 6, 15), time_filter=tf_frueh, facility=facility)
        assert frisch.updated_by_id == lead_user.pk

    def test_aendern_schreibt_user_facility_und_ziel_exakt(self, facility, staff_user, lead_user, schichten):
        tf_frueh, _, _ = schichten
        schicht = Shift(date(2026, 6, 15), tf_frueh)
        angelegt = save_note(facility, schicht, staff_user, "Eins", now=_berlin(2026, 6, 15, 7, 0))
        save_note(facility, schicht, lead_user, "Zwei", now=_berlin(2026, 6, 15, 8, 0))

        eintrag = AuditLog.objects.filter(action=AuditLog.Action.HANDOVER_NOTE_UPDATED).order_by("-timestamp").first()
        assert eintrag is not None
        assert eintrag.user_id == lead_user.pk
        assert eintrag.facility_id == facility.pk
        assert eintrag.target_type == "ShiftHandoverNote"
        assert eintrag.target_id == str(angelegt.pk)


class TestRevisionsMindestzuwachsIstExakt:
    """Pin ``xǁx_save_noteǁ__mutmut_172``."""

    @staticmethod
    def _angehaltene_uhr(instant):
        return patch("django.utils.timezone.now", return_value=instant)

    def test_revision_waechst_bei_stehender_uhr_um_genau_eine_mikrosekunde(self, facility, staff_user, schichten):
        tf_frueh, _, _ = schichten
        schicht = Shift(date(2026, 6, 15), tf_frueh)
        jetzt = _berlin(2026, 6, 15, 7, 0)
        stillstand = datetime(2026, 6, 15, 5, 0, tzinfo=UTC)

        with self._angehaltene_uhr(stillstand):
            erste = save_note(facility, schicht, staff_user, "Eins", now=jetzt)
            zweite = save_note(facility, schicht, staff_user, "Zwei", now=jetzt)

        assert zweite.updated_at == erste.updated_at + timedelta(microseconds=1)


class _FakeDriverCause(Exception):
    """Treiber-Ausnahme mit ``.diag``, wie psycopg sie als ``__cause__`` unter
    den Django-Wrapper legt (Muster ``_FakeDeadlockCause`` in
    ``test_eventhistory_retention.py``).
    """

    def __init__(self, constraint_name):
        super().__init__("driver-level detail")

        class _Diag:
            pass

        diag = _Diag()
        diag.constraint_name = constraint_name
        self.diag = diag


class TestIstSchichtkonflikt:
    """Pins ``xǁx__ist_schichtkonfliktǁ__mutmut_1/2/6/7/11(äquivalent)/12/13/
    14/15/16/17/21/22/23/25/26``.

    ``_ist_schichtkonflikt`` hat keine eigene Testdatei — die Bestandssuite
    erreicht sie nur indirekt mit einem ECHTEN Postgres-Fehler, dessen
    ``str()`` den Constraint-Namen ohnehin enthält, sodass strukturierter
    ``diag``-Pfad und String-Fallback dasselbe Ergebnis liefern. Direkt
    konstruierte Fehlerobjekte trennen die beiden Pfade.
    """

    def test_strukturierter_diag_gewinnt_auch_ohne_den_namen_im_fehlertext(self):
        """Killt mutmut_1/2/7/12/13/14/15 (``diagnose`` wird faktisch immer
        ``None``, dadurch faelschlich auf den String-Fallback umgeleitet)
        sowie mutmut_16/17/22/23 (``name`` wird faktisch immer ``None``,
        derselbe Effekt eine Zeile weiter unten): der Fehlertext enthaelt den
        Constraint-Namen bewusst NICHT, nur der strukturierte ``diag`` traegt
        ihn — nur das Original erkennt den Konflikt trotzdem.
        """
        exc = IntegrityError("insert or update on table violates check_other_rule")
        exc.__cause__ = _FakeDriverCause(_SHIFT_UNIQUE_CONSTRAINT)

        assert _ist_schichtkonflikt(exc) is True

    def test_fehler_ohne_treiber_diag_stuerzt_nicht_ab(self):
        """Killt mutmut_6/mutmut_21 (Default aus der ``getattr``-Kette
        entfernt): ohne echten Treiber-``diag`` (``exc.__cause__`` bleibt
        ``None``, der Normalfall für Backends/Fehler ohne psycopg-diag) ist
        das Zwischenobjekt an der jeweils mutierten Stelle ``None`` — die
        2-Arg-``getattr``-Form wirft dort ``AttributeError`` statt den
        Fallback-Pfad zu nehmen.
        """
        exc = IntegrityError("duplicate key value violates unique constraint")

        assert _ist_schichtkonflikt(exc) is False

    def test_textfallback_erkennt_den_constraint_namen_im_fehlertext(self):
        """Killt mutmut_25 (``in`` -> ``not in``) und mutmut_26 (``str(exc)``
        -> ``str(None)``): ohne strukturierten ``diag`` ist der Fehlertext
        die letzte Instanz, die den Konflikt noch erkennen kann.
        """
        exc = IntegrityError(f'duplicate key value violates unique constraint "{_SHIFT_UNIQUE_CONSTRAINT}"')

        assert _ist_schichtkonflikt(exc) is True

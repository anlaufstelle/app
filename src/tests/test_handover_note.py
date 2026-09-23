"""Tests fuer die manuelle Schichtnotiz in der Uebergabe (Refs #2042).

Deckt die vier Zusagen des Tickets ab, die ohne Browser pruefbar sind:
Editierfenster (inkl. DST), Rollen-Guard, Verschluesselung at rest und der
PII-freie Audit-Eintrag. Retention und Rotation liegen in
``test_retention_strategies_unit.py`` bzw. ``test_reencrypt_fields.py``.
"""

from datetime import UTC, date, datetime, time, timedelta
from unittest.mock import patch

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.db.models import ProtectedError
from django.template.loader import render_to_string
from django.utils import timezone

from core.models import AuditLog, ShiftHandoverNote, TimeFilter
from core.services.case.handover_note import (
    Shift,
    build_handover_note_context,
    can_read_notes,
    can_write_notes,
    compute_shift_window,
    current_shift,
    decrypt_note,
    editable_until,
    get_note,
    is_shift_editable,
    next_shift,
    note_revision,
    save_note,
    shift_window,
)
from core.services.zeitstrom_filters import resolve_zeitstrom_state


def _berlin(year, month, day, hour, minute=0, *, fold=0):
    """Aware Berliner Wanduhrzeit — ``fold`` waehlt die Okkurrenz (PEP 495)."""
    naive = datetime(year, month, day, hour, minute, fold=fold)
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


@pytest.mark.django_db
class TestSchichtbestimmung:
    """``current_shift``/``next_shift`` — die Schicht als (Datum, Zeitfenster)."""

    def test_laufende_schicht_ist_das_deckende_fenster(self, facility, schichten):
        tf_frueh, tf_spaet, _ = schichten
        jetzt = _berlin(2026, 6, 15, 15, 30)
        assert current_shift(facility, now=jetzt) == Shift(date(2026, 6, 15), tf_spaet)

    def test_nachtschicht_gehoert_zum_vortag(self, facility, schichten):
        _, _, tf_nacht = schichten
        jetzt = _berlin(2026, 6, 15, 2, 0)
        assert current_shift(facility, now=jetzt) == Shift(date(2026, 6, 14), tf_nacht)

    def test_ohne_zeitfilter_ist_die_schicht_der_kalendertag(self, facility):
        jetzt = _berlin(2026, 6, 15, 15, 30)
        assert current_shift(facility, now=jetzt) == Shift(date(2026, 6, 15), None)

    def test_naechste_schicht_innerhalb_des_tages(self, facility, schichten):
        tf_frueh, tf_spaet, _ = schichten
        assert next_shift(facility, Shift(date(2026, 6, 15), tf_frueh)) == Shift(date(2026, 6, 15), tf_spaet)

    def test_letzte_schicht_des_tages_zeigt_auf_die_erste_des_folgetags(self, facility, schichten):
        tf_frueh, _, tf_nacht = schichten
        assert next_shift(facility, Shift(date(2026, 6, 15), tf_nacht)) == Shift(date(2026, 6, 16), tf_frueh)

    def test_ohne_zeitfilter_ist_die_naechste_schicht_der_folgetag(self, facility):
        assert next_shift(facility, Shift(date(2026, 6, 15), None)) == Shift(date(2026, 6, 16), None)

    def test_inaktive_zeitfenster_zaehlen_nicht_als_naechste_schicht(self, facility, schichten):
        tf_frueh, tf_spaet, tf_nacht = schichten
        tf_spaet.is_active = False
        tf_spaet.save(update_fields=["is_active"])
        assert next_shift(facility, Shift(date(2026, 6, 15), tf_frueh)) == Shift(date(2026, 6, 15), tf_nacht)

    def test_fremde_einrichtung_liefert_keine_schicht(self, facility, second_facility, schichten):
        """Die Zeitfenster der eigenen Einrichtung gelten nur dort."""
        jetzt = _berlin(2026, 6, 15, 15, 30)
        assert current_shift(second_facility, now=jetzt) == Shift(date(2026, 6, 15), None)


@pytest.mark.django_db
class TestEditierfenster:
    """Editierbar ist eine Notiz bis zum Ende der FOLGE-Schicht (Refs #2042)."""

    def test_fenster_endet_mit_der_naechsten_schicht(self, facility, schichten):
        tf_frueh, tf_spaet, _ = schichten
        schicht = Shift(date(2026, 6, 15), tf_frueh)
        assert editable_until(facility, schicht) == shift_window(Shift(date(2026, 6, 15), tf_spaet))[1]

    def test_waehrend_der_eigenen_schicht_editierbar(self, facility, schichten):
        tf_frueh, _, _ = schichten
        schicht = Shift(date(2026, 6, 15), tf_frueh)
        assert is_shift_editable(facility, schicht, now=_berlin(2026, 6, 15, 7, 0)) is True

    def test_waehrend_der_folgeschicht_noch_editierbar(self, facility, schichten):
        tf_frueh, _, _ = schichten
        schicht = Shift(date(2026, 6, 15), tf_frueh)
        assert is_shift_editable(facility, schicht, now=_berlin(2026, 6, 15, 21, 0)) is True

    def test_nach_der_folgeschicht_geschlossen(self, facility, schichten):
        tf_frueh, _, _ = schichten
        schicht = Shift(date(2026, 6, 15), tf_frueh)
        assert is_shift_editable(facility, schicht, now=_berlin(2026, 6, 15, 22, 30)) is False

    def test_vor_schichtbeginn_noch_nicht_editierbar(self, facility, schichten):
        tf_frueh, _, _ = schichten
        schicht = Shift(date(2026, 6, 15), tf_frueh)
        assert is_shift_editable(facility, schicht, now=_berlin(2026, 6, 15, 5, 0)) is False

    def test_ohne_zeitfilter_endet_das_fenster_mit_dem_folgetag(self, facility):
        schicht = Shift(date(2026, 6, 15), None)
        assert editable_until(facility, schicht) == shift_window(Shift(date(2026, 6, 16), None))[1]
        assert is_shift_editable(facility, schicht, now=_berlin(2026, 6, 16, 23, 0)) is True
        assert is_shift_editable(facility, schicht, now=_berlin(2026, 6, 17, 0, 30)) is False


@pytest.mark.django_db
class TestEditierfensterDST:
    """DST-Grenzfaelle — die Semantik stammt aus ``get_time_range`` (#2057)."""

    def test_rueckfalltag_fenster_endet_auf_der_zweiten_okkurrenz(self, facility, schichten):
        """2026-10-25: die Stunde 02:00-03:00 laeuft zweimal.

        Der Nachtdienst 22:00-06:00 des 24.10. endet um 06:00 CET; das
        Editierfenster des Spaetdienstes vom 24.10. reicht genau bis dahin —
        real neun statt acht Stunden, weil der Tag 25 Stunden hat.
        """
        _, tf_spaet, _ = schichten
        grenze = editable_until(facility, Shift(date(2026, 10, 24), tf_spaet))
        assert grenze.astimezone(UTC) == datetime(2026, 10, 25, 5, 0, tzinfo=UTC)
        assert grenze.utcoffset() == timedelta(hours=1)

    def test_rueckfalltag_beide_okkurrenzen_liegen_im_fenster(self, facility, schichten):
        """02:30 gibt es zweimal — die Nachtschicht laeuft in beiden."""
        _, _, tf_nacht = schichten
        schicht = Shift(date(2026, 10, 24), tf_nacht)
        assert is_shift_editable(facility, schicht, now=_berlin(2026, 10, 25, 2, 30, fold=0)) is True
        assert is_shift_editable(facility, schicht, now=_berlin(2026, 10, 25, 2, 30, fold=1)) is True

    def test_rueckfalltag_laufende_schicht_ueber_die_doppelte_stunde(self, facility, schichten):
        _, _, tf_nacht = schichten
        for fold in (0, 1):
            assert current_shift(facility, now=_berlin(2026, 10, 25, 2, 30, fold=fold)) == Shift(
                date(2026, 10, 24), tf_nacht
            )

    def test_vorstelltag_fenster_folgt_der_dauer_ab_start(self, facility, schichten):
        """2026-03-29: die Stunde 02:00-03:00 existiert nicht.

        Der Nachtdienst 22:00-06:00 des 28.03. endet um 06:00 MESZ — die
        Wanduhrzeit existiert an diesem Tag, also greift die Dauer-ab-Start-
        Regel hier NICHT. Real dauert die Schicht dadurch nur **7 Stunden**
        (21:00Z bis 04:00Z): die Uhr ist waehrend der Schicht vorgesprungen.
        Die frueher hier behauptete Dauer von 8 h war falsch Befund
        E-09); die Dauer-ab-Start-Regel greift erst, wenn das Fensterende
        selbst in der Luecke liegt — das prueft
        ``TestDstFensterendeInDerGrenzstunde``.
        """
        _, tf_spaet, _ = schichten
        grenze = editable_until(facility, Shift(date(2026, 3, 28), tf_spaet))
        assert grenze.astimezone(UTC) == datetime(2026, 3, 29, 4, 0, tzinfo=UTC)
        assert grenze.utcoffset() == timedelta(hours=2)

    def test_vorstelltag_uebersprungene_stunde_bricht_das_fenster_nicht(self, facility, schichten):
        _, _, tf_nacht = schichten
        schicht = Shift(date(2026, 3, 28), tf_nacht)
        assert is_shift_editable(facility, schicht, now=_berlin(2026, 3, 29, 1, 30)) is True
        assert is_shift_editable(facility, schicht, now=_berlin(2026, 3, 29, 3, 30)) is True


@pytest.mark.django_db
class TestRollen:
    """Freitext kann sensible Inhalte tragen — ``assistant`` bleibt aussen vor."""

    def test_lesen_erst_ab_fachkraft(self, staff_user, lead_user, admin_user, assistant_user):
        assert can_read_notes(staff_user) is True
        assert can_read_notes(lead_user) is True
        assert can_read_notes(admin_user) is True
        assert can_read_notes(assistant_user) is False

    def test_schreiben_erst_ab_fachkraft(self, staff_user, lead_user, admin_user, assistant_user):
        assert can_write_notes(staff_user) is True
        assert can_write_notes(lead_user) is True
        assert can_write_notes(admin_user) is True
        assert can_write_notes(assistant_user) is False

    def test_assistenz_darf_nicht_speichern(self, facility, assistant_user, schichten):
        tf_frueh, _, _ = schichten
        with pytest.raises(PermissionDenied):
            save_note(
                facility,
                Shift(date(2026, 6, 15), tf_frueh),
                assistant_user,
                "Nicht erlaubt",
                now=_berlin(2026, 6, 15, 7, 0),
            )
        assert ShiftHandoverNote.objects.count() == 0

    def test_assistenz_sieht_keinen_notiz_kontext(self, facility, assistant_user, staff_user, schichten):
        tf_frueh, _, _ = schichten
        schicht = Shift(date(2026, 6, 15), tf_frueh)
        save_note(facility, schicht, staff_user, "Bitte nachfassen", now=_berlin(2026, 6, 15, 7, 0))
        assert build_handover_note_context(facility, assistant_user, schicht, now=_berlin(2026, 6, 15, 7, 30)) is None


@pytest.mark.django_db
class TestVerschluesselung:
    """Verschluesselt at rest — die Spalte darf den Klartext nie tragen."""

    def test_datenbankwert_ist_kein_klartext(self, facility, staff_user, schichten):
        tf_frueh, _, _ = schichten
        note = save_note(
            facility,
            Shift(date(2026, 6, 15), tf_frueh),
            staff_user,
            "B war heute sehr belastbar",
            now=_berlin(2026, 6, 15, 7, 0),
        )
        with connection.cursor() as cursor:
            cursor.execute("SELECT note::text FROM core_shifthandovernote WHERE id = %s", [str(note.pk)])
            roh = cursor.fetchone()[0]
        assert "belastbar" not in roh
        assert "__encrypted__" in roh

    def test_lesen_entschluesselt(self, facility, staff_user, schichten):
        tf_frueh, _, _ = schichten
        schicht = Shift(date(2026, 6, 15), tf_frueh)
        save_note(facility, schicht, staff_user, "B war heute sehr belastbar", now=_berlin(2026, 6, 15, 7, 0))
        assert decrypt_note(get_note(facility, schicht)) == "B war heute sehr belastbar"

    def test_str_nennt_den_text_nicht(self, facility, staff_user, schichten):
        tf_frueh, _, _ = schichten
        note = save_note(
            facility,
            Shift(date(2026, 6, 15), tf_frueh),
            staff_user,
            "Geheimer Freitext",
            now=_berlin(2026, 6, 15, 7, 0),
        )
        assert "Geheimer Freitext" not in str(note)
        assert "Geheimer Freitext" not in repr(note)


@pytest.mark.django_db
class TestSpeichern:
    def test_anlegen_setzt_autor_und_schicht(self, facility, staff_user, schichten):
        tf_frueh, _, _ = schichten
        schicht = Shift(date(2026, 6, 15), tf_frueh)
        note = save_note(facility, schicht, staff_user, "Erste Notiz", now=_berlin(2026, 6, 15, 7, 0))
        assert note.facility_id == facility.pk
        assert note.shift_date == date(2026, 6, 15)
        assert note.time_filter_id == tf_frueh.pk
        assert note.author_id == staff_user.pk
        assert note.updated_by_id == staff_user.pk

    def test_zweites_speichern_aktualisiert_dieselbe_zeile(self, facility, staff_user, lead_user, schichten):
        tf_frueh, _, _ = schichten
        schicht = Shift(date(2026, 6, 15), tf_frueh)
        erst = save_note(facility, schicht, staff_user, "Erste Notiz", now=_berlin(2026, 6, 15, 7, 0))
        zweit = save_note(facility, schicht, lead_user, "Korrigiert", now=_berlin(2026, 6, 15, 8, 0))
        assert erst.pk == zweit.pk
        assert ShiftHandoverNote.objects.count() == 1
        assert zweit.author_id == staff_user.pk
        assert zweit.updated_by_id == lead_user.pk
        assert decrypt_note(zweit) == "Korrigiert"

    def test_leerer_text_loescht_den_datensatz(self, facility, staff_user, schichten):
        tf_frueh, _, _ = schichten
        schicht = Shift(date(2026, 6, 15), tf_frueh)
        save_note(facility, schicht, staff_user, "Erste Notiz", now=_berlin(2026, 6, 15, 7, 0))
        assert save_note(facility, schicht, staff_user, "   ", now=_berlin(2026, 6, 15, 8, 0)) is None
        assert ShiftHandoverNote.objects.count() == 0

    def test_leerer_text_ohne_bestand_legt_nichts_an(self, facility, staff_user, schichten):
        tf_frueh, _, _ = schichten
        assert (
            save_note(facility, Shift(date(2026, 6, 15), tf_frueh), staff_user, "", now=_berlin(2026, 6, 15, 7, 0))
            is None
        )
        assert ShiftHandoverNote.objects.count() == 0

    def test_nach_dem_fenster_kein_schreiben(self, facility, staff_user, schichten):
        tf_frueh, _, _ = schichten
        schicht = Shift(date(2026, 6, 15), tf_frueh)
        save_note(facility, schicht, staff_user, "Erste Notiz", now=_berlin(2026, 6, 15, 7, 0))
        with pytest.raises(ValidationError):
            save_note(facility, schicht, staff_user, "Zu spät", now=_berlin(2026, 6, 15, 22, 30))
        assert decrypt_note(get_note(facility, schicht)) == "Erste Notiz"

    def test_nach_dem_fenster_kein_loeschen(self, facility, staff_user, schichten):
        tf_frueh, _, _ = schichten
        schicht = Shift(date(2026, 6, 15), tf_frueh)
        save_note(facility, schicht, staff_user, "Erste Notiz", now=_berlin(2026, 6, 15, 7, 0))
        with pytest.raises(ValidationError):
            save_note(facility, schicht, staff_user, "", now=_berlin(2026, 6, 15, 22, 30))
        assert ShiftHandoverNote.objects.count() == 1

    def test_fehlermeldung_nennt_die_frist_aber_keinen_text(self, facility, staff_user, schichten):
        tf_frueh, _, _ = schichten
        schicht = Shift(date(2026, 6, 15), tf_frueh)
        with pytest.raises(ValidationError) as excinfo:
            save_note(facility, schicht, staff_user, "Zu spät", now=_berlin(2026, 6, 15, 22, 30))
        meldung = " ".join(excinfo.value.messages)
        assert "Zu spät" not in meldung
        assert "Bearbeitung" in meldung

    def test_notiz_einer_anderen_einrichtung_bleibt_unsichtbar(
        self, facility, second_facility, staff_user, foreign_staff, schichten
    ):
        tf_frueh, _, _ = schichten
        schicht = Shift(date(2026, 6, 15), tf_frueh)
        save_note(facility, schicht, staff_user, "Nur hier", now=_berlin(2026, 6, 15, 7, 0))
        assert get_note(second_facility, Shift(date(2026, 6, 15), None)) is None


@pytest.mark.django_db
class TestAudit:
    """Der Audit-Eintrag traegt Schicht-Koordinaten, nie den Notiztext."""

    def _letzter(self, action):
        return AuditLog.objects.filter(action=action).order_by("-timestamp").first()

    def test_anlegen_schreibt_created(self, facility, staff_user, schichten):
        tf_frueh, _, _ = schichten
        save_note(facility, Shift(date(2026, 6, 15), tf_frueh), staff_user, "Geheim", now=_berlin(2026, 6, 15, 7, 0))
        eintrag = self._letzter(AuditLog.Action.HANDOVER_NOTE_CREATED)
        assert eintrag is not None
        assert eintrag.target_type == "ShiftHandoverNote"
        assert eintrag.detail == {
            "shift_date": "2026-06-15",
            "time_filter": "Frühdienst",
            "changed_fields": ["note"],
        }
        assert "Geheim" not in str(eintrag.detail)

    def test_aendern_schreibt_updated(self, facility, staff_user, schichten):
        tf_frueh, _, _ = schichten
        schicht = Shift(date(2026, 6, 15), tf_frueh)
        save_note(facility, schicht, staff_user, "Eins", now=_berlin(2026, 6, 15, 7, 0))
        save_note(facility, schicht, staff_user, "Zwei", now=_berlin(2026, 6, 15, 8, 0))
        eintrag = self._letzter(AuditLog.Action.HANDOVER_NOTE_UPDATED)
        assert eintrag is not None
        assert eintrag.detail["changed_fields"] == ["note"]
        assert "Zwei" not in str(eintrag.detail)

    def test_unveraenderter_text_schreibt_keine_update_zeile(self, facility, staff_user, schichten):
        tf_frueh, _, _ = schichten
        schicht = Shift(date(2026, 6, 15), tf_frueh)
        save_note(facility, schicht, staff_user, "Eins", now=_berlin(2026, 6, 15, 7, 0))
        save_note(facility, schicht, staff_user, "Eins", now=_berlin(2026, 6, 15, 8, 0))
        assert AuditLog.objects.filter(action=AuditLog.Action.HANDOVER_NOTE_UPDATED).count() == 0

    def test_loeschen_schreibt_deleted(self, facility, staff_user, schichten):
        tf_frueh, _, _ = schichten
        schicht = Shift(date(2026, 6, 15), tf_frueh)
        save_note(facility, schicht, staff_user, "Eins", now=_berlin(2026, 6, 15, 7, 0))
        save_note(facility, schicht, staff_user, "", now=_berlin(2026, 6, 15, 8, 0))
        eintrag = self._letzter(AuditLog.Action.HANDOVER_NOTE_DELETED)
        assert eintrag is not None
        assert eintrag.detail["shift_date"] == "2026-06-15"

    def test_ganztags_schicht_traegt_time_filter_null(self, facility, staff_user):
        save_note(facility, Shift(date(2026, 6, 15), None), staff_user, "Eins", now=_berlin(2026, 6, 15, 7, 0))
        eintrag = self._letzter(AuditLog.Action.HANDOVER_NOTE_CREATED)
        assert eintrag.detail["time_filter"] is None


@pytest.mark.django_db
class TestAnzeigeKontext:
    """Was die Uebergabe-Ansicht zeigt: letzte Schicht + laufende Schicht."""

    def test_zwei_bloecke_wenn_angezeigte_und_laufende_schicht_verschieden(self, facility, staff_user, schichten):
        tf_frueh, tf_spaet, _ = schichten
        save_note(
            facility,
            Shift(date(2026, 6, 15), tf_frueh),
            staff_user,
            "Aus dem Frühdienst",
            now=_berlin(2026, 6, 15, 7, 0),
        )
        kontext = build_handover_note_context(
            facility, staff_user, Shift(date(2026, 6, 15), tf_frueh), now=_berlin(2026, 6, 15, 15, 0)
        )
        assert [block.kind for block in kontext["blocks"]] == ["previous", "current"]
        assert kontext["blocks"][0].text == "Aus dem Frühdienst"
        assert kontext["blocks"][0].shift.time_filter == tf_frueh
        assert kontext["blocks"][1].shift.time_filter == tf_spaet
        assert kontext["blocks"][1].text == ""

    def test_ein_block_wenn_die_angezeigte_schicht_die_laufende_ist(self, facility, staff_user, schichten):
        _, tf_spaet, _ = schichten
        kontext = build_handover_note_context(
            facility, staff_user, Shift(date(2026, 6, 15), tf_spaet), now=_berlin(2026, 6, 15, 15, 0)
        )
        assert [block.kind for block in kontext["blocks"]] == ["current"]

    def test_historische_schicht_ist_schreibgeschuetzt(self, facility, staff_user, schichten):
        tf_frueh, _, _ = schichten
        save_note(facility, Shift(date(2026, 6, 10), tf_frueh), staff_user, "Alt", now=_berlin(2026, 6, 10, 7, 0))
        kontext = build_handover_note_context(
            facility, staff_user, Shift(date(2026, 6, 10), tf_frueh), now=_berlin(2026, 6, 15, 15, 0)
        )
        alt = kontext["blocks"][0]
        assert alt.kind == "previous"
        assert alt.editable is False
        assert alt.text == "Alt"

    def test_laufende_schicht_ist_editierbar(self, facility, staff_user, schichten):
        tf_frueh, _, _ = schichten
        kontext = build_handover_note_context(
            facility, staff_user, Shift(date(2026, 6, 15), tf_frueh), now=_berlin(2026, 6, 15, 15, 0)
        )
        assert kontext["blocks"][1].editable is True
        assert kontext["can_write"] is True

    def test_autor_und_zeit_stehen_am_block(self, facility, staff_user, schichten):
        tf_frueh, _, _ = schichten
        save_note(facility, Shift(date(2026, 6, 15), tf_frueh), staff_user, "Mit Autor", now=_berlin(2026, 6, 15, 7, 0))
        kontext = build_handover_note_context(
            facility, staff_user, Shift(date(2026, 6, 15), tf_frueh), now=_berlin(2026, 6, 15, 15, 0)
        )
        block = kontext["blocks"][0]
        assert block.author == str(staff_user)
        assert block.updated_at is not None


@pytest.mark.django_db
class TestHinweisInDienstplanluecke:
    """Refs #2230: der leere Current-Block darf keine Schicht vortäuschen."""

    @staticmethod
    def _render(facility, staff_user, displayed_shift, *, now):
        context = build_handover_note_context(facility, staff_user, displayed_shift, now=now)
        return render_to_string(
            "core/handover/partials/_note.html",
            {
                "handover_notes": context,
                "handover_note_display": displayed_shift,
            },
        )

    def test_luecke_erklaert_fehlende_laufende_schicht(self, facility, staff_user):
        frueh = TimeFilter.objects.create(
            facility=facility,
            label="Frühdienst",
            start_time=time(6, 0),
            end_time=time(14, 0),
            sort_order=0,
        )
        TimeFilter.objects.create(
            facility=facility,
            label="Spätdienst",
            start_time=time(16, 0),
            end_time=time(22, 0),
            sort_order=1,
        )

        html = self._render(
            facility,
            staff_user,
            Shift(date(2026, 6, 15), frueh),
            now=_berlin(2026, 6, 15, 15, 0),
        )

        assert "In dieser Dienstplan-Lücke läuft keine Schicht." in html
        assert "Für diese Schicht wurde keine Übergabenotiz hinterlassen." not in html

    def test_deckende_schicht_behaelt_das_bestehende_formular(self, facility, staff_user, schichten):
        tf_frueh, tf_spaet, _ = schichten

        html = self._render(
            facility,
            staff_user,
            Shift(date(2026, 6, 15), tf_frueh),
            now=_berlin(2026, 6, 15, 15, 0),
        )

        assert "In dieser Dienstplan-Lücke läuft keine Schicht." not in html
        assert f'name="time_filter" value="{tf_spaet.pk}"' in html
        assert "Notiz speichern" in html

    def test_kalenderfall_zeigt_notiz_ohne_lueckenhinweis(self, facility, staff_user):
        gestern = Shift(date(2026, 6, 14), None)
        save_note(facility, gestern, staff_user, "Notiz von gestern", now=_berlin(2026, 6, 14, 20, 0))

        html = self._render(
            facility,
            staff_user,
            Shift(date(2026, 6, 15), None),
            now=_berlin(2026, 6, 15, 9, 0),
        )

        assert "In dieser Dienstplan-Lücke läuft keine Schicht." not in html
        assert "Notiz von gestern" in html


# ---------------------------------------------------------------------------
# Cross-Review E-01…E-09
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFensterGrenzenSindPersistiert:
    """E-01 (BLOCKER): eine geschlossene Notiz bleibt geschlossen.

    Anfangs wurde das Fenster bei JEDEM Zugriff aus den *gegenwaertig
    aktiven* Zeitfiltern neu gerechnet. Wer den Spaetdienst deaktivierte, riss
    damit das Editierfenster einer laengst abgeschlossenen Fruehschicht wieder
    auf. Die Grenzen gehoeren deshalb an die Zeile, nicht an die Konfiguration.
    """

    def test_anlage_persistiert_die_drei_grenzen(self, facility, staff_user, schichten):
        tf_frueh, tf_spaet, _ = schichten
        schicht = Shift(date(2026, 6, 15), tf_frueh)
        note = save_note(facility, schicht, staff_user, "Erste Notiz", now=_berlin(2026, 6, 15, 7, 0))
        assert note.window_start == shift_window(schicht)[0]
        assert note.window_end == shift_window(schicht)[1]
        assert note.editable_until == shift_window(Shift(date(2026, 6, 15), tf_spaet))[1]

    def test_deaktivierte_folgeschicht_oeffnet_die_notiz_nicht_wieder(self, facility, staff_user, schichten):
        _, tf_spaet, _ = schichten
        schicht = Shift(date(2026, 6, 15), schichten[0])
        save_note(facility, schicht, staff_user, "Erste Notiz", now=_berlin(2026, 6, 15, 7, 0))
        spaet_ende = _berlin(2026, 6, 15, 23, 0)
        with pytest.raises(ValidationError):
            save_note(facility, schicht, staff_user, "Zu spät", now=spaet_ende)

        tf_spaet.is_active = False
        tf_spaet.save(update_fields=["is_active"])

        with pytest.raises(ValidationError):
            save_note(facility, schicht, staff_user, "Durch Konfig-Änderung", now=spaet_ende)
        assert decrypt_note(get_note(facility, schicht)) == "Erste Notiz"

    def test_verschobene_zeiten_oeffnen_die_notiz_nicht_wieder(self, facility, staff_user, schichten):
        _, tf_spaet, _ = schichten
        schicht = Shift(date(2026, 6, 15), schichten[0])
        save_note(facility, schicht, staff_user, "Erste Notiz", now=_berlin(2026, 6, 15, 7, 0))

        tf_spaet.end_time = time(23, 59)
        tf_spaet.save(update_fields=["end_time"])

        with pytest.raises(ValidationError):
            save_note(facility, schicht, staff_user, "Durch Zeitverschiebung", now=_berlin(2026, 6, 15, 23, 0))

    def test_deaktivierte_folgeschicht_verhindert_auch_das_loeschen_nicht_mehr(self, facility, staff_user, schichten):
        _, tf_spaet, _ = schichten
        schicht = Shift(date(2026, 6, 15), schichten[0])
        save_note(facility, schicht, staff_user, "Erste Notiz", now=_berlin(2026, 6, 15, 7, 0))
        tf_spaet.is_active = False
        tf_spaet.save(update_fields=["is_active"])
        with pytest.raises(ValidationError):
            save_note(facility, schicht, staff_user, "", now=_berlin(2026, 6, 15, 23, 0))
        assert ShiftHandoverNote.objects.count() == 1

    def test_anzeige_nutzt_die_gespeicherten_grenzen(self, facility, staff_user, schichten):
        tf_frueh, tf_spaet, _ = schichten
        schicht = Shift(date(2026, 6, 15), tf_frueh)
        note = save_note(facility, schicht, staff_user, "Erste Notiz", now=_berlin(2026, 6, 15, 7, 0))
        tf_spaet.is_active = False
        tf_spaet.save(update_fields=["is_active"])
        kontext = build_handover_note_context(facility, staff_user, schicht, now=_berlin(2026, 6, 15, 23, 0))
        block = kontext["blocks"][0]
        assert block.editable is False
        assert block.editable_until == note.editable_until


@pytest.mark.django_db
class TestZeitfilterLoeschungSchuetztNotizen:
    """E-02: ein Zeitfilter mit Notizen ist nicht still löschbar."""

    def test_loeschen_eines_zeitfilters_mit_notizen_scheitert(self, facility, staff_user, schichten):
        tf_frueh, _, _ = schichten
        save_note(facility, Shift(date(2026, 6, 15), tf_frueh), staff_user, "Bleibt", now=_berlin(2026, 6, 15, 7, 0))
        with pytest.raises(ProtectedError):
            tf_frueh.delete()
        assert ShiftHandoverNote.objects.count() == 1

    def test_seed_flush_raeumt_notizen_vor_den_zeitfiltern_ab(self, facility, staff_user, schichten):
        """Der Schutz darf ``seed --flush`` nicht blockieren.

        ``flush_seed_data`` loescht ``TimeFilter.objects.all()`` — mit
        ``PROTECT`` lief das in einen ``ProtectedError``, sobald irgendwo eine
        Notiz lag, und riss damit jeden E2E-Aufbau. Der Flush raeumt ohnehin
        alles ab; er muss die Notizen nur selbst mitnehmen.
        """
        from core.seed.flush import flush_seed_data

        tf_frueh, _, _ = schichten
        # Bewusst direkt angelegt statt ueber ``save_note``: dessen Audit-Zeile
        # hinterliesse offene Trigger-Ereignisse auf ``core_auditlog``, und der
        # Flush schaltet den Immutability-Trigger per ALTER TABLE ab — das
        # scheitert dann an „pending trigger events". Geprueft wird hier die
        # Loeschreihenfolge, nicht der Schreibpfad.
        schicht = Shift(date(2026, 6, 15), tf_frueh)
        fenster = compute_shift_window(facility, schicht)
        ShiftHandoverNote.objects.create(
            facility=facility,
            shift_date=schicht.shift_date,
            time_filter=tf_frueh,
            note=None,
            window_start=fenster.window_start,
            window_end=fenster.window_end,
            editable_until=fenster.editable_until,
        )
        flush_seed_data()
        assert ShiftHandoverNote.objects.count() == 0
        assert TimeFilter.objects.count() == 0

    def test_zeitfilter_ohne_notizen_bleibt_loeschbar(self, facility, schichten):
        _, _, tf_nacht = schichten
        tf_nacht.delete()
        assert not TimeFilter.objects.filter(pk=tf_nacht.pk).exists()


@pytest.mark.django_db
class TestKalenderfallFolgeschicht:
    """E-03: ohne Zeitfilter ist die abgebende Schicht der VORTAG."""

    def test_kontext_zeigt_vortag_und_heute(self, facility, staff_user):
        gestern = Shift(date(2026, 6, 14), None)
        save_note(facility, gestern, staff_user, "Notiz von gestern", now=_berlin(2026, 6, 14, 20, 0))
        kontext = build_handover_note_context(
            facility, staff_user, Shift(date(2026, 6, 15), None), now=_berlin(2026, 6, 15, 9, 0)
        )
        assert [b.kind for b in kontext["blocks"]] == ["previous", "current"]
        assert kontext["blocks"][0].shift == gestern
        assert kontext["blocks"][0].text == "Notiz von gestern"
        assert kontext["blocks"][1].shift == Shift(date(2026, 6, 15), None)

    def test_explizite_datumsnavigation_bleibt_unveraendert(self, facility, staff_user):
        kontext = build_handover_note_context(
            facility, staff_user, Shift(date(2026, 6, 10), None), now=_berlin(2026, 6, 15, 9, 0)
        )
        assert [b.shift.shift_date for b in kontext["blocks"]] == [date(2026, 6, 10), date(2026, 6, 15)]

    def test_mit_zeitfiltern_kein_vortags_block(self, facility, staff_user, schichten):
        _, tf_spaet, _ = schichten
        kontext = build_handover_note_context(
            facility, staff_user, Shift(date(2026, 6, 15), tf_spaet), now=_berlin(2026, 6, 15, 15, 0)
        )
        assert [b.kind for b in kontext["blocks"]] == ["current"]


@pytest.mark.django_db
class TestSchichtIdentitaet:
    """E-07: eine Ganztags-Notiz gibt es nur ohne konfigurierte Zeitfilter."""

    def test_ganztagsnotiz_bei_konfigurierten_schichten_abgelehnt(self, facility, staff_user, schichten):
        with pytest.raises(ValidationError):
            save_note(
                facility,
                Shift(date(2026, 6, 15), None),
                staff_user,
                "Neben der Schicht",
                now=_berlin(2026, 6, 15, 15, 0),
            )
        assert ShiftHandoverNote.objects.count() == 0

    def test_ganztagsnotiz_ohne_zeitfilter_erlaubt(self, facility, staff_user):
        note = save_note(
            facility, Shift(date(2026, 6, 15), None), staff_user, "Ganzer Tag", now=_berlin(2026, 6, 15, 15, 0)
        )
        assert note is not None

    def test_bestandsnotiz_bleibt_editierbar_wenn_spaeter_zeitfilter_entstehen(self, facility, staff_user):
        schicht = Shift(date(2026, 6, 15), None)
        save_note(facility, schicht, staff_user, "Ganzer Tag", now=_berlin(2026, 6, 15, 9, 0))
        TimeFilter.objects.create(
            facility=facility, label="Frühdienst", start_time=time(6, 0), end_time=time(14, 0), sort_order=0
        )
        note = save_note(facility, schicht, staff_user, "Korrigiert", now=_berlin(2026, 6, 15, 10, 0))
        assert decrypt_note(note) == "Korrigiert"


@pytest.mark.django_db
class TestFensterEndeIstOffen:
    """E-08: ``now < editable_until`` — der Endinstant selbst ist zu."""

    def test_kurz_vor_dem_ende_offen(self, facility, staff_user, schichten):
        tf_frueh, _, _ = schichten
        schicht = Shift(date(2026, 6, 15), tf_frueh)
        grenze = editable_until(facility, schicht)
        note = save_note(facility, schicht, staff_user, "Gerade noch", now=grenze - timedelta(seconds=1))
        assert note is not None

    def test_genau_auf_dem_ende_geschlossen(self, facility, staff_user, schichten):
        tf_frueh, _, _ = schichten
        schicht = Shift(date(2026, 6, 15), tf_frueh)
        grenze = editable_until(facility, schicht)
        with pytest.raises(ValidationError):
            save_note(facility, schicht, staff_user, "Zu spät", now=grenze)

    def test_nach_dem_ende_geschlossen(self, facility, staff_user, schichten):
        tf_frueh, _, _ = schichten
        schicht = Shift(date(2026, 6, 15), tf_frueh)
        grenze = editable_until(facility, schicht)
        with pytest.raises(ValidationError):
            save_note(facility, schicht, staff_user, "Zu spät", now=grenze + timedelta(seconds=1))


@pytest.mark.django_db
class TestDstFensterendeInDerGrenzstunde:
    """E-09: Fensterenden, die IN der doppelten Stunde bzw. der Luecke liegen."""

    @staticmethod
    def _nacht_2045(facility):
        return TimeFilter.objects.create(
            facility=facility, label="Nachtdienst", start_time=time(20, 0), end_time=time(2, 45), sort_order=0
        )

    def test_rueckfalltag_ende_0245_nimmt_die_zweite_okkurrenz(self, facility):
        tf = self._nacht_2045(facility)
        _, ende = shift_window(Shift(date(2026, 10, 24), tf))
        assert ende.astimezone(UTC) == datetime(2026, 10, 25, 1, 45, tzinfo=UTC)
        assert ende.utcoffset() == timedelta(hours=1)
        assert ende.hour == 2 and ende.minute == 45

    def test_editierfenster_endet_in_der_doppelten_stunde(self, facility):
        """Das EDITIERFENSTER — nicht das eigene Fenster — endet in der Doppelstunde.

        Refs #2042 Befund E-13): Der frühere Test nahm die Nacht vom
        24.10. als eigene Schicht und landete beim Editierende am **26.10.**
        um 01:45Z — einem Montag, weit außerhalb der doppelten Stunde. Er
        prüfte damit nicht, was sein Name behauptete.

        Richtig ist die **Vorgänger**schicht: der Tagdienst des 24.10. hat als
        Folgeschicht die Nacht 20:00–02:45, und die endet am 25.10. auf der
        ZWEITEN Okkurrenz von 02:45 (= 01:45Z, Offset +01:00). Genau dieser
        Instant ist die Editiergrenze der Tagschicht.
        """
        tf_nacht = self._nacht_2045(facility)
        tf_tag = TimeFilter.objects.create(
            facility=facility, label="Tagdienst", start_time=time(8, 0), end_time=time(20, 0), sort_order=1
        )
        assert next_shift(facility, Shift(date(2026, 10, 24), tf_tag)) == Shift(date(2026, 10, 24), tf_nacht)

        grenze = editable_until(facility, Shift(date(2026, 10, 24), tf_tag))
        assert grenze.astimezone(UTC) == datetime(2026, 10, 25, 1, 45, tzinfo=UTC)
        assert grenze.utcoffset() == timedelta(hours=1), "zweite Okkurrenz (MEZ)"
        assert (grenze.hour, grenze.minute) == (2, 45)

    def test_eigenes_fenster_der_folgenacht_endet_ebenfalls_dort(self, facility):
        """Gegenprobe von der anderen Seite: dieselbe Grenze als eigenes Fenster."""
        tf = self._nacht_2045(facility)
        assert shift_window(Shift(date(2026, 10, 24), tf))[1].astimezone(UTC) == datetime(
            2026, 10, 25, 1, 45, tzinfo=UTC
        )

    def test_vorstelltag_imaginaeres_ende_folgt_der_dauer_ab_start(self, facility):
        tf = TimeFilter.objects.create(
            facility=facility, label="Nachtdienst", start_time=time(20, 0), end_time=time(2, 30), sort_order=0
        )
        start, ende = shift_window(Shift(date(2026, 3, 28), tf))
        # Ueber UTC subtrahieren: beide Werte tragen dasselbe ``tzinfo``-Objekt,
        # und dann vergleicht Python Wanduhrfelder und ignoriert die Offsets
        # (genau die Falle aus ``shift_window_covers``, Refs #2057).
        assert ende.astimezone(UTC) - start.astimezone(UTC) == timedelta(hours=6, minutes=30)
        assert ende.astimezone(UTC) == datetime(2026, 3, 29, 1, 30, tzinfo=UTC)
        assert (ende.hour, ende.minute) == (3, 30)


# ---------------------------------------------------------------------------
# Re-Review E-12/E-13
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSchichtzeitenWerdenLokalAngezeigt:
    """E-12: der Notizkopf zeigt Schichtzeiten, keine UTC-Instants.

    Die gespeicherten Grenzen kommen aus Postgres mit ``tzinfo=UTC``. Wer sie
    ohne ``localtime()`` formatiert, macht aus dem Fruehdienst 06:00–14:00 die
    Anzeige „04:00 – 12:00" — und zwar erst NACH dem Speichern, weil vorher die
    lokal gerechneten Werte aus ``get_time_range`` verwendet werden.
    """

    @staticmethod
    def _range_label(facility, user, shift, now):
        kontext = build_handover_note_context(facility, user, shift, now=now)
        return next(b.range_label for b in kontext["blocks"] if b.shift == shift)

    def test_sommerzeit_gleiche_anzeige_vor_und_nach_der_anlage(self, facility, staff_user, schichten):
        tf_frueh, _, _ = schichten
        schicht = Shift(date(2026, 6, 15), tf_frueh)
        jetzt = _berlin(2026, 6, 15, 7, 0)
        vorher = self._range_label(facility, staff_user, schicht, jetzt)
        assert vorher == "06:00 – 14:00"

        save_note(facility, schicht, staff_user, "Angelegt", now=jetzt)
        nachher = self._range_label(facility, staff_user, schicht, jetzt)
        assert nachher == vorher, "Nach dem Speichern kommen die Grenzen aus der DB — als UTC"

    def test_winterzeit_gleiche_anzeige_vor_und_nach_der_anlage(self, facility, staff_user, schichten):
        tf_frueh, _, _ = schichten
        schicht = Shift(date(2026, 1, 15), tf_frueh)
        jetzt = _berlin(2026, 1, 15, 7, 0)
        vorher = self._range_label(facility, staff_user, schicht, jetzt)
        assert vorher == "06:00 – 14:00"

        save_note(facility, schicht, staff_user, "Angelegt", now=jetzt)
        assert self._range_label(facility, staff_user, schicht, jetzt) == vorher

    def test_nachtschicht_ueber_mitternacht(self, facility, staff_user, schichten):
        _, _, tf_nacht = schichten
        schicht = Shift(date(2026, 6, 15), tf_nacht)
        jetzt = _berlin(2026, 6, 15, 23, 0)
        vorher = self._range_label(facility, staff_user, schicht, jetzt)
        assert vorher == "22:00 – 06:00"

        save_note(facility, schicht, staff_user, "Nachtnotiz", now=jetzt)
        assert self._range_label(facility, staff_user, schicht, jetzt) == vorher


@pytest.mark.django_db
class TestSchichtfolgeIstUhrzeitunabhaengig:
    """E-16: die Schichtfolge der Wechsel-Tests gilt auch am Tagesrand.

    Der Re-Review hat gezeigt, dass die frühere Konstruktion um
    **00:00:30** kippte: „jetzt minus eine Minute" wurde nach dem Wegfall des
    Datums zu ``23:59``, die zweite Schicht bekam ``23:59–23:59`` und übernahm
    nie. Hier wird dieselbe Konstruktion mit **injiziertem** ``now`` geprüft —
    ohne Browser und ohne von der Uhr des Testlaufs abzuhängen.
    """

    @staticmethod
    def _minuten_vor(jetzt, minuten):
        return (jetzt - timedelta(minutes=minuten)).time().replace(second=0, microsecond=0)

    def _aufbau(self, facility, jetzt):
        """Eine laufende Schicht A (Mitternachts-Überlauf deckt ``jetzt``)."""
        return TimeFilter.objects.create(
            facility=facility,
            label="Schicht A",
            start_time=self._minuten_vor(jetzt, 5),
            end_time=self._minuten_vor(jetzt, 6),
            sort_order=0,
        )

    def _wechsel(self, facility, schicht_a, jetzt):
        grenze = self._minuten_vor(jetzt, 1)
        schicht_a.end_time = grenze
        schicht_a.save(update_fields=["end_time"])
        return TimeFilter.objects.create(
            facility=facility,
            label="Schicht B",
            start_time=grenze,
            end_time=self._minuten_vor(jetzt, 2),
            sort_order=1,
        )

    @pytest.mark.parametrize(
        ("stunde", "minute", "sekunde"),
        [
            (0, 0, 30),  # der Fall, an dem die alte Konstruktion riss
            (0, 4, 0),
            (15, 0, 30),
            (23, 59, 30),
        ],
        ids=["tagesbeginn", "kurz-nach-mitternacht", "mittags", "tagesende"],
    )
    def test_schicht_a_laeuft_und_wird_danach_zur_abgebenden(self, facility, staff_user, stunde, minute, sekunde):
        jetzt = _berlin(2026, 6, 15, stunde, minute) + timedelta(seconds=sekunde)
        schicht_a = self._aufbau(facility, jetzt)

        laufend = current_shift(facility, now=jetzt)
        assert laufend.time_filter == schicht_a, "A muss vor dem Wechsel die laufende Schicht sein"
        geschrieben = laufend

        self._wechsel(facility, schicht_a, jetzt)

        # Die automatische Übergabe waehlt die zuletzt abgeschlossene Schicht —
        # das muss jetzt genau die geschriebene sein.
        state = resolve_zeitstrom_state({"view": "uebergabe"}, facility, today=jetzt.date(), now=jetzt)
        assert Shift(state.target_date, state.time_filter) == geschrieben, (
            "Nach dem Wechsel ist die geschriebene Schicht die abgebende"
        )
        assert current_shift(facility, now=jetzt).time_filter.label == "Schicht B"


@pytest.mark.django_db
class TestRevisionSchreitetGarantiertFort:
    """E-17: eine fachliche Änderung bekommt IMMER eine neue Revision.

    ``updated_at`` ist ``auto_now`` und damit keine monotone Revisionsnummer —
    Django setzt schlicht ``timezone.now()``, ohne den bisherigen Wert
    anzusehen. In der Praxis lagen zwei aufeinanderfolgende Writes im
    Stichprobenlauf mindestens 4,2 µs auseinander, aber das ist eine
    Beobachtung und keine Zusage. Steht die Uhr still (angehaltene Testuhr,
    Zeitsprung, gröbere Zeitquelle), trügen zwei verschiedene Fassungen
    dieselbe Revision — und der Abgleich aus R-E-25 übersähe genau den
    Zwischen-Write, den er verhindern soll.

    Die Schreibstelle unter der Zeilensperre schreibt ``updated_at`` deshalb
    garantiert fort. Die Doku-Zusage „nie still überschrieben" gilt damit
    technisch, nicht nur statistisch.
    """

    @staticmethod
    def _angehaltene_uhr(instant):
        return patch("django.utils.timezone.now", return_value=instant)

    def test_zweiter_write_bekommt_eine_andere_revision(self, facility, staff_user, schichten):
        tf_frueh, _, _ = schichten
        schicht = Shift(date(2026, 6, 15), tf_frueh)
        jetzt = _berlin(2026, 6, 15, 7, 0)
        stillstand = datetime(2026, 6, 15, 5, 0, tzinfo=UTC)

        with self._angehaltene_uhr(stillstand):
            erste = save_note(facility, schicht, staff_user, "Fassung eins", now=jetzt)
            revision_eins = note_revision(erste)
            zweite = save_note(facility, schicht, staff_user, "Fassung zwei", now=jetzt)
            revision_zwei = note_revision(zweite)

        assert decrypt_note(zweite) == "Fassung zwei"
        assert revision_zwei != revision_eins, (
            "Bei angehaltener Uhr trügen beide Fassungen dieselbe Revision — der Abgleich übersähe den Zwischen-Write"
        )

    def test_revision_waechst_auch_ueber_mehrere_writes(self, facility, staff_user, schichten):
        tf_frueh, _, _ = schichten
        schicht = Shift(date(2026, 6, 15), tf_frueh)
        jetzt = _berlin(2026, 6, 15, 7, 0)
        stillstand = datetime(2026, 6, 15, 5, 0, tzinfo=UTC)

        revisionen = []
        with self._angehaltene_uhr(stillstand):
            for nummer in range(4):
                note = save_note(facility, schicht, staff_user, f"Fassung {nummer}", now=jetzt)
                revisionen.append(note.updated_at)

        assert len(set(revisionen)) == len(revisionen), "Jeder Write braucht seinen eigenen Zeitstempel"
        assert revisionen == sorted(revisionen), "Und die Folge muss monoton steigen"

    def test_der_gespeicherte_wert_ueberlebt_den_db_roundtrip(self, facility, staff_user, schichten):
        tf_frueh, _, _ = schichten
        schicht = Shift(date(2026, 6, 15), tf_frueh)
        jetzt = _berlin(2026, 6, 15, 7, 0)
        stillstand = datetime(2026, 6, 15, 5, 0, tzinfo=UTC)

        with self._angehaltene_uhr(stillstand):
            save_note(facility, schicht, staff_user, "Fassung eins", now=jetzt)
            zweite = save_note(facility, schicht, staff_user, "Fassung zwei", now=jetzt)

        frisch = get_note(facility, schicht)
        assert note_revision(frisch) == note_revision(zweite), (
            "Die in der DB stehende Revision muss die sein, die der Abgleich mitgibt"
        )

    def test_bei_laufender_uhr_bleibt_es_bei_der_echten_zeit(self, facility, staff_user, schichten):
        """Kein künstliches Vorlaufen: die Fortschreibung ist nur eine Untergrenze."""
        tf_frueh, _, _ = schichten
        schicht = Shift(date(2026, 6, 15), tf_frueh)
        jetzt = _berlin(2026, 6, 15, 7, 0)

        vorher = timezone.now()
        note = save_note(facility, schicht, staff_user, "Einmalig", now=jetzt)
        nachher = timezone.now()

        assert vorher <= note.updated_at <= nachher

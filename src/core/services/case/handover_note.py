"""Manuelle Schichtnotiz in der Uebergabe — Service (Refs #2042).

Bewusst ein eigenes Modul neben :mod:`core.services.case.handover` statt einer
Erweiterung: ``handover.py`` beantwortet „was ist in dieser Schicht passiert"
(reine Aggregation ueber Ereignisse/Aufgaben, ohne Schreibpfad) und ist mit
``test_mutation_followup_handover.py`` als solche gepinnt. Hier liegt der
Gegenpart — ein Schreibpfad mit Verschluesselung, Rollen-Guard, Editierfenster
und Audit. Ein Modul je Verantwortung haelt die Mutations-Pin-Flaeche der
Aggregation frei und macht den Schreibpfad einzeln pruefbar.

Das ``detail`` der drei Audit-Zeilen steht bewusst **dreimal literal** statt
einmal in einem Helfer: ein dynamisch gebautes ``detail`` liefe am statischen
Guard ``TestAuditDetailKeyInventory`` vorbei und waere nur noch laufzeitgeprueft
(Refs #1944) — dieselbe Entscheidung und Begruendung wie in
``retention.enforcement.enforce_activities``.

Die DST-Semantik wird **nicht** hier definiert: Schichtfenster kommen
ausschliesslich aus :func:`core.services.events.get_time_range` (Refs #2057),
die laufende Schicht aus :func:`core.services.events.current_tally_context`.
Dieses Modul komponiert nur — es rechnet keine Wanduhrzeiten selbst.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from core.models import AuditLog, ShiftHandoverNote, TimeFilter
from core.services.audit import audit_event
from core.services.events import current_tally_context, get_time_range
from core.services.file_vault import encrypt_field, safe_decrypt


@dataclass(frozen=True)
class Shift:
    """Eine Schicht = lokales Kalenderdatum des Beginns + Zeitfenster.

    ``time_filter is None`` heisst „kein Zeitfilter konfiguriert" und meint den
    ganzen Kalendertag — dieselbe Fallunterscheidung wie ``get_time_range``.
    """

    shift_date: date
    time_filter: TimeFilter | None

    @property
    def time_filter_id(self) -> str:
        """Formularwert des Zeitfensters (``""`` fuer den Ganztag)."""
        return str(self.time_filter.pk) if self.time_filter is not None else ""

    @property
    def label(self) -> str:
        return self.time_filter.label if self.time_filter is not None else _("Ganzer Tag")


@dataclass(frozen=True)
class ShiftWindow:
    """Die drei Instants einer Schicht — Beginn, Ende, Ende des Editierfensters."""

    window_start: datetime
    window_end: datetime
    editable_until: datetime


@dataclass(frozen=True)
class NoteBlock:
    """Ein Anzeigeblock der Uebergabe (vorherige bzw. laufende Schicht)."""

    kind: str  # "previous" | "current"
    shift: Shift
    label: str
    range_label: str
    text: str
    author: str | None
    updated_at: datetime | None
    editable: bool
    editable_until: datetime


def _active_time_filters(facility) -> list[TimeFilter]:
    return list(TimeFilter.objects.for_facility(facility).filter(is_active=True).order_by("sort_order", "pk"))


def shift_window(shift: Shift) -> tuple[datetime, datetime]:
    """Instants der Schicht — reiner Durchgriff auf ``get_time_range``."""
    return get_time_range(shift.shift_date, shift.time_filter)


def current_shift(facility, *, now=None) -> Shift:
    """Die gerade laufende Schicht.

    Delegiert an ``current_tally_context`` — dieselbe Quelle, aus der die
    Strichliste ihr Live-Fenster zieht. Zwei Antworten auf „welche Schicht
    laeuft gerade" duerften an einer DST-Grenze nicht auseinanderlaufen.
    """
    context = current_tally_context(facility, now=now or timezone.localtime())
    return Shift(context.shift_date, context.time_filter)


def next_shift(facility, shift: Shift, *, time_filters: list[TimeFilter] | None = None) -> Shift:
    """Die Schicht, die unmittelbar nach *shift* beginnt.

    Gesucht wird der frueheste Schichtbeginn, der **echt nach** dem Beginn von
    *shift* liegt — ueber die Instants (UTC), nicht ueber Wanduhrzeiten. Damit
    faellt die letzte Schicht eines Tages von selbst auf die erste des
    Folgetags, und der Rueckfalltag verschiebt die Reihenfolge nicht.

    Ohne Zeitfilter (Ganztags-Schicht) ist die naechste Schicht der Folgetag.
    """
    if shift.time_filter is None:
        return Shift(shift.shift_date + timedelta(days=1), None)

    kandidaten = time_filters if time_filters is not None else _active_time_filters(facility)
    if not kandidaten:
        return Shift(shift.shift_date + timedelta(days=1), None)

    start_utc = shift_window(shift)[0].astimezone(UTC)
    bester: tuple[datetime, Shift] | None = None
    for shift_date in (shift.shift_date, shift.shift_date + timedelta(days=1)):
        for time_filter in kandidaten:
            kandidat = Shift(shift_date, time_filter)
            kandidat_start = shift_window(kandidat)[0].astimezone(UTC)
            if kandidat_start <= start_utc:
                continue
            if bester is None or kandidat_start < bester[0]:
                bester = (kandidat_start, kandidat)
    if bester is None:
        # Das eigene Zeitfenster ist inzwischen inaktiv/geloescht und es gibt
        # keinen spaeteren Beginn im Suchfenster — der Folgetag bleibt die
        # ehrliche Antwort statt einer erfundenen Schicht.
        return Shift(shift.shift_date + timedelta(days=1), None)
    return bester[1]


def editable_until(facility, shift: Shift, *, time_filters: list[TimeFilter] | None = None) -> datetime:
    """Ende des Editierfensters = Ende der FOLGE-Schicht (Ticket-Zusage).

    Rechnet aus der **gegenwaertigen** Konfiguration und ist damit nur fuer
    eine Schicht ohne gespeicherte Notiz die richtige Quelle. Fuer eine
    bestehende Notiz gilt ausschliesslich ihr eingefrorenes
    ``editable_until`` (Refs #2042, E-01).
    """
    return shift_window(next_shift(facility, shift, time_filters=time_filters))[1]


def compute_shift_window(facility, shift: Shift, *, time_filters: list[TimeFilter] | None = None) -> ShiftWindow:
    """Die drei Grenzen einer Schicht aus der aktuellen Konfiguration."""
    start_dt, end_dt = shift_window(shift)
    return ShiftWindow(
        window_start=start_dt,
        window_end=end_dt,
        editable_until=editable_until(facility, shift, time_filters=time_filters),
    )


def note_window(note: ShiftHandoverNote) -> ShiftWindow:
    """Die bei der Anlage eingefrorenen Grenzen einer bestehenden Notiz."""
    return ShiftWindow(
        window_start=note.window_start,
        window_end=note.window_end,
        editable_until=note.editable_until,
    )


def _window_is_open(fenster: ShiftWindow, now, *, ab_schichtbeginn: bool) -> bool:
    """``now < editable_until`` — der Endinstant selbst ist bereits zu.

    Verglichen wird ueber UTC: zwei aware Datetimes mit demselben
    ``tzinfo``-Objekt vergleichen sonst ihre Wanduhrfelder und ignorieren die
    Offsets — genau der Fehler, den ``_handover_filter`` fuer den Rueckfalltag
    beschreibt (Refs #2057).

    ``ab_schichtbeginn`` gilt nur fuer die **Erstanlage**: eine Notiz fuer eine
    noch nicht begonnene Schicht ist keine Uebergabe. Fuer eine bestehende
    Zeile entscheidet allein ``editable_until`` (Refs #2042, R-E-15) — sonst
    haenge das Schreibrecht doch wieder an einer nachtraeglich verschobenen
    Schichtgrenze.

    Das obere Ende ist **offen** (``<``, Befund E-08): R-E-4 sagt
    ``now < editable_until``. ``get_time_range`` bleibt davon unberuehrt — dort
    ist das Ereignis-Fenster weiterhin an beiden Enden geschlossen.
    """
    jetzt = now.astimezone(UTC)
    if ab_schichtbeginn and jetzt < fenster.window_start.astimezone(UTC):
        return False
    return jetzt < fenster.editable_until.astimezone(UTC)


#: Sentinel: „die Zeile wurde noch nicht gelesen" — unterscheidbar von
#: ``None`` (= „nachgesehen, es gibt keine").
_UNGELESEN: Any = object()


def is_shift_editable(
    facility,
    shift: Shift,
    *,
    now=None,
    time_filters: list[TimeFilter] | None = None,
    note: Any = _UNGELESEN,
) -> bool:
    """Ist diese Schicht gerade beschreibbar?

    Die gemeinsame Quelle fuer Anzeige (``_build_block``) und Tests. Fuer eine
    bereits gespeicherte Notiz zaehlen ihre **eingefrorenen** Grenzen, sonst die
    aktuelle Konfiguration — und dann zusaetzlich die Identitaetsregel: ein
    Formular fuer eine Schicht anzubieten, die ``save_note`` gleich wieder
    ablehnen wuerde, waere ein Versprechen ins Leere (E-07).

    ``note`` kann eine bereits geladene Zeile mitbringen; der Anzeigepfad hat
    sie ohnehin in der Hand und spart sich damit die zweite Abfrage.
    """
    jetzt = now or timezone.localtime()
    if note is _UNGELESEN:
        note = get_note(facility, shift)
    if note is not None:
        return _window_is_open(note_window(note), jetzt, ab_schichtbeginn=False)
    fenster = compute_shift_window(facility, shift, time_filters=time_filters)
    return _window_is_open(fenster, jetzt, ab_schichtbeginn=True) and _identitaet_ok(
        facility, shift, time_filters=time_filters
    )


def can_read_notes(user) -> bool:
    """Lesen erst ab Fachkraft.

    Die Assistenz erreicht die Uebergabe (``AssistantOrAboveRequiredMixin``),
    sieht dort aber Kennzahlen und Systemtypen — keinen unstrukturierten
    Freitext ueber Personen. Die Notiz bleibt deshalb bewusst hinter
    ``is_staff_or_above``.
    """
    return bool(getattr(user, "is_staff_or_above", False))


def can_write_notes(user) -> bool:
    """Schreiben mit derselben Grenze wie Lesen (kein Nur-Schreib-Recht)."""
    return can_read_notes(user)


def get_note(facility, shift: Shift) -> ShiftHandoverNote | None:
    """Die Notiz dieser Schicht — oder ``None``."""
    return (
        ShiftHandoverNote.objects.for_facility(facility)
        .filter(shift_date=shift.shift_date, time_filter=shift.time_filter)
        .select_related("author", "updated_by", "time_filter")
        .first()
    )


def decrypt_note(note: ShiftHandoverNote | None) -> str:
    """Klartext der Notiz — ``""``, wenn keine da ist."""
    if note is None or not note.note:
        return ""
    return safe_decrypt(note.note)


def _shift_label_or_none(shift: Shift) -> str | None:
    """Bezeichnung des Zeitfensters bzw. ``None`` fuer die Ganztags-Schicht."""
    return shift.time_filter.label if shift.time_filter is not None else None


#: Name des UniqueConstraints aus Migration 0179. Nur DIESER Konflikt wird beim
#: Erstanlage-Rennen abgefangen (Refs #2042, E-05) — jeder andere
#: Integritaetsfehler bleibt ein Fehler und wird durchgereicht.
_SHIFT_UNIQUE_CONSTRAINT = "uniq_handover_note_per_shift"

#: Fachlich unterstuetzter Datumsbereich einer Schicht (Refs #2042, E-06).
#: Untergrenze: vor 2000 gab es die Anwendung nicht. Obergrenze: zwei Jahre
#: voraus — genug fuer jede Nacherfassung und jeden Dienstplan, aber weit
#: genug vor ``date.max``, dass die Folgetags-Rechnung nicht in einen
#: ``OverflowError`` laeuft.
_MIN_SHIFT_DATE = date(2000, 1, 1)
_MAX_SHIFT_FUTURE = timedelta(days=730)


def _schicht_unbestimmbar() -> ValidationError:
    return ValidationError(_("Die Schicht konnte nicht bestimmt werden. Bitte die Seite neu laden."))


def assert_shift_date_supported(shift: Shift, *, today=None) -> None:
    """Datumsbereich pruefen, BEVOR irgendetwas gerechnet wird (E-06).

    ``shift_date`` kam anfangs ungeprueft aus dem POST. Ein
    syntaktisch gueltiges ``9999-12-31`` liess die Folgetags-Rechnung in einen
    ``OverflowError`` laufen — HTTP 500 statt einer Fachmeldung.
    """
    heute = today or timezone.localdate()
    if shift.shift_date < _MIN_SHIFT_DATE or shift.shift_date > heute + _MAX_SHIFT_FUTURE:
        raise _schicht_unbestimmbar()


def assert_shift_identity(facility, shift: Shift, *, time_filters: list[TimeFilter] | None = None) -> None:
    """Eine Ganztags-Notiz gibt es nur ohne konfigurierte Zeitfilter (E-07).

    ``time_filter = NULL`` ist laut R-E-2/R-E-4 der Kalenderfall — „diese
    Einrichtung hat keine Schichten". Der POST akzeptierte einen leeren Filter
    anfangs bedingungslos und legte damit neben den echten Schichten
    eine zweite, in der Uebergabe unsichtbare Schreibidentitaet an.

    Gilt nur fuer die **Neuanlage**: eine Notiz, die entstand, als es noch
    keine Zeitfilter gab, bleibt bearbeitbar, auch wenn spaeter welche
    konfiguriert werden.
    """
    if shift.time_filter is not None:
        return
    kandidaten = time_filters if time_filters is not None else _active_time_filters(facility)
    if kandidaten:
        raise ValidationError(
            _(
                "Für diese Einrichtung sind Schichten konfiguriert — eine Übergabenotiz "
                "gehört zu einer Schicht, nicht zum ganzen Tag."
            )
        )


#: Revisions-Token fuer „an dieser Stelle gab es keine Zeile".
_KEINE_ZEILE = "-"


def note_revision(note: ShiftHandoverNote | None) -> str:
    """Kennung genau der Fassung, die dem Abgleich vorlag (Refs #2042, R-E-25).

    ``updated_at`` ist der natuerliche Revisionszeiger dieses Modells: es ist
    ohnehin der Marker der letzten **fachlichen** Aenderung (die
    Aufbewahrungsfrist haengt daran), und die Schluesselrotation schreibt ihn
    bewusst NICHT fort — ein Rotationslauf entwertet den Abgleich also nicht.
    Ein Inhalts-Hash taete das: er haengt am Chiffrat, und das wechselt bei
    jeder Rotation, ohne dass sich fachlich etwas geaendert haette.

    Die ``pk`` steht mit davor, damit auch „geloescht und neu angelegt"
    (gleiche Schicht, andere Zeile) als Abweichung auffaellt.
    """
    if note is None:
        return _KEINE_ZEILE
    return f"{note.pk}@{note.updated_at.isoformat()}"


class HandoverNoteConflictError(ValidationError):
    """Der Bestand ist nicht (mehr) der, auf den sich die Eingabe bezieht.

    Zwei Faelle, dieselbe Antwort: das erstmalige Rennen zweier Personen um
    dieselbe Schicht (Refs #2042, R-E-23) und die Aenderung des Bestands
    **waehrend** des Abgleichs (R-E-25). Beide Male traegt die Ausnahme die
    aktuelle Zeile (oder ``None``, wenn sie inzwischen geloescht wurde) und die
    eingereichte Eingabe mit, damit die Oberflaeche beide Fassungen
    nebeneinander zeigen kann. Eine blosse Fehlermeldung genuegt nicht: die
    unterlegene Person haette ihren Entwurf sonst verloren, und das Rennen ist
    genau der Moment, in dem jemand etwas Wichtiges aufgeschrieben hat.
    """

    def __init__(self, message, *, bestand: ShiftHandoverNote | None, eingabe: str):
        super().__init__(message)
        self.bestand = bestand
        self.eingabe = eingabe


def _fenster_zu(fenster: ShiftWindow) -> ValidationError:
    return ValidationError(
        _("Bearbeitung nur bis %(grenze)s möglich — diese Schicht ist abgeschlossen.")
        % {"grenze": date_format(timezone.localtime(fenster.editable_until), "SHORT_DATETIME_FORMAT")}
    )


def _locked_existing_note(facility, shift: Shift) -> ShiftHandoverNote | None:
    """Die vorhandene Zeile dieser Schicht unter Zeilensperre — oder ``None``.

    Eigene Funktion, weil sie die Naht des Erstanlage-Rennens ist: eine noch
    nicht existierende Zeile laesst sich nicht sperren (Refs #2042, E-05).
    """
    return (
        ShiftHandoverNote.objects.select_for_update()
        .filter(facility=facility, shift_date=shift.shift_date, time_filter=shift.time_filter)
        .first()
    )


def _ist_schichtkonflikt(exc: IntegrityError) -> bool:
    """Traegt dieser Integritaetsfehler genau den Schicht-UniqueConstraint?

    psycopg meldet den Constraint-Namen strukturiert; der String-Vergleich ist
    nur der Rueckfall fuer Backends ohne ``diag``.
    """
    diagnose = getattr(getattr(exc, "__cause__", None), "diag", None)
    name = getattr(diagnose, "constraint_name", None)
    if name:
        return name == _SHIFT_UNIQUE_CONSTRAINT
    return _SHIFT_UNIQUE_CONSTRAINT in str(exc)


#: Die handelnde Person gehoert nicht zur Einrichtung der Schicht — oder die
#: Schicht traegt gar keine Einrichtung. ``can_write_notes`` prueft nur die
#: Rolle; die Einrichtungsgrenze zieht der Dienst selbst (fail-closed).
NOTE_FOREIGN_FACILITY_MESSAGE = gettext_lazy("Übergabenotiz gehört zu einer anderen Einrichtung.")


@transaction.atomic
def save_note(
    facility,
    shift: Shift,
    user,
    text: str,
    *,
    now=None,
    base_revision: str | None = None,
) -> ShiftHandoverNote | None:
    """Legt an, aktualisiert oder loescht die Notiz dieser Schicht.

    Leerer Text ist die Loeschung: eine Schicht ohne Mitteilung soll keine
    leere Zeile hinterlassen, die die Folgeschicht als „nichts zu melden"
    fehldeutet. Rueckgabe ist die Zeile bzw. ``None`` nach dem Loeschen.
    """
    now = now or timezone.localtime()
    if not can_write_notes(user):
        raise PermissionDenied(_("Übergabenotizen sind Fachkräften, Leitung und Anwendungsbetreuung vorbehalten."))
    # Einrichtungsgrenze der handelnden Person vor jeder Sperre: die View
    # liefert die Einrichtung aus der Sitzung, in Produktion traegt die
    # Zeilensicherheit mit — der Dienst zieht die Grenze trotzdem selbst.
    # Fail-closed: ohne Einrichtung auf einer der beiden Seiten kein Schreiben.
    facility_id = getattr(facility, "pk", None)
    if facility_id is None or getattr(user, "facility_id", None) != facility_id:
        raise ValidationError(NOTE_FOREIGN_FACILITY_MESSAGE)
    assert_shift_date_supported(shift)

    inhalt = (text or "").strip()
    bestand = _locked_existing_note(facility, shift)

    if base_revision is not None and base_revision != note_revision(bestand):
        # Refs #2042 (R-E-25): Der Abgleich galt einer bestimmten Fassung. Ist
        # sie inzwischen geaendert, ersetzt oder geloescht, wird NICHT
        # geschrieben — die Zeilensperre serialisiert zwar, sagt aber nichts
        # darueber, welche Fassung die Person tatsaechlich gesehen hat. Ein
        # unlesbares oder leeres Revisionsfeld zaehlt als Abweichung: dann ist
        # gerade nicht belegt, dass ueberhaupt verglichen wurde.
        raise HandoverNoteConflictError(
            _(
                "Diese Schichtnotiz wurde geändert, während Sie verglichen haben. "
                "Bitte die jetzt gespeicherte Fassung lesen und erneut entscheiden — "
                "die eigene Eingabe ist im Feld erhalten."
            ),
            bestand=bestand,
            eingabe=inhalt,
        )

    if bestand is None:
        # Neuanlage: die Identitaetsregel greift, und das Fenster kommt aus der
        # gegenwaertigen Konfiguration — sie ist in diesem Moment die Wahrheit
        # und wird gleich mit der Zeile eingefroren.
        assert_shift_identity(facility, shift)
        fenster = compute_shift_window(facility, shift)
        offen = _window_is_open(fenster, now, ab_schichtbeginn=True)
    else:
        # Bestandszeile: ausschliesslich die eingefrorene Grenze entscheidet
        # (Refs #2042, E-01/R-E-15). Eine spaetere Konfigurationsaenderung
        # oeffnet eine geschlossene Notiz nicht wieder.
        fenster = note_window(bestand)
        offen = _window_is_open(fenster, now, ab_schichtbeginn=False)
    if not offen:
        raise _fenster_zu(fenster)

    if not inhalt:
        if bestand is None:
            return None
        note_id = str(bestand.pk)
        bestand.delete()
        audit_event(
            AuditLog.Action.HANDOVER_NOTE_DELETED,
            user=user,
            facility=facility,
            target_type="ShiftHandoverNote",
            target_id=note_id,
            detail={
                "shift_date": shift.shift_date.isoformat(),
                "time_filter": _shift_label_or_none(shift),
                "changed_fields": ["note"],
            },
        )
        return None

    if bestand is None:
        try:
            # Savepoint um genau diesen INSERT: ``select_for_update`` sperrt
            # keine Zeile, die es noch nicht gibt — zwei Faeden koennen beide
            # ``None`` lesen. Die UniqueConstraint haelt die Daten sauber, aber
            # der Verlierer bekam anfangs einen ungefangenen
            # ``IntegrityError`` (HTTP 500). Refs #2042, E-05.
            with transaction.atomic():
                note = ShiftHandoverNote.objects.create(
                    facility=facility,
                    shift_date=shift.shift_date,
                    time_filter=shift.time_filter,
                    note=encrypt_field(inhalt),
                    author=user,
                    updated_by=user,
                    window_start=fenster.window_start,
                    window_end=fenster.window_end,
                    editable_until=fenster.editable_until,
                )
        except IntegrityError as exc:
            if not _ist_schichtkonflikt(exc):
                # Bewusst NICHT jeden Integritaetsfehler verschlucken.
                raise
            bestand = _locked_existing_note(facility, shift)
            if bestand is None:  # pragma: no cover — die Zeile MUSS jetzt da sein
                raise
            if decrypt_note(bestand) == inhalt:
                # Beide wollten dasselbe: fachlich ist nichts schiefgegangen.
                return bestand
            raise HandoverNoteConflictError(
                _(
                    "Für diese Schicht wurde soeben eine andere Notiz gespeichert. "
                    "Bitte beide Fassungen vergleichen: Der gespeicherte Text steht oben, "
                    "die eigene Eingabe ist im Feld erhalten."
                ),
                bestand=bestand,
                eingabe=inhalt,
            ) from exc
        audit_event(
            AuditLog.Action.HANDOVER_NOTE_CREATED,
            user=user,
            facility=facility,
            target_obj=note,
            detail={
                "shift_date": shift.shift_date.isoformat(),
                "time_filter": _shift_label_or_none(shift),
                "changed_fields": ["note"],
            },
        )
        return note

    if decrypt_note(bestand) == inhalt:
        # Kein Diff, keine Audit-Zeile: ein erneutes Speichern desselben Texts
        # ist keine Aenderung, und eine Zeile ohne Aenderung entwertet die
        # Rechenschaftsspur (dieselbe Regel wie ``changed_fields_vs_db``).
        return bestand

    # Refs #2042 Befund E-17): ``updated_at`` ist die Revision, gegen die
    # der Konflikt-Abgleich prueft — und ``auto_now`` ist dafuer keine Zusage.
    # Django setzt dort schlicht ``timezone.now()``, ohne den bisherigen Wert
    # anzusehen. Steht die Uhr still (angehaltene Testuhr, Zeitsprung, groebere
    # Zeitquelle), truegen zwei verschiedene Fassungen dieselbe Revision, und
    # der Abgleich uebersaehe genau den Zwischen-Write, den er verhindern soll.
    # Deshalb hier eine garantierte Untergrenze: mindestens eine Mikrosekunde
    # nach dem bisherigen Stand. Bei laufender Uhr aendert das nichts — es
    # bleibt bei der echten Zeit.
    #
    # Geschrieben wird per ``update()``, nicht ``save()``: ``auto_now`` haengt
    # an ``Field.pre_save`` und ueberschriebe einen explizit gesetzten Wert bei
    # jedem ``save()`` wieder mit ``timezone.now()``. ``QuerySet.update()``
    # schreibt den Wert literal — dasselbe Muster, das die Rotation nutzt.
    # Die Rotation selbst schreibt ``updated_at`` weiterhin NICHT fort: an ihm
    # haengt die Aufbewahrungsfrist.
    neue_revision = max(timezone.now(), bestand.updated_at + timedelta(microseconds=1))
    neues_chiffrat = encrypt_field(inhalt)
    # Der Schreibpfad laeuft ueber ``for_facility`` (Repo-Konvention, Refs
    # #1834), obwohl die pk bereits aus einem facility-gescopten Lookup stammt:
    # ein Update, das NUR die pk kennt, waere die eine Stelle, an der ein
    # spaeterer Umbau still die Mandantengrenze verlieren koennte.
    ShiftHandoverNote.objects.for_facility(facility).filter(pk=bestand.pk).update(
        note=neues_chiffrat,
        updated_by=user,
        updated_at=neue_revision,
    )
    # Die Instanz auf denselben Stand ziehen: sie geht gleich als
    # ``target_obj`` ins Audit und als Rueckgabewert an den Aufrufer.
    bestand.note = neues_chiffrat
    bestand.updated_by = user
    bestand.updated_at = neue_revision
    audit_event(
        AuditLog.Action.HANDOVER_NOTE_UPDATED,
        user=user,
        facility=facility,
        target_obj=bestand,
        detail={
            "shift_date": shift.shift_date.isoformat(),
            "time_filter": _shift_label_or_none(shift),
            "changed_fields": ["note"],
        },
    )
    return bestand


def _range_label_from(fenster: ShiftWindow, shift: Shift) -> str:
    """Schichtzeiten fuer den Notizkopf — in der aktiven Zeitzone.

    Refs #2042 Befund E-12): Die eingefrorenen Grenzen kommen aus
    Postgres mit ``tzinfo=UTC``. Ein direktes ``strftime`` machte daraus im
    Sommer „04:00 – 12:00" statt „06:00 – 14:00" — und zwar erst NACH dem
    Speichern, weil eine noch nicht angelegte Notiz ihre Grenzen lokal aus
    ``get_time_range`` bezieht. Die Instants waren immer richtig, nur ihre
    Darstellung nicht. Das Template rechnet bei Datetime-Ausgaben selbst um;
    hier wird der String gebaut, also muss es hier passieren.
    """
    if shift.time_filter is None:
        return "00:00 – 23:59"
    beginn = timezone.localtime(fenster.window_start)
    ende = timezone.localtime(fenster.window_end)
    return f"{beginn.strftime('%H:%M')} – {ende.strftime('%H:%M')}"


def _build_block(facility, shift: Shift, kind: str, *, now, time_filters) -> NoteBlock:
    note = get_note(facility, shift)
    # Bestandszeile: die bei der Anlage eingefrorenen Grenzen — auch fuer die
    # ANZEIGE. Sonst stuende unter einer geschlossenen Notiz eine Frist, die
    # aus der inzwischen geaenderten Konfiguration stammt (Refs #2042, E-01).
    fenster = (
        note_window(note) if note is not None else compute_shift_window(facility, shift, time_filters=time_filters)
    )
    offen = is_shift_editable(facility, shift, now=now, time_filters=time_filters, note=note)
    return NoteBlock(
        kind=kind,
        shift=shift,
        label=shift.label,
        range_label=_range_label_from(fenster, shift),
        text=decrypt_note(note),
        author=str(note.updated_by or note.author) if note is not None and (note.updated_by or note.author) else None,
        updated_at=note.updated_at if note is not None else None,
        editable=offen,
        editable_until=fenster.editable_until,
    )


def _identitaet_ok(facility, shift: Shift, *, time_filters) -> bool:
    """Darf fuer diese Schicht ueberhaupt neu angelegt werden? (E-07)

    Faellt ``current_shift`` in einer Dienstplan-Luecke auf den Ganztag zurueck,
    obwohl Zeitfilter konfiguriert sind, waere das Formular ein Versprechen,
    das der Service gleich wieder brechen muesste. Dann lieber kein Formular.
    """
    try:
        assert_shift_identity(facility, shift, time_filters=time_filters)
    except ValidationError:
        return False
    return True


def build_handover_note_context(facility, user, displayed_shift: Shift, *, now=None) -> dict | None:
    """Kontext des Notiz-Partials der Uebergabe.

    Liefert ``None`` fuer Rollen ohne Leserecht — die Ansicht rendert dann
    keinen Platzhalter, sondern gar nichts (kein Hinweis darauf, dass es eine
    Notiz gibt).

    Zwei Bloecke: die **angezeigte** Schicht (im Automatikfall die zuletzt
    abgeschlossene — genau die, deren Uebergabe gelesen wird) und die
    **laufende** Schicht, fuer die geschrieben wird. Fallen beide zusammen,
    bleibt es bei einem Block statt zweier identischer Formulare.
    """
    if not can_read_notes(user):
        return None

    now = now or timezone.localtime()
    time_filters = _active_time_filters(facility)
    laufend = current_shift(facility, now=now)

    abgebend = displayed_shift
    if not time_filters and displayed_shift == laufend:
        # Kalenderfall (Refs #2042, E-03): ohne konfigurierte Schichten ist die
        # abgebende Schicht der VORTAG. Vorher fielen angezeigte und laufende
        # Schicht auf denselben Kalendertag zusammen, es entstand nur der
        # ``current``-Block — und die Notiz von gestern, also genau die
        # Uebergabe, war nirgends zu sehen. Eine ausdrueckliche
        # Datumsnavigation bleibt unberuehrt: dort ist ``displayed_shift``
        # bereits ein anderer Tag.
        abgebend = Shift(displayed_shift.shift_date - timedelta(days=1), None)

    blocks: list[NoteBlock] = []
    if abgebend != laufend:
        blocks.append(_build_block(facility, abgebend, "previous", now=now, time_filters=time_filters))
    blocks.append(_build_block(facility, laufend, "current", now=now, time_filters=time_filters))

    return {
        "can_write": can_write_notes(user),
        "day_mode": not time_filters,
        "previous_is_yesterday": abgebend.shift_date == laufend.shift_date - timedelta(days=1),
        "blocks": blocks,
    }

"""Pin-Tests der Prio-1-Testluecken in ``core.forms.fields`` (Welle F, 0.93).

Quelle: Survivor-Triage des ``mutation-delta``-Laufs zur 0.93-Fix-Welle,
Vorgabe ``/work/welle-f-0.93/reports/pin-fields.json`` (25 bestaetigte
Prio-1-Mutanten). Modul unter Test: ``src/core/forms/fields.py`` —
:class:`~core.forms.fields.DstAwareDateTimeField` (Refs #2117).

Die getroffene Luecke ist in allen drei Bloecken dieselbe: die bestehende
Suite (``src/tests/test_timezone_dst.py``) treibt das Feld ausschliesslich
ueber das Ereignis-Formular mit **ISO-Strings** in der **Standard-Zeitzone**
an. Damit bleiben unbetreten:

* der Rueckweg von ``_dst_code`` in die *aktive* (statt der Prozess-)
  Zeitzone sowie der ``None``-Ausgang der Funktion (Normalzeitpunkte
  erreichen ``_dst_code`` ueber das Formular nie);
* saemtliche Nicht-String-Zweige und der komplette
  ``input_formats``-Fallback von ``_naive_value`` (lokalisierte Eingabe
  ``25.10.2026 02:30``);
* die Defensivkante ``getattr(exc, "code", None)`` in ``to_python``, die
  eine code-lose ``ValidationError`` aus ``super()`` durchreicht statt an
  ihr zu zerschellen.

Konventionen wie ``src/tests/test_mutation_pins_091_cut.py``: Zusicherungen
vergleichen EXAKT (``==`` / ``is None``), nie per Teilstring (mutmut erzeugt
``"XX<text>XX"``-Varianten) — wo Text im Spiel ist, wird der
``ValidationError``-Code verglichen, nicht die uebersetzte Meldung;
Sprach- und Zeitzonenkontext werden per ``translation.override("de")`` bzw.
``timezone.override(...)`` festgenagelt; KEINE Imports aus
``test_rls_functional``; keine Datenbank noetig (das Feld ist reine
Eingabelogik), daher kein ``django_db``-Marker. Jeder Test nennt seine
Mutanten in der Docstring samt abweichendem Wert.
"""

import datetime as dt
from zoneinfo import ZoneInfo

import pytest
from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone, translation

from core.forms.fields import DstAwareDateTimeField

BERLIN = ZoneInfo("Europe/Berlin")
UTC = ZoneInfo("UTC")

# Rueckfalltag Europe/Berlin 2026: 25.10., 03:00 -> 02:00 (02:00-02:59 doppelt).
AMBIGUOUS = dt.datetime(2026, 10, 25, 2, 30)
# Vorstelltag Europe/Berlin 2026: 29.03., 02:00 -> 03:00 (02:00-02:59 fehlt).
NONEXISTENT = dt.datetime(2026, 3, 29, 2, 30)
# Stinknormaler Sommertag, weit weg von jeder Umstellung.
ORDINARY = dt.datetime(2026, 5, 16, 10, 0)


def _error_codes(exc):
    """Die Fehlercodes einer ``ValidationError`` — sprachunabhaengig exakt."""
    return [error.code for error in exc.error_list]


# ============================================================================
# DstAwareDateTimeField._dst_code — 7 Mutanten
# ============================================================================


class TestDstAwareDateTimeFieldDstCode:
    """Die Klassifikation der beiden Umstellungs-Grenzfaelle.

    ``_dst_code`` beantwortet fuer einen naiven Wert genau drei Faelle:
    ``"dst_nonexistent"`` (Luecke), ``"dst_ambiguous"`` (doppelte Stunde) und
    ``None`` (alles andere — dann bleibt Djangos Originalfehler stehen,
    fail-honest). Ueber das Formular ist ``_dst_code`` nur an DST-Grenzen
    erreichbar; der ``None``-Ausgang und die Zeitzonen-Herkunft waren deshalb
    komplett ungetestet.
    """

    def test_roundtrip_runs_through_the_active_timezone_not_the_process_zone(self):
        """Pin: xǁDstAwareDateTimeFieldǁ_dst_code__mutmut_4 — ``.astimezone(tz)`` -> ``.astimezone(None)``.
        Pin: xǁDstAwareDateTimeFieldǁ_dst_code__mutmut_6 — ``naive.replace(tzinfo=tz)`` -> ``tzinfo=None``.

        Beide Mutanten tauschen die aktive Django-Zeitzone gegen die
        Prozess-Lokalzeit, die Django beim Settings-Laden auf
        ``TIME_ZONE = "Europe/Berlin"`` setzt. Solange beide Zonen identisch
        sind, faellt das nicht auf — deshalb wird hier per
        ``timezone.override(UTC)`` eine abweichende aktive Zone aktiviert.

        Original: in UTC gibt es weder eine Luecke noch eine doppelte Stunde,
        also ``None`` fuer beide Werte. Mutant 4 rechnet den UTC-Zeitpunkt
        zurueck nach Berlin (10:00 -> 12:00 bzw. 02:30 -> 04:30), Mutant 6
        liest den naiven Wert als Berliner Wanduhrzeit; beide Roundtrips
        landen neben dem Ausgangswert und liefern ``"dst_nonexistent"``.
        """
        # Praemisse des Pins: die Prozess-Lokalzeit ist NICHT UTC (Django setzt
        # os.environ["TZ"] auf TIME_ZONE). Sonst waeren beide Mutanten hier blind.
        assert dt.datetime(2026, 5, 16, 10, 0, tzinfo=dt.UTC).astimezone().utcoffset() != dt.timedelta(0)

        with timezone.override(UTC):
            assert DstAwareDateTimeField._dst_code(ORDINARY) is None
            assert DstAwareDateTimeField._dst_code(NONEXISTENT) is None
            assert DstAwareDateTimeField._dst_code(AMBIGUOUS) is None

    def test_ordinary_times_are_neither_ambiguous_nor_nonexistent(self):
        """Pin: xǁDstAwareDateTimeFieldǁ_dst_code__mutmut_8 — linke Seite ``tzinfo=tz`` -> ``tzinfo=None``.
        Pin: xǁDstAwareDateTimeFieldǁ_dst_code__mutmut_10 — linke Seite ohne ``tzinfo``-Argument.
        Pin: xǁDstAwareDateTimeFieldǁ_dst_code__mutmut_14 — rechte Seite ``tzinfo=tz`` -> ``tzinfo=None``.
        Pin: xǁDstAwareDateTimeFieldǁ_dst_code__mutmut_16 — rechte Seite ohne ``tzinfo``-Argument.

        Alle vier machen eine Vergleichsseite naiv; ``utcoffset()`` ist dann
        ``None``, und ``None != timedelta`` ist immer wahr — ``offsets_differ``
        wird konstant ``True``.

        Original: ``None`` fuer alle drei Zeitpunkte (Sommertag sowie die
        Nachbarstunden des Rueckfalltags). Mutanten: ``"dst_ambiguous"``,
        weil der Roundtrip passt und die entwertete Offset-Pruefung zuschlaegt.
        """
        with timezone.override(BERLIN):
            assert DstAwareDateTimeField._dst_code(ORDINARY) is None
            assert DstAwareDateTimeField._dst_code(dt.datetime(2026, 10, 25, 1, 30)) is None
            assert DstAwareDateTimeField._dst_code(dt.datetime(2026, 10, 25, 3, 30)) is None

    def test_ambiguous_hour_is_detected_regardless_of_the_incoming_fold(self):
        """Pin: xǁDstAwareDateTimeFieldǁ_dst_code__mutmut_11 — linke Seite ohne ``fold=0``.

        Diff: ``naive.replace(tzinfo=tz, fold=0)`` -> ``naive.replace(tzinfo=tz, )``.

        Ohne das explizite ``fold=0`` uebernimmt die linke Vergleichsseite das
        ``fold`` des Eingabewertes. Fuer geparste Werte (immer ``fold=0``) ist
        das identisch; ein direkt durchgereichtes ``datetime`` mit ``fold=1``
        vergleicht beim Mutanten ``fold=1`` gegen ``fold=1``.

        Original: ``"dst_ambiguous"`` fuer beide ``fold``-Lesarten der
        doppelten Stunde. Mutant: ``None`` fuer die ``fold=1``-Variante — die
        mehrdeutige Stunde bekaeme wieder Djangos generische Meldung.
        """
        with timezone.override(BERLIN):
            assert DstAwareDateTimeField._dst_code(AMBIGUOUS.replace(fold=1)) == "dst_ambiguous"
            assert DstAwareDateTimeField._dst_code(AMBIGUOUS.replace(fold=0)) == "dst_ambiguous"

    def test_nonexistent_hour_wins_over_ambiguity(self):
        """Gegenprobe zum ``None``-Ausgang: die Luecke wird als solche benannt.

        Kein eigener Pin — haelt die Aussagekraft der Nachbartests fest, damit
        ein ``return None`` als Rundum-Antwort nicht unbemerkt durchginge.
        """
        with timezone.override(BERLIN):
            assert DstAwareDateTimeField._dst_code(NONEXISTENT) == "dst_nonexistent"
            assert DstAwareDateTimeField._dst_code(None) is None


# ============================================================================
# DstAwareDateTimeField._naive_value — 17 Mutanten
# ============================================================================


class TestDstAwareDateTimeFieldNaiveValue:
    """Die Rekonstruktion des naiven Eingabewerts auf dem Fehlerpfad.

    ``_naive_value`` spiegelt ``DateTimeField.to_python`` fuer den Fall, dass
    Djangos Zeitzonen-Umrechnung geworfen hat. Die bestehende Suite fuettert
    das Formular nur mit ISO-Strings — der ``datetime``-Zweig, der
    ``date``-Zweig, der ``input_formats``-Fallback und die Schutzzeile am Ende
    waren damit vollstaendig ungetestet.
    """

    def test_datetime_input_passes_through_naive_and_drops_aware(self):
        """Pin: xǁDstAwareDateTimeFieldǁ_naive_value__mutmut_1 — ``timezone.is_naive(value)`` -> ``is_naive(None)``.

        Original: der naive Wert kommt unveraendert zurueck, ein zonenbehafteter
        Wert wird zu ``None`` (``_dst_code`` darf nur auf Wanduhrzeiten
        rechnen). Mutant: ``timezone.is_naive(None)`` wertet ``None.utcoffset()``
        aus und wirft ``AttributeError`` — aus einem Feldfehler wuerde ein 500er,
        sobald ein ``datetime`` (statt eines Strings) als Feldwert ankommt.
        """
        field = DstAwareDateTimeField()

        assert field._naive_value(AMBIGUOUS) == AMBIGUOUS
        assert field._naive_value(AMBIGUOUS.replace(tzinfo=BERLIN)) is None

    def test_date_input_becomes_midnight_of_that_day(self):
        """Pin: xǁDstAwareDateTimeFieldǁ_naive_value__mutmut_2 — ``dt.datetime(None, value.month, value.day)``.
        Pin: xǁDstAwareDateTimeFieldǁ_naive_value__mutmut_3 — ``dt.datetime(value.year, None, value.day)``.
        Pin: xǁDstAwareDateTimeFieldǁ_naive_value__mutmut_4 — ``dt.datetime(value.year, value.month, None)``.
        Pin: xǁDstAwareDateTimeFieldǁ_naive_value__mutmut_5 — Jahr faellt weg.
        Pin: xǁDstAwareDateTimeFieldǁ_naive_value__mutmut_6 — Monat faellt weg.
        Pin: xǁDstAwareDateTimeFieldǁ_naive_value__mutmut_7 — Tag faellt weg.

        Original: ``datetime.date`` wird zur Mitternacht desselben Tages.
        Alle sechs Mutanten werfen stattdessen ``TypeError`` (``None`` als
        Datumsteil bzw. zu wenige Positionsargumente). Ueber das Formular ist
        der Zweig unerreichbar, weil Mitternacht in Europe/Berlin nie ein
        DST-Grenzfall ist — hier wird er direkt angefahren.
        """
        field = DstAwareDateTimeField()

        assert field._naive_value(dt.date(2026, 10, 25)) == dt.datetime(2026, 10, 25, 0, 0)
        assert field._naive_value(dt.date(2026, 3, 29)) == dt.datetime(2026, 3, 29, 0, 0)

    def test_localised_input_format_is_parsed_by_the_fallback(self):
        """Pin: xǁDstAwareDateTimeFieldǁ_naive_value__mutmut_12 — ``if parsed is None:`` -> ``is not None``.
        Pin: xǁDstAwareDateTimeFieldǁ_naive_value__mutmut_13 — ``self.input_formats or ()`` -> ``and ()``.
        Pin: xǁDstAwareDateTimeFieldǁ_naive_value__mutmut_14 — ``parsed = self.strptime(...)`` -> ``parsed = None``.
        Pin: xǁDstAwareDateTimeFieldǁ_naive_value__mutmut_15 — ``self.strptime(None, input_format)``.
        Pin: xǁDstAwareDateTimeFieldǁ_naive_value__mutmut_16 — ``self.strptime(text, None)``.
        Pin: xǁDstAwareDateTimeFieldǁ_naive_value__mutmut_17 — ``self.strptime(input_format)``.
        Pin: xǁDstAwareDateTimeFieldǁ_naive_value__mutmut_18 — ``self.strptime(text, )``.
        Pin: xǁDstAwareDateTimeFieldǁ_naive_value__mutmut_19 — ``break`` -> ``return`` (verwirft den Treffer).
        Pin: xǁDstAwareDateTimeFieldǁ_naive_value__mutmut_20 — ``continue`` im ``except`` -> ``break``.

        Die deutsche Formatliste ist ``["%d.%m.%Y %H:%M:%S", "%d.%m.%Y
        %H:%M:%S.%f", "%d.%m.%Y %H:%M", "%d.%m.%Y"]``; ``"25.10.2026 02:30"``
        trifft erst das DRITTE Format — damit stirbt auch der
        ``break``-im-``except``-Mutant (er gibt nach dem ersten Fehlschlag auf).

        Original: das naive ``datetime`` des Rueckfalltags. Alle neun Mutanten
        liefern ``None`` (Fallback uebersprungen, leer gelaufen, Treffer
        verworfen oder ``TypeError`` vom eigenen ``except`` geschluckt).
        """
        field = DstAwareDateTimeField()

        with translation.override("de"):
            assert field._naive_value("25.10.2026 02:30") == AMBIGUOUS
            assert field._naive_value("29.03.2026 02:30") == NONEXISTENT
            # Gegenprobe: der ISO-Weg laeuft weiterhin ueber ``parse_datetime``.
            assert field._naive_value("2026-10-25T02:30") == AMBIGUOUS

    def test_localised_dst_input_keeps_the_own_error_code(self):
        """Pin: xǁDstAwareDateTimeFieldǁ_naive_value__mutmut_12 bis _mutmut_20 (oeffentlicher Pfad).

        Derselbe Fallback, diesmal ueber ``clean()`` statt ueber den Helfer:
        eine lokalisierte Eingabe, die Django selbst ueber ``input_formats``
        akzeptiert und dann als mehrdeutig/nicht existent ablehnt.

        Original: die eigenen Codes ``dst_ambiguous`` / ``dst_nonexistent``.
        Mutanten: ``_naive_value`` liefert ``None``, ``_dst_code(None)`` ist
        ``None``, und die Fachkraft bekommt wieder Djangos generischen
        ``ambiguous_timezone``-Code statt der Handlungsanweisung. Verglichen
        wird der Code, nicht der uebersetzte Text — sprachunabhaengig exakt.
        """
        field = DstAwareDateTimeField()

        with translation.override("de"), timezone.override(BERLIN):
            with pytest.raises(ValidationError) as ambiguous:
                field.clean("25.10.2026 02:30")
            with pytest.raises(ValidationError) as nonexistent:
                field.clean("29.03.2026 02:30")

        assert _error_codes(ambiguous.value) == ["dst_ambiguous"]
        assert _error_codes(nonexistent.value) == ["dst_nonexistent"]

    def test_unparsable_and_aware_strings_yield_none(self):
        """Pin: xǁDstAwareDateTimeFieldǁ_naive_value__mutmut_21 — ``parsed is None or is_aware(parsed)`` -> ``and``.

        Original: beide Faelle enden sauber bei ``None`` — nicht parsebar bzw.
        bereits zonenbehaftet (``_dst_code`` wuerde sonst auf einem
        aware-Wert rechnen). Mutant: fuer den unparsbaren Text wertet er
        ``timezone.is_aware(None)`` aus und wirft ``AttributeError``; fuer den
        Offset-String gibt er das aware ``datetime``
        ``2026-10-25 02:30+02:00`` zurueck statt ``None``.
        """
        field = DstAwareDateTimeField()

        with translation.override("de"):
            assert field._naive_value("kein Datum") is None
            assert field._naive_value("2026-10-25T02:30+02:00") is None
            assert field._naive_value(12345) is None


# ============================================================================
# DstAwareDateTimeField.to_python — 1 Mutant
# ============================================================================


class TestDstAwareDateTimeFieldToPython:
    """Die Defensivkante am ``code``-Zugriff der aufgefangenen Fehlermeldung."""

    def test_codeless_validation_error_from_super_is_passed_through(self, monkeypatch):
        """Pin: xǁDstAwareDateTimeFieldǁto_python__mutmut_6 — ``getattr``-Default entfaellt.

        Diff: ``getattr(exc, "code", None)`` -> ``getattr(exc, "code")``.

        Djangos ``ValidationError`` setzt ``.code`` NUR im Skalar-Zweig von
        ``__init__``; in Listen-/Dict-Form (hier: ``ValidationError(["kaputt"])``)
        existiert das Attribut nicht. Der Default ``None`` ist deshalb echte
        Schutzmassnahme, keine Beigabe.

        Original: die code-lose Original-``ValidationError`` wird unveraendert
        durchgereicht (``messages == ["kaputt"]``). Mutant: ``getattr`` ohne
        Default wirft ``AttributeError`` — im Formularpfad ein 500er statt
        eines Feldfehlers; der ``pytest.raises(ValidationError)``-Block hier
        schlaegt damit fehl.
        """

        def _raise_codeless(self, value):
            raise ValidationError(["kaputt"])

        monkeypatch.setattr(forms.DateTimeField, "to_python", _raise_codeless)
        field = DstAwareDateTimeField()

        assert not hasattr(ValidationError(["kaputt"]), "code")
        with pytest.raises(ValidationError) as excinfo:
            field.to_python("2026-10-25T02:30")

        assert excinfo.value.messages == ["kaputt"]

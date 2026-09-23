"""Pin-Tests der Welle L (0.99, Refs #2174, Refs #2193) — Testlücken in
``core.services.system.field_types``, Batch ``system-1``.

Quelle: ``welle-l-0.99/reports/m-scratch/batch-system-1.json`` (64
Survivors/No-Tests über die Default-Wert-Validatoren und die
Import-Koerzierungs-Adapter der Feldtyp-Registry). Triage:
``welle-l-0.99/reports/m-scratch/pins-system-1/triage-099-system-1.json``.

Bestehende Tests (``test_field_types_registry.py``) prüfen für die meisten
Validatoren nur ``pytest.raises(ValidationError)`` ohne den ``default_value``-
Schlüssel oder den exakten Text zu verifizieren. Ein Mutant, der das
Fehler-Dict auf einen falschen Schlüssel (``"XXdefault_valueXX"``/
``"DEFAULT_VALUE"``) kippt oder ``ValidationError(None)`` statt des Dicts
wirft, entgeht dieser losen Prüfung — die betroffene Fehlermeldung würde in
Django-Admin-Formularen nicht mehr am ``default_value``-Feld angezeigt.
Deshalb pinnen die ``*_exact_message``-Tests hier den vollständigen
``message_dict`` (Schlüssel UND Wortlaut) statt nur ``pytest.raises``; das
tötet als Nebeneffekt auch die reinen ``XX…XX``/Groß-Klein-Textvarianten
(mutmut-Marker), ohne die von ``pytest.raises(match=…)`` uebersehen würden.

* **``x__validate_number__mutmut_7``/``…_8``/``…_9``**: Textvarianten der
  Meldung "Default-Wert muss eine ganze Zahl sein." — gepinnt über den
  exakten ``message_dict``-Vergleich.
* **``x_validate_number_value__mutmut_10``/``…_11``/``…_12``**: Textvarianten
  von "Wert muss eine ganze Zahl sein." (Bool-/Nicht-Zahl-Zweig).
* **``x_validate_number_value__mutmut_15``**: ``value < min_value`` kippt zu
  ``value <= min_value`` — ein Default-Wert GENAU auf der Untergrenze würde
  fälschlich abgelehnt.
* **``x_validate_number_value__mutmut_19``/``…_20``**: Textvarianten von
  "Wert muss mindestens %(minimum)d sein.".
* **``x_validate_number_value__mutmut_26``**: ``value > max_value`` kippt zu
  ``value >= max_value`` — ein Default-Wert GENAU auf der Obergrenze würde
  fälschlich abgelehnt.
* **``x__validate_date__mutmut_2``…``…_8``**: ``ValidationError(None)``,
  Schlüssel-Umbenennung und Textvarianten der ISO-Datum-Meldung.
* **``x__validate_time__mutmut_2``…``…_8``**: dieselbe Lücke für die
  ISO-Uhrzeit-Meldung.
* **``x__validate_boolean__mutmut_5``/``…_6``**: die Menge gültiger Werte
  verliert ``"false"`` (auf ``"XXfalseXX"``/``"FALSE"`` mutiert) — ein
  Default-Wert ``"false"`` würde fälschlich abgelehnt.
* **``x__validate_boolean__mutmut_7``**: dieselbe Lücke für ``"1"``.
* **``x__validate_boolean__mutmut_8``**: dieselbe Lücke für ``"0"``.
* **``x__validate_boolean__mutmut_9``…``…_15``**: ``ValidationError(None)``,
  Schlüssel-Umbenennung und Textvarianten der Boolean-Meldung.
* **``x__validate_select__mutmut_12``/``…_13``**: Textvarianten der
  Options-Slug-Meldung.
* **``x__validate_multi_select__mutmut_9``…``…_15``** (9/10/11/14/15):
  dieselbe Lücke für den Multi-Select-Zweig.
* **``x__validate_file__mutmut_1``…``…_7``**: ``_validate_file`` ist über den
  Modell-Pfad unerreichbar (``FieldTemplate._validate_default_value`` blockt
  ``allows_default=False`` bereits davor mit derselben Meldung), bleibt aber
  Teil des ``FieldTypeSpec``-Vertrags (``validate_default``) — direkter
  Contract-Pin auf die Registry-Funktion.
* **``x__coerce_import_date__mutmut_2``**: das ``column``-Argument fällt auf
  ``None`` — ein Import-Zeilenfehler verliert die Spaltenzuordnung, die
  Fehlermeldung zeigt dem Bearbeitenden nicht mehr, welche Spalte betroffen
  war.
* **``x__coerce_import_time__mutmut_1``/``…_3``/``…_4``**: Argument-Anzahl
  bzw. ``raw``-Wert kippen — der Aufruf bricht mit unbehandeltem
  ``TypeError``/falschem Wert ab statt eines kontrollierten
  ``_CellError``/Ergebnisses.
* **``x__coerce_import_time__mutmut_2``**: dieselbe ``column``-Lücke wie bei
  ``_coerce_import_date``.
* **``x__coerce_import_boolean__mutmut_2``**: dieselbe ``column``-Lücke.
* **``x__coerce_import_select__mutmut_3``**: dieselbe ``column``-Lücke.
* **``x__coerce_import_multi_select__mutmut_1``/``…_2``/``…_4``…``…_8``**:
  ``template``/``raw``-Werte kippen auf ``None`` (Absturz statt Ergebnis)
  oder der Split-Delimiter ``","`` kippt auf ``raw.split(None)``/
  ``"XX,XX"`` — Komma-getrennte Options-Werte würden beim CSV-Import falsch
  zerlegt und keiner aktiven Option mehr zugeordnet.
* **``x__coerce_import_multi_select__mutmut_3``**: dieselbe ``column``-Lücke.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.core.exceptions import ValidationError

from core.services.import_engine import _CellError
from core.services.system import field_types

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


# ---------------------------------------------------------------------
# _validate_number / validate_number_value
# ---------------------------------------------------------------------


def test_validate_number_invalid_raw_exact_message():
    """Pins ``x__validate_number__mutmut_7``/``…_8``/``…_9``: nicht-ganzzahliger
    Default-Wert liefert exakt ``{"default_value": ["Default-Wert muss eine
    ganze Zahl sein."]}`` — nicht nur irgendeine ``ValidationError``."""
    with pytest.raises(ValidationError) as exc:
        field_types._validate_number("abc", {})
    assert exc.value.message_dict == {"default_value": ["Default-Wert muss eine ganze Zahl sein."]}


def test_validate_number_value_non_integer_exact_message():
    """Pins ``x_validate_number_value__mutmut_10``/``…_11``/``…_12``: ein
    boolescher Wert (kein echter ``int``) liefert exakt "Wert muss eine ganze
    Zahl sein." am ``default_value``-Schlüssel."""
    with pytest.raises(ValidationError) as exc:
        field_types.validate_number_value(True, None, None, error_key="default_value")
    assert exc.value.message_dict == {"default_value": ["Wert muss eine ganze Zahl sein."]}


def test_validate_number_value_min_boundary_is_valid():
    """Pins ``x_validate_number_value__mutmut_15``: ein Wert GENAU auf
    ``min_value`` ist gültig (``<`` statt ``<=``) — darf NICHT ablehnen."""
    field_types.validate_number_value(5, 5, None, error_key="default_value")  # no raise


def test_validate_number_value_below_min_exact_message():
    """Pins ``x_validate_number_value__mutmut_19``/``…_20``: ein Wert
    unterhalb ``min_value`` liefert exakt "Wert muss mindestens 5 sein."."""
    with pytest.raises(ValidationError) as exc:
        field_types.validate_number_value(3, 5, None, error_key="default_value")
    assert exc.value.message_dict == {"default_value": ["Wert muss mindestens 5 sein."]}


def test_validate_number_value_max_boundary_is_valid():
    """Pins ``x_validate_number_value__mutmut_26``: ein Wert GENAU auf
    ``max_value`` ist gültig (``>`` statt ``>=``) — darf NICHT ablehnen."""
    field_types.validate_number_value(10, None, 10, error_key="default_value")  # no raise


# ---------------------------------------------------------------------
# _validate_date / _validate_time
# ---------------------------------------------------------------------


def test_validate_date_invalid_raw_exact_message():
    """Pins ``x__validate_date__mutmut_2``…``…_8``: ein nicht-ISO-Datum liefert
    exakt ``{"default_value": ["Default-Wert muss ein ISO-Datum sein
    (YYYY-MM-DD)."]}``."""
    with pytest.raises(ValidationError) as exc:
        field_types._validate_date("nicht-valide", {})
    assert exc.value.message_dict == {"default_value": ["Default-Wert muss ein ISO-Datum sein (YYYY-MM-DD)."]}


def test_validate_time_invalid_raw_exact_message():
    """Pins ``x__validate_time__mutmut_2``…``…_8``: eine nicht-ISO-Uhrzeit
    liefert exakt ``{"default_value": ["Default-Wert muss eine ISO-Uhrzeit
    sein (HH:MM oder HH:MM:SS)."]}``."""
    with pytest.raises(ValidationError) as exc:
        field_types._validate_time("nicht-valide", {})
    assert exc.value.message_dict == {
        "default_value": ["Default-Wert muss eine ISO-Uhrzeit sein (HH:MM oder HH:MM:SS)."]
    }


# ---------------------------------------------------------------------
# _validate_boolean
# ---------------------------------------------------------------------


def test_boolean_accepts_false():
    """Pins ``x__validate_boolean__mutmut_5``/``…_6``: ``"false"`` muss als
    gültiger Default-Wert akzeptiert werden (kein Raise)."""
    field_types._validate_boolean("false", {})  # no raise


def test_boolean_accepts_one():
    """Pins ``x__validate_boolean__mutmut_7``: ``"1"`` muss als gültiger
    Default-Wert akzeptiert werden (kein Raise)."""
    field_types._validate_boolean("1", {})  # no raise


def test_boolean_accepts_zero():
    """Pins ``x__validate_boolean__mutmut_8``: ``"0"`` muss als gültiger
    Default-Wert akzeptiert werden (kein Raise)."""
    field_types._validate_boolean("0", {})  # no raise


def test_boolean_invalid_raw_exact_message():
    """Pins ``x__validate_boolean__mutmut_9``…``…_15``: ein ungültiges Wort
    liefert exakt ``{"default_value": ["Default-Wert muss 'true' oder
    'false' sein."]}``."""
    with pytest.raises(ValidationError) as exc:
        field_types._validate_boolean("vielleicht", {})
    assert exc.value.message_dict == {"default_value": ["Default-Wert muss 'true' oder 'false' sein."]}


# ---------------------------------------------------------------------
# _validate_select / _validate_multi_select
# ---------------------------------------------------------------------


def test_select_invalid_raw_exact_message():
    """Pins ``x__validate_select__mutmut_12``/``…_13``: ein inaktiver/unbekannter
    Slug liefert exakt ``{"default_value": ["Default-Wert 'bad' ist kein
    aktiver Options-Slug."]}``."""
    context = {"options_json": [{"slug": "ok", "is_active": True}]}
    with pytest.raises(ValidationError) as exc:
        field_types._validate_select("bad", context)
    assert exc.value.message_dict == {"default_value": ["Default-Wert 'bad' ist kein aktiver Options-Slug."]}


def test_multi_select_invalid_raw_exact_message():
    """Pins ``x__validate_multi_select__mutmut_9``/``…_10``/``…_11``/``…_14``/
    ``…_15``: derselbe exakte Fehlertext für den Multi-Select-Zweig, hier für
    den ersten unbekannten Token ``"bad"``."""
    context = {"options_json": [{"slug": "ok", "is_active": True}]}
    with pytest.raises(ValidationError) as exc:
        field_types._validate_multi_select("bad", context)
    assert exc.value.message_dict == {"default_value": ["Default-Wert 'bad' ist kein aktiver Options-Slug."]}


# ---------------------------------------------------------------------
# _validate_file
# ---------------------------------------------------------------------


def test_validate_file_always_raises_exact_message():
    """Pins ``x__validate_file__mutmut_1``…``…_7``: die Registry-Funktion für
    FILE lehnt JEDEN Default-Wert mit exakt ``{"default_value": ["Für
    Datei-Felder ist kein Default-Wert zulässig."]}`` ab — Contract-Pin,
    auch wenn ``FieldTemplate._validate_default_value`` denselben Fall über
    ``allows_default=False`` bereits vorher abfängt."""
    with pytest.raises(ValidationError) as exc:
        field_types._validate_file("beliebig", {})
    assert exc.value.message_dict == {"default_value": ["Für Datei-Felder ist kein Default-Wert zulässig."]}


# ---------------------------------------------------------------------
# _coerce_import_date / _coerce_import_time / _coerce_import_boolean /
# _coerce_import_select
# ---------------------------------------------------------------------


def test_coerce_import_date_invalid_column_attribution():
    """Pins ``x__coerce_import_date__mutmut_2``: der ``_CellError`` einer
    ungültigen CSV-Zelle trägt die tatsächlich übergebene Spalte, nicht
    ``None``."""
    with pytest.raises(_CellError) as exc:
        field_types._coerce_import_date(None, "nicht-valide", "Stichtag")
    assert exc.value.column == "Stichtag"


def test_coerce_import_time_valid_returns_iso():
    """Pins ``x__coerce_import_time__mutmut_1``/``…_3``/``…_4``: ein gültiger
    Uhrzeit-Wert liefert das erwartete ISO-Ergebnis — die Mutanten ändern
    ``raw`` auf ``None`` bzw. die Argumentzahl und brechen mit einer anderen
    Exception ab."""
    assert field_types._coerce_import_time(None, "09:30", "Uhrzeit") == "09:30:00"


def test_coerce_import_time_invalid_column_attribution():
    """Pins ``x__coerce_import_time__mutmut_2``: der ``_CellError`` trägt die
    tatsächlich übergebene Spalte, nicht ``None``."""
    with pytest.raises(_CellError) as exc:
        field_types._coerce_import_time(None, "nicht-valide", "Uhrzeit")
    assert exc.value.column == "Uhrzeit"


def test_coerce_import_boolean_invalid_column_attribution():
    """Pins ``x__coerce_import_boolean__mutmut_2``: der ``_CellError`` trägt
    die tatsächlich übergebene Spalte, nicht ``None``."""
    with pytest.raises(_CellError) as exc:
        field_types._coerce_import_boolean(None, "vielleicht", "Aktiv")
    assert exc.value.column == "Aktiv"


def test_coerce_import_select_invalid_column_attribution():
    """Pins ``x__coerce_import_select__mutmut_3``: der ``_CellError`` einer
    leeren Zelle trägt die tatsächlich übergebene Spalte, nicht ``None``."""
    with pytest.raises(_CellError) as exc:
        field_types._coerce_import_select(None, "", "Status")
    assert exc.value.column == "Status"


# ---------------------------------------------------------------------
# _coerce_import_multi_select
# ---------------------------------------------------------------------


def test_coerce_import_multi_select_valid_splits_and_maps():
    """Pins ``x__coerce_import_multi_select__mutmut_1``/``…_2``/``…_4``…``…_8``:
    ein komma-getrennter Default-Wert wird pro Token auf den aktiven
    Options-Slug gemappt. Der ``template``/``raw``-auf-``None``-Kippschalter
    lässt die Registry-Funktion mit ``AttributeError`` abstürzen; der
    Split-Delimiter-Kippschalter (``raw.split(None)``/``"XX,XX"``) zerlegt
    ``"a, b"`` nicht mehr korrekt in zwei Token und wirft ``_CellError``
    statt des erwarteten Ergebnisses."""
    template = SimpleNamespace(
        options_json=[
            {"slug": "a", "label": "A", "is_active": True},
            {"slug": "b", "label": "B", "is_active": True},
        ]
    )
    assert field_types._coerce_import_multi_select(template, "a, b", "Themen") == ["a", "b"]


def test_coerce_import_multi_select_invalid_column_attribution():
    """Pins ``x__coerce_import_multi_select__mutmut_3``: der ``_CellError``
    für einen unbekannten Options-Token trägt die tatsächlich übergebene
    Spalte, nicht ``None``."""
    template = SimpleNamespace(options_json=[{"slug": "a", "label": "A", "is_active": True}])
    with pytest.raises(_CellError) as exc:
        field_types._coerce_import_multi_select(template, "unbekannt", "Themen")
    assert exc.value.column == "Themen"

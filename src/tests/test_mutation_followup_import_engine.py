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

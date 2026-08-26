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

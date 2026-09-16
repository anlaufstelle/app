"""Pin-Test der Welle L (0.99, Refs #2174/#2193) — Prio-2-Testluecke in
``core.forms.fields``, Batch ``forms-3`` (Vollmutation-Survivor-Triage).

Quelle: ``welle-l-0.99/reports/m-scratch/batch-forms-3.json`` (Triage
``triage-099-forms-3.json``).

* **``xǁDstAwareDateTimeFieldǁ__init____mutmut_4``**:
  ``super().__init__(*args, **kwargs)`` wird zu ``super().__init__(*args,
  )`` — ``**kwargs`` faellt komplett weg. Die reale Verwendung
  (``core/forms/events.py::EventMetaForm.occurred_at``) uebergibt ``label``,
  ``resolve_ambiguous`` und ``widget`` ausschliesslich als Keyword — unter
  dem Mutanten erreichen ``label`` und ``widget`` die Basisklasse
  (``forms.DateTimeField``/``forms.Field``) nicht mehr, das Feld faellt auf
  Djangos Default-Label und -Widget zurueck.

``_encryption_key`` liegt modulweit an (Lehre 112): der Mutantenbaum hat
keine ``.env`` und damit keinen ``ENCRYPTION_KEY``. Diese Datei braucht
keine Datenbank (reine Feld-Konstruktion) — der Marker steht trotzdem
gemaess Briefing.
"""

from __future__ import annotations

import pytest
from django import forms

from core.forms.fields import DstAwareDateTimeField

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def test_init_reicht_keyword_argumente_an_die_basisklasse_durch():
    """Pins ``xǁDstAwareDateTimeFieldǁ__init____mutmut_4``: ``label``,
    ``required`` und ``widget`` muessen bei der Basisklasse ankommen."""
    widget = forms.DateTimeInput(attrs={"type": "datetime-local"})

    field = DstAwareDateTimeField(label="Testzeitpunkt", required=False, widget=widget)

    assert str(field.label) == "Testzeitpunkt"
    assert field.required is False
    assert field.widget.input_type == "datetime-local"

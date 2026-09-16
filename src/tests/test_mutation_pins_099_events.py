"""Pin-Test der Welle L (0.99, Refs #2174/#2193) — Prio-3-Testluecke in
``core.forms.events``, Batch ``forms-3`` (Vollmutation-Survivor-Triage).

Quelle: ``welle-l-0.99/reports/m-scratch/batch-forms-3.json`` (Triage
``triage-099-forms-3.json``).

* **``xǁEventMetaFormǁclean__mutmut_39``**: die Rueckfrage-Meldung am Feld
  ``occurred_at_fold`` (Rueckfalltag mit doppelter Stunde) bekommt einen
  XX-Marker. Der BESTEHENDE Pin
  (``src/tests/test_timezone_dst.py::TestEventFormOccurrenceFold::
  test_ambiguous_hour_without_choice_asks_at_the_fold_field``) prueft nur
  Teilstrings (``"zweimal" in text``, ``"Sommer- oder Winterzeit" in
  text``) — beide bleiben auch im XX-markierten Mutanten
  (``"XX" + Text + "XX"``) als Teilstring erhalten, darum ueberlebt der
  Mutant dort. Dieser Test vergleicht die Meldung EXAKT.

``_encryption_key`` liegt modulweit an (Lehre 112): der Mutantenbaum hat
keine ``.env`` und damit keinen ``ENCRYPTION_KEY``.
"""

from __future__ import annotations

import pytest

from core.forms.events import EventMetaForm

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def test_fold_required_meldung_hat_den_exakten_deutschen_text(facility, staff_user, doc_type_contact):
    """Pins ``xǁEventMetaFormǁclean__mutmut_39``: die Rueckfrage-Meldung am
    ``occurred_at_fold``-Feld muss GENAU dem deutschen Text entsprechen."""
    form = EventMetaForm(
        data={
            "document_type": str(doc_type_contact.pk),
            "occurred_at": "2026-10-25T02:30",
            "client": "",
        },
        facility=facility,
        user=staff_user,
    )

    assert not form.is_valid()
    assert form.errors["occurred_at_fold"] == [
        "Diese Uhrzeit gibt es am Rückfalltag zweimal — bitte Sommer- oder Winterzeit wählen."
    ]

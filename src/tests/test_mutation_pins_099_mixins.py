"""Pin-Test der Welle L (0.99, Refs #2174/#2193) — Prio-3-Testluecke in
``core.forms.mixins``, Batch ``forms-3`` (Vollmutation-Survivor-Triage).

Quelle: ``welle-l-0.99/reports/m-scratch/batch-forms-3.json`` (Triage
``triage-099-forms-3.json``).

* **``xǁPinnedClientMixinǁapply_client_pin__mutmut_13``**: die
  Pin-Manipulations-Meldung („Die Personenzuordnung kann nach dem Anlegen
  nicht geändert werden.") bekommt einen XX-Marker. Die bestehenden Tests
  (``src/tests/test_client_pinning_views.py``) pruefen nur ``in
  response.content.decode()`` — auch der XX-markierte Mutant
  (``"XX" + Text + "XX"``) ENTHAELT den Original-Text als Teilstring, darum
  ueberlebt der Mutant dort. Dieser Test vergleicht die Formularfehler-
  Meldung EXAKT, ueber ``CaseForm`` als Traeger des gemeinsamen
  ``PinnedClientMixin``.

``_encryption_key`` liegt modulweit an (Lehre 112): der Mutantenbaum hat
keine ``.env`` und damit keinen ``ENCRYPTION_KEY``.
"""

from __future__ import annotations

import pytest

from core.forms.cases import CaseForm

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def test_pin_manipulation_meldung_hat_den_exakten_deutschen_text(
    facility, staff_user, client_identified, client_qualified
):
    """Pins ``xǁPinnedClientMixinǁapply_client_pin__mutmut_13``: ein
    manipulierter ``client``-POST-Wert bei gepinntem Formular muss GENAU
    diesen deutschen Text als Feldfehler liefern."""
    form = CaseForm(
        data={"title": "Testfall", "client": str(client_qualified.pk)},
        facility=facility,
        user=staff_user,
        pinned_client=client_identified,
    )

    assert not form.is_valid()
    assert form.errors["client"] == ["Die Personenzuordnung kann nach dem Anlegen nicht geändert werden."]

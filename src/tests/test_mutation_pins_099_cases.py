"""Pin-Tests der Welle L (0.99, Refs #2174/#2193) — Prio-2/3-Testluecken in
``core.forms.cases``, Batch ``forms-3`` (Vollmutation-Survivor-Triage).

Quelle: ``welle-l-0.99/reports/m-scratch/batch-forms-3.json`` (Triage
``triage-099-forms-3.json``). Vier Tests toeten elf Survivor-Mutanten:

* **``xǁCaseFormǁ__init____mutmut_32``/``_34``/``_35``/``_36``**: das
  ``empty_label`` des ``document_type``-Auswahlfelds wird ``None`` bzw. mit
  XX-Marker/Gross-/Kleinschreibung veraendert. ``None`` ist NICHT nur
  kosmetisch: ``ModelChoiceIterator`` haengt die Leer-Option nur an, wenn
  ``empty_label is not None`` (``django/forms/models.py``) — mit ``None``
  verschwindet die Option „— Kein Typ —" komplett aus der Auswahl.
* **``xǁCaseFormǁ__init____mutmut_54``**: ohne ``facility`` wird
  ``document_type_field.queryset`` auf ``None`` statt
  ``DocumentType.objects.none()`` gesetzt. Jeder Zugriff auf die Auswahl
  (Rendern, Validieren) crasht dann mit ``AttributeError`` statt eine leere,
  aber echte Queryset zu liefern (``ModelChoiceIterator.__iter__`` liest
  ``queryset._prefetch_related_lookups``).
* **``xǁCaseFormǁclean_client__mutmut_7``/``_8``/``_9``/``_10``/``_11``**:
  die eigene defensive Pruefung ``if not client_id: raise
  ValidationError(...)`` in ``clean_client`` ist ueber die normale
  ``is_valid()``-Kette NICHT erreichbar — das ``client``-Feld traegt
  ``required=True`` mit DERSELBEN deutschen Meldung als
  ``error_messages["required"]`` und weist einen leeren/fehlenden Submit
  schon auf Feldebene ab (empirisch verifiziert: der Feld-Fehler traegt
  ``code="required"``, waehrend ``clean_client``s eigene ValidationError
  ``code=None`` traegt). Die Zeile ist trotzdem Teil des Methodenvertrags —
  der direkte Aufruf haelt ihn fest: ``ValidationError(None)`` (mutmut_7,
  der Meldungstext wird literal ``"None"``), ``_(None)`` (mutmut_8, wirft
  ``AttributeError`` VOR der ValidationError — Crash statt Formularfehler),
  XX-Marker/Klein-/Grossschreibung (mutmut_9/_10/_11).
* **``xǁCaseFormǁclean_client__mutmut_17``**: der ``missing_error`` von
  ``resolve_client`` (bei einer wohlgeformten, aber nicht existierenden
  Client-UUID) bekommt einen XX-Marker.

``_encryption_key`` liegt modulweit an (Lehre 112): der Mutantenbaum hat
keine ``.env`` und damit keinen ``ENCRYPTION_KEY``.
"""

from __future__ import annotations

import uuid

import pytest
from django.core.exceptions import ValidationError

from core.forms.cases import CaseForm

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def test_document_type_leerauswahl_hat_den_exakten_deutschen_text(facility):
    """Pins ``xǁCaseFormǁ__init____mutmut_32``/``_34``/``_35``/``_36``:
    ``empty_label`` muss GENAU ``"— Kein Typ —"`` sein — nicht ``None``,
    nicht mit XX-Marker, nicht klein-/grossgeschrieben."""
    form = CaseForm(facility=facility)

    assert str(form.fields["document_type"].empty_label) == "— Kein Typ —"


def test_document_type_queryset_ohne_facility_bleibt_leer_und_iterierbar():
    """Pins ``xǁCaseFormǁ__init____mutmut_54``: ohne ``facility`` muss das
    ``document_type``-Queryset eine ECHTE leere Queryset sein (0 Treffer)
    statt ``None`` — sonst crasht jeder Zugriff (z. B. beim Rendern) mit
    ``AttributeError``."""
    form = CaseForm()

    assert form.fields["document_type"].queryset.count() == 0


def test_clean_client_eigene_pruefung_wirft_den_exakten_deutschen_text(facility, staff_user):
    """Pins ``xǁCaseFormǁclean_client__mutmut_7``/``_8``/``_9``/``_10``/
    ``_11``: die eigene ``if not client_id``-Pruefung in ``clean_client``
    (ueber die normale Feld-Validierung unerreichbar, s. Kopf-Docstring)
    wirft GENAU den deutschen Text — nicht ``None`` als String, kein Crash
    durch ``_(None)``, kein XX-Marker/keine Gross-/Kleinschreibung."""
    form = CaseForm(facility=facility, user=staff_user)
    form.cleaned_data = {"client": None}

    with pytest.raises(ValidationError) as excinfo:
        form.clean_client()

    assert excinfo.value.messages == ["Bitte eine Person auswählen — Fälle müssen einer Person zugeordnet sein."]


def test_clean_client_unbekannte_uuid_liefert_den_exakten_deutschen_text(facility, staff_user):
    """Pins ``xǁCaseFormǁclean_client__mutmut_17``: eine wohlgeformte, aber
    nicht existierende Client-UUID muss GENAU ``"Person existiert nicht."``
    als Feldfehler liefern."""
    form = CaseForm(
        data={"title": "Testfall", "client": str(uuid.uuid4())},
        facility=facility,
        user=staff_user,
    )

    assert not form.is_valid()
    assert form.errors["client"] == ["Person existiert nicht."]

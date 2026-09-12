"""Pin-Tests der Welle K (0.98, Refs #2193) — Prio-2-Testlücken in
``core.services.case.cases``, Batch ``case-client``.

Quelle: ``welle-k-0.98/reports/c-scratch/batch-case-client.json``
(``triage-097-case-rest.json``). Drei Survivors, keine Überschneidung mit den
in ``test_mutation_pins_097_case_rest.py`` bereits gepinnten Mutanten (dort:
Zeilensperren der Personenzeile, ``data_json``-Whitelist-Zieltreue,
Audit-Zurechnung — hier: Default-Parameterwerte und der effektive
Dokumentationstyp bei einem reinen ``data_json``-Update).

* **``x_create_case__mutmut_1``**: der Default-Wert von ``description``
  ändert sich von ``""`` auf den mutmut-Marker ``"XXXX"``. Ruft ein Aufrufer
  ``create_case()`` ohne ``description`` auf, landet der Literaltext
  ``"XXXX"`` als Fallbeschreibung in der DB statt eines leeren Felds.
* **``x_update_case__mutmut_34``/``…_36``**: ``fields.get("document_type",
  case.document_type)`` liefert bei fehlendem ``document_type``-Kwarg
  ``None`` statt des BESTEHENDEN Typs. Ein reines ``data_json``-Update an
  einem Fall MIT Dokumentationstyp schlägt dann fälschlich mit
  ``ValidationError`` fehl (``clean_dynamic_data`` behandelt
  ``document_type=None`` bei wahrheitswertigem ``raw_data`` als „kein Typ").
"""

from __future__ import annotations

import pytest

from core.services.case.cases import create_case, update_case

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def test_create_case_ohne_description_speichert_leerstring(facility, staff_user, client_identified):
    """Pins ``x_create_case__mutmut_1``: der Default-Parameterwert von
    ``description`` wird zum Literal ``"XXXX"``."""
    case = create_case(facility, staff_user, client_identified, title="Pin098-Fall")

    assert case.description == ""


def test_update_case_data_json_only_behaelt_den_bestehenden_dokumenttyp(
    facility, staff_user, client_identified, doc_type_case_form
):
    """Pins ``x_update_case__mutmut_34`` UND ``…_36``: ``document_type`` NICHT
    in ``fields`` -> der effektive Typ muss der BESTEHENDE Typ des Falls
    bleiben. Unter dem Mutanten wird er ``None`` und ``clean_dynamic_data``
    wirft ``ValidationError``, obwohl nur ``data_json`` geändert wird."""
    case = create_case(
        facility,
        staff_user,
        client_identified,
        title="Pin098-Fall-Typwechsel",
        document_type=doc_type_case_form,
        data_json={"anlass": "Erstkontakt"},
    )

    update_case(case, staff_user, data_json={"anlass": "Zweitkontakt"})

    case.refresh_from_db()
    assert case.document_type_id == doc_type_case_form.pk
    assert case.data_json == {"anlass": "Zweitkontakt"}

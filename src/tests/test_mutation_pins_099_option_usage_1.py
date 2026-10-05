"""Pin-Tests der Welle L (0.99) — Vollmutation #2174, Batch ``option_usage-1``.

Refs #2174.
Refs #2193.

Quelle: Vollmutations-Lauf auf main@c2fae01d (frischer Mutantenbaum), Batch
``option_usage-1`` aus ``reports/m-scratch/batch-option_usage-1.json`` — drei
Survivor-Mutanten in ``core.services.option_usage``: ``_collect_option_values``
(1) und ``_dynamic_values`` (2).

Zwei Mutationsachsen:

* ``x__collect_option_values__mutmut_4``: das explizite ``default=None`` an
  ``safe_decrypt`` entfaellt — bei einem korrupten Fernet-Token faellt die
  Funktion dann auf ``safe_decrypt``s EIGENEN Funktions-Default
  ``"[verschlüsselt]"`` zurueck, der anschliessend faelschlich als benutzter
  Options-Slug in die Rueckgabemenge aufgenommen wird (Prio 2, fachliches
  Verhalten: falscher Rueckgabewert von ``option_slugs_in_use``).
* ``x__dynamic_values__mutmut_8``/``_9``: je ein Filter der Abfrage entfaellt.
  ``_8`` verliert ``facility=template.facility`` — Werte einer FREMDEN
  Einrichtung wuerden mitgezaehlt (Mandantengrenze, RLS wird in der
  Testsuite nicht durchgesetzt; Prio 1). ``_9`` verliert
  ``data_json__has_key=template.slug`` — Datensaetze OHNE diesen Slug in
  ``data_json`` liefern dann einen zusaetzlichen ``None``-Eintrag im
  generierten Strom (Prio 2, fachliches Verhalten: Rueckgabewert der
  Generatorfunktion weicht von ihrem dokumentierten Auswahlvertrag ab).

Konventionen wie ``test_mutation_pins_099_delete_blockers_a.py``: Zusicherungen
vergleichen EXAKT (``==``), Fixtures aus ``src/tests/conftest.py`` bzw. echte
Model-Objekte; jeder Test nennt seinen Mutanten mit vollem Namen in der
Docstring. ``_encryption_key`` liegt modulweit an (Lehre 112) — der
Mutantenbaum hat keine ``.env`` und damit keinen ``ENCRYPTION_KEY``.
"""

from __future__ import annotations

import pytest
from django.utils import timezone

from core.models import Client, DocumentType, Event, FieldTemplate
from core.services.option_usage import _collect_option_values, _dynamic_values

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


class TestCollectOptionValuesSuppressesEncryptionFallbackDefault:
    """``_collect_option_values`` uebergibt an ``safe_decrypt`` bewusst
    ``default=None`` — ein korruptes Fernet-Token darf NICHT als
    Options-Slug in die Ergebnismenge wandern."""

    def test_korruptes_token_wird_nicht_als_slug_aufgenommen(self):
        """Pin: ``x__collect_option_values__mutmut_4`` (``default=None``
        entfaellt — ``safe_decrypt`` faellt auf seinen eigenen
        Funktions-Default ``"[verschlüsselt]"`` zurueck, der dann faelschlich
        als benutzter Options-Slug gezaehlt wird)."""
        used: set[str] = set()
        broken = {"__encrypted__": True, "value": "kaputter-token-kein-gueltiges-fernet-format"}

        _collect_option_values(used, broken)

        assert used == set()


class TestDynamicValuesScopedToOwnFacility:
    """``_dynamic_values`` filtert IMMER auf die Einrichtung der Vorlage —
    Werte einer anderen Einrichtung duerfen nicht einfliessen (Mandantengrenze,
    RLS wird in der Testsuite nicht durchgesetzt)."""

    def test_andere_einrichtung_liefert_keine_werte(self, facility, other_facility, staff_user):
        """Pin: ``x__dynamic_values__mutmut_8`` (``facility=template.facility``-
        Filter entfaellt) — ein Ereignis einer FREMDEN Einrichtung mit
        demselben Slug wuerde sonst mitgezaehlt."""
        template = FieldTemplate.objects.create(
            facility=facility, name="Scope-Test", slug="scope-test", field_type=FieldTemplate.FieldType.TEXT
        )
        foreign_doc_type = DocumentType.objects.create(facility=other_facility, name="Fremd-Kontakt")
        foreign_client = Client.objects.create(facility=other_facility, pseudonym="P-Fremd-Scope")
        Event.objects.create(
            facility=other_facility,
            client=foreign_client,
            document_type=foreign_doc_type,
            occurred_at=timezone.now(),
            data_json={"scope-test": "Fremdwert"},
            created_by=staff_user,
        )

        assert list(_dynamic_values(Event, template)) == []


class TestDynamicValuesRequiresSlugKeyPresent:
    """``_dynamic_values`` liest NUR Datensaetze, deren ``data_json`` den
    Options-Slug ueberhaupt als Schluessel enthaelt."""

    def test_datensatz_ohne_slug_liefert_keinen_eintrag(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        """Pin: ``x__dynamic_values__mutmut_9`` (``data_json__has_key``-Filter
        entfaellt) — ein Ereignis OHNE diesen Slug in ``data_json`` wuerde
        sonst als zusaetzlicher (leerer) Eintrag im Strom auftauchen."""
        template = FieldTemplate.objects.create(
            facility=facility, name="Key-Test", slug="key-test", field_type=FieldTemplate.FieldType.TEXT
        )
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"anderer-slug": "Wert"},
            created_by=staff_user,
        )

        assert list(_dynamic_values(Event, template)) == []

"""Pin-Tests der Welle L (0.99) — Vollmutation #2174/#2193, Batch ``config-c``.

Refs https://github.com/anlaufstelle/app/issues/2174,
Refs https://github.com/anlaufstelle/app/issues/2193.

Modul: ``core.forms.config`` (Familie ``forms``). Das Modul ist auf drei
Batches ``config-a``/``config-b``/``config-c`` verteilt (andere Funktionen,
andere Lanes) — diese Datei deckt ausschließlich die ``config-c``-Batch ab:
5 Survivor-Mutanten aus ``FieldGroupForm.clean_name`` (2),
``DocumentTypeFieldAddForm.__init__`` (2) und ``FieldTemplateForm.__init__``
(1). Die vollständige Triage (Klasse ``test``/``skip``/``equivalent``/``bug``
je Mutante, Begründung, Prio) steht in
``reports/m-scratch/pins-config-c/triage-099-config-c.json``; hier stehen nur
die als ``test`` klassifizierten Survivor mit Pin. ``xǁFieldGroupFormǁ
clean_name__mutmut_5`` ist ``equivalent`` (``name`` ist beim Erreichen von
``clean_name()`` immer wahr — Pflichtfeld ohne ``blank=True`` — und
``document_type`` ist eine nicht-nullbare FK, daher liefert die Query in
BEIDEN Fassungen der ``and``/``or``-Bedingung nie einen Treffer) und bleibt
bewusst ungepinnt.

``_encryption_key`` liegt modulweit an: der Mutantenbaum
(``/work/anlaufstelle/mutants``) hat keine ``.env`` und damit keinen
``ENCRYPTION_KEY``; ohne die Fixture wären Pins, deren Formulare
verschlüsselte Felder anfassen, aus dem FALSCHEN Grund rot (Lehre 112).
"""

from __future__ import annotations

import pytest

from core.forms.config import DocumentTypeFieldAddForm, FieldGroupForm, FieldTemplateForm
from core.models import FieldGroup

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


# ---------------------------------------------------------------------------
# FieldGroupForm.clean_name — exakter Fehlertext statt Substring
# (xǁFieldGroupFormǁclean_name__mutmut_16)
# ---------------------------------------------------------------------------


class TestFieldGroupFormCleanNameErrorWording:
    def test_duplicate_name_error_matches_the_exact_wording(self, doc_type_contact):
        """Killt ``xǁFieldGroupFormǁclean_name__mutmut_16`` (String-Marker
        ``_("XXEine Feldgruppe … bereits.XX")`` statt des Originaltexts): eine
        reine Substring-Prüfung (wie ``assert_field_error`` sie macht) findet
        den unveränderten Kernsatz weiterhin als Teilstring des mutierten,
        XX-umklammerten Textes und übersieht die Mutation — deshalb hier ein
        EXAKTER Listenvergleich von ``form.errors["name"]``."""
        FieldGroup.objects.create(document_type=doc_type_contact, name="Doppelt")
        form = FieldGroupForm(data={"name": "Doppelt"}, doc_type=doc_type_contact)

        assert not form.is_valid()
        assert form.errors["name"] == [
            "Eine Feldgruppe mit diesem Namen existiert für diesen Dokumentationstyp bereits."
        ]


# ---------------------------------------------------------------------------
# DocumentTypeFieldAddForm.__init__ — Sortierung der field_group-Auswahl
# (xǁDocumentTypeFieldAddFormǁ__init____mutmut_18 / __mutmut_19)
# ---------------------------------------------------------------------------


class TestDocumentTypeFieldAddFormFieldGroupOrdering:
    def test_field_group_choices_follow_sort_order_before_name(self, doc_type_contact):
        """Killt ``xǁDocumentTypeFieldAddFormǁ__init____mutmut_18``
        (``.order_by("sort_order", "name")`` → ``.order_by("name")``): mit
        gegenläufigem ``sort_order``/``name`` zeigt die Wahl der Feldgruppe
        im Zuordnen-Formular nur bei erhaltenem ``sort_order``-Primat die
        dokumentierte globale Block-Sequenz (Refs #1713) — rein alphabetisch
        (Mutante) vertauscht die beiden Gruppen."""
        FieldGroup.objects.create(document_type=doc_type_contact, name="Zweite", sort_order=1)
        FieldGroup.objects.create(document_type=doc_type_contact, name="Erste", sort_order=2)

        form = DocumentTypeFieldAddForm(doc_type=doc_type_contact)
        names = list(form.fields["field_group"].queryset.values_list("name", flat=True))  # type: ignore[attr-defined]

        assert names == ["Zweite", "Erste"]

    def test_field_group_queryset_order_by_keeps_both_sort_keys(self, doc_type_contact):
        """Killt sowohl ``__mutmut_18`` (``.order_by("name")``, verliert
        ``sort_order``) als auch ``__mutmut_19``
        (``.order_by("sort_order", )``, verliert den ``name``-Tie-Break):
        bei GLEICHEM ``sort_order`` zweier Gruppen ist die DB-Reihenfolge
        ohne den Tie-Break nicht deterministisch — der Nachweis prüft daher
        direkt die konstruierte ORDER-BY-Klausel statt physischer
        Zeilenreihenfolge."""
        form = DocumentTypeFieldAddForm(doc_type=doc_type_contact)

        assert form.fields["field_group"].queryset.query.order_by == (  # type: ignore[attr-defined]
            "sort_order",
            "name",
        )


# ---------------------------------------------------------------------------
# FieldTemplateForm.__init__ — Trennzeichen des used_option_slugs-Attributs
# (xǁFieldTemplateFormǁ__init____mutmut_6)
# ---------------------------------------------------------------------------


class TestFieldTemplateFormUsedOptionSlugsSeparator:
    def test_used_option_slugs_attr_is_space_separated(self, facility):
        """Killt ``xǁFieldTemplateFormǁ__init____mutmut_6``
        (``" ".join(...)`` → ``"XX XX".join(...)``): ``used_option_slugs_attr``
        ist laut Docstring ein vom Browser gelesenes, leerzeichengetrenntes
        data-Attribut, das beim Entfernen/Umbenennen einer verwendeten Option
        warnt (Refs #1875) — ein falsches Trennzeichen bricht das
        clientseitige Split-Parsing dieser Warnfunktion."""
        form = FieldTemplateForm(facility=facility, used_option_slugs=["dauer", "anzahl"])

        assert form.used_option_slugs_attr == "anzahl dauer"

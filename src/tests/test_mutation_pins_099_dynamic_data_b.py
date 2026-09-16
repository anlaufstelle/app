"""Pin-Tests der Welle L (0.99, Vollmutation #2174/#2193) — Testlücken in
``core.services.dynamic_data``, Batch ``dynamic_data-b``.

Quelle: ``welle-l-0.99/reports/m-scratch/batch-dynamic_data-b.json``
(Triage: ``welle-l-0.99/reports/m-scratch/pins-dynamic_data-b/triage-099-dynamic_data-b.json``).
Elf Survivor über sechs Funktionen — Batch ``dynamic_data-a`` (paralleler
Pin-Agent, eigene Datei) deckt den Rest des Moduls ab, keine Überschneidung:

* :func:`build_orphan_fields_display` — ``is_encrypted``/``ft``-Weitergabe an
  ``_display_entry`` beim Aufloesen eines Waisen-Slugs (``_mutmut_26``/``_27``/``_46``).
* :func:`build_field_template_lookup` — Default-Parameterwert von ``ordered``
  und der ``select_related``-Join im unsortierten Zweig (``_mutmut_1``/``_4``).
* :func:`resolve_orphan_field_templates` — die BEIDEN Filter der
  facility-weiten Slug-Auflösung, je einzeln entfernt (``_mutmut_6``/``_7``).
* :func:`clean_dynamic_data` — Pass-Through des ``facility_error``-Kwargs an
  ``check_document_type`` (``_mutmut_6``/``_11``, beobachtbar identisch).
* :func:`is_file_marker` — der ``and``/``or``-Unterschied im Stufe-B-Zweig
  (``_mutmut_13``).
* :func:`remove_restricted_fields` — der Default-Sensitivity-Fallback für ein
  Formularfeld ohne zugeordnetes ``FieldTemplate`` (``_mutmut_9``).

``conftest.py`` bleibt unangetastet (Parallel-Konvention); Testdaten lokal gebaut.
"""

from __future__ import annotations

import pytest
from django import forms
from django.db import connection
from django.test.utils import CaptureQueriesContext

from core.models import DocumentType, DocumentTypeField, FieldGroup, FieldTemplate
from core.services.dynamic_data import (
    build_field_template_lookup,
    build_orphan_fields_display,
    clean_dynamic_data,
    is_file_marker,
    remove_restricted_fields,
    resolve_orphan_field_templates,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def _ft(facility, name, field_type=FieldTemplate.FieldType.TEXT, **kwargs):
    return FieldTemplate.objects.create(facility=facility, name=name, field_type=field_type, **kwargs)


def _assign(doc_type, field_template, *, field_group=None, sort_order=0):
    return DocumentTypeField.objects.create(
        document_type=doc_type,
        field_template=field_template,
        field_group=field_group,
        sort_order=sort_order,
    )


def _count_queries(call) -> int:
    """Query-Zahl eines Aufrufs — Skalierungs-Pin gegen N+1."""
    with CaptureQueriesContext(connection) as ctx:
        call()
    return len(ctx.captured_queries)


# ============================================================================
# build_orphan_fields_display — is_encrypted/ft-Weitergabe (3 Mutanten)
# ============================================================================


class TestBuildOrphanFieldsDisplayResolvedTemplate:
    """Ein verwaister Slug, dessen FieldTemplate facility-weit auflösbar ist.

    Pins ``x_build_orphan_fields_display__mutmut_26`` UND ``…_27`` in EINEM
    Test: beide betreffen denselben ``_display_entry``-Aufruf für den
    aufgelösten Fall (``ft is not None``).
    """

    def test_resolved_orphan_keeps_the_real_encryption_flag_and_the_option_label(self, facility, staff_user):
        """Pin ``x_build_orphan_fields_display__mutmut_26``: ``is_encrypted``
        wird zu ``None`` statt ``ft.is_encrypted`` (hier ``True``).
        Pin ``x_build_orphan_fields_display__mutmut_27``: ``ft`` wird zu
        ``None`` weitergereicht — die SELECT-Formatierung (Slug -> Label)
        entfällt, der rohe Slug erscheint statt des Labels."""
        doc_type = DocumentType.objects.create(facility=facility, name="Profil ohne Zuordnung")
        ft_option = _ft(
            facility,
            "Auswahlfeld",
            field_type=FieldTemplate.FieldType.SELECT,
            is_encrypted=True,
            options_json=[{"slug": "opt-a", "label": "Option A"}, {"slug": "opt-b", "label": "Option B"}],
        )
        # Bewusst KEINE DocumentTypeField-Zuordnung an doc_type — der Slug ist
        # damit sofort verwaist, das FieldTemplate bleibt facility-weit auffindbar.

        entries = build_orphan_fields_display(doc_type, {ft_option.slug: "opt-a"}, staff_user)

        assert len(entries) == 1
        entry = entries[0]
        assert entry["restricted"] is False
        assert entry["is_encrypted"] is True
        assert entry["value"] == "Option A"


class TestBuildOrphanFieldsDisplayUnresolvedTemplate:
    """Verwaister Slug, für den sich (defensiv) KEIN FieldTemplate findet."""

    def test_unresolvable_orphan_stays_not_encrypted_by_default(self, facility, staff_user):
        """Pin ``x_build_orphan_fields_display__mutmut_46``: der
        Nicht-gefunden-Fallback von ``is_encrypted`` wird ``True`` statt
        ``False``. Ohne auffindbares Template gated die Funktion konservativ
        als HIGH (restricted für ``staff_user``); auch der restricted-Zweig
        trägt ``is_encrypted`` unverändert weiter."""
        doc_type = DocumentType.objects.create(facility=facility, name="Profil ohne jedes Template")

        entries = build_orphan_fields_display(doc_type, {"phantom-slug": "irrelevanter-wert"}, staff_user)

        assert len(entries) == 1
        entry = entries[0]
        assert entry["restricted"] is True
        assert entry["is_encrypted"] is False


# ============================================================================
# build_field_template_lookup — Default-ordered + select_related (2 Mutanten)
# ============================================================================


class TestBuildFieldTemplateLookupDefaultOrdering:
    def test_unordered_default_call_follows_sort_order_not_the_block_sequence(self, facility):
        """Pin ``x_build_field_template_lookup__mutmut_1``: der Default von
        ``ordered`` wechselt von ``False`` auf ``True``. Aufrufer ohne
        explizites Kwarg (z. B. ``core/views/cases.py``) erwarten die
        ``DocumentTypeField.sort_order``-Reihenfolge (Modell-``Meta.ordering``),
        NICHT die globale Block-Reihenfolge. Ein freies Feld mit niedrigem
        ``sort_order`` und ein gruppiertes Feld mit hohem inneren
        ``sort_order``, aber niedriger Gruppen-Position, unterscheiden die
        beiden Reihenfolgen deterministisch."""
        doc_type = DocumentType.objects.create(facility=facility, name="Block-vs-Sortierung")
        group = FieldGroup.objects.create(document_type=doc_type, name="Gruppe vorn", sort_order=0)
        ft_grouped = _ft(facility, "Feld in Gruppe")
        ft_free = _ft(facility, "Freies Feld")
        # Block-Position von ft_grouped = Gruppen-sort_order (0) < Block-Position
        # von ft_free (eigener sort_order 1) — Block-Reihenfolge: [grouped, free].
        # DocumentTypeField.Meta.ordering = ["sort_order"] (reiner Feldwert):
        # ft_free (1) < ft_grouped (5) — Default-Reihenfolge: [free, grouped].
        _assign(doc_type, ft_grouped, field_group=group, sort_order=5)
        _assign(doc_type, ft_free, sort_order=1)

        result = build_field_template_lookup(doc_type)

        assert list(result.keys()) == [ft_free.slug, ft_grouped.slug]


class TestBuildFieldTemplateLookupSelectRelated:
    def test_unordered_lookup_query_count_does_not_grow_with_field_count(self, facility):
        """Pin ``x_build_field_template_lookup__mutmut_4``: der
        ``select_related("field_template")``-Join im unsortierten Zweig wird
        zu ``select_related(None)``. Ohne den Join lädt ``dtf.field_template``
        je Zuordnung eine eigene Query nach (N+1) — die Query-Zahl von
        ``build_field_template_lookup(document_type)`` (ohne ``ordered``)
        muss unabhängig von der Feldzahl konstant bleiben."""
        doc_type = DocumentType.objects.create(facility=facility, name="Skalierung")
        _assign(doc_type, _ft(facility, "Feld 1"), sort_order=0)
        # Aufwaermen: erster Aufruf traegt einmalige Nebenkosten (Modell-Cache).
        build_field_template_lookup(doc_type)
        with_one = _count_queries(lambda: build_field_template_lookup(doc_type))

        for index in range(2, 5):
            _assign(doc_type, _ft(facility, f"Feld {index}"), sort_order=index - 1)
        with_four = _count_queries(lambda: build_field_template_lookup(doc_type))

        assert len(build_field_template_lookup(doc_type)) == 4
        assert with_four == with_one


# ============================================================================
# resolve_orphan_field_templates — facility_id-/slug__in-Filter (2 Mutanten)
# ============================================================================


class TestResolveOrphanFieldTemplatesFacilityScope:
    def test_a_foreign_facilitys_field_template_never_resolves_here(self, facility, second_facility):
        """Pin ``x_resolve_orphan_field_templates__mutmut_6``: der
        ``facility_id``-Filter entfällt — ein angefragter Slug löst sich dann
        FACILITY-ÜBERGREIFEND auf. ``second_facility`` trägt ein FieldTemplate
        mit demselben Slug, den ``facility`` NICHT hat; die Auflösung für
        ``facility`` muss leer bleiben."""
        doc_type = DocumentType.objects.create(facility=facility, name="Eigene Einrichtung")
        foreign_template = _ft(second_facility, "Fremdfeld")

        result = resolve_orphan_field_templates(doc_type, [foreign_template.slug])

        assert result == {}


class TestResolveOrphanFieldTemplatesSlugScope:
    def test_only_the_requested_slugs_come_back_not_the_whole_facility(self, facility):
        """Pin ``x_resolve_orphan_field_templates__mutmut_7``: der
        ``slug__in``-Filter entfällt — die Funktion liefert dann JEDES
        FieldTemplate der Einrichtung statt nur der angefragten verwaisten
        Slugs. Ein unbeteiligtes FieldTemplate in derselben Einrichtung darf
        bei einer Anfrage nach einem ANDEREN (nicht existenten) Slug nicht im
        Ergebnis auftauchen."""
        doc_type = DocumentType.objects.create(facility=facility, name="Viele Vorlagen")
        _ft(facility, "Unbeteiligte Vorlage")

        result = resolve_orphan_field_templates(doc_type, ["dieser-slug-existiert-nicht"])

        assert result == {}


# ============================================================================
# clean_dynamic_data — facility_error-Pass-Through (2 Mutanten, ein Pin)
# ============================================================================


class TestCleanDynamicDataFacilityErrorPassThrough:
    def test_custom_facility_error_reaches_the_caller_unchanged(self, facility, foreign_doc_type_client):
        """Pin ``x_clean_dynamic_data__mutmut_6`` UND ``…_11`` (beobachtbar
        identisch — explizites ``facility_error=None`` vs. komplett
        weggelassenes Kwarg, beides fällt auf den Default ``None`` zurück):
        ``clean_dynamic_data`` reicht den aufrufer-eigenen ``facility_error``
        nicht mehr an ``check_document_type`` durch. Mit einem
        DocumentType einer FREMDEN Einrichtung muss die übergebene
        ``ValueError`` unverändert durchschlagen — NICHT die generische
        deutsche ``ValidationError``."""
        with pytest.raises(ValueError) as exc_info:
            clean_dynamic_data(
                facility,
                foreign_doc_type_client,
                {},
                entity_type=DocumentType.EntityType.CLIENT,
                entity_type_error="Der Dokumentationstyp ist nicht für Personen vorgesehen.",
                facility_error=ValueError("DocumentType gehört nicht zur Facility"),
            )

        assert str(exc_info.value) == "DocumentType gehört nicht zur Facility"


# ============================================================================
# is_file_marker — and/or im Stufe-B-Zweig (1 Mutant)
# ============================================================================


class TestIsFileMarkerRequiresTheFlag:
    def test_an_entries_list_alone_is_not_a_file_marker(self):
        """Pin ``x_is_file_marker__mutmut_13``: ``and`` wird zu ``or`` — ein
        Dict mit einer ``entries``-Liste, aber OHNE (oder mit falschem)
        ``__files__``-Flag, gilt dann fälschlich als Datei-Marker."""
        assert is_file_marker({"entries": []}) is False
        assert is_file_marker({"entries": ["irrelevant"]}) is False
        assert is_file_marker({"__files__": False, "entries": []}) is False


# ============================================================================
# remove_restricted_fields — Default-Sensitivity-Fallback (1 Mutant)
# ============================================================================


class TestRemoveRestrictedFieldsUnknownTemplateFallback:
    def test_a_field_without_matching_template_stays_visible_even_for_facility_admin(self, facility, admin_user):
        """Pin ``x_remove_restricted_fields__mutmut_9``: der
        Default-Sensitivity-Fallback für ein Formularfeld OHNE zugeordnetes
        FieldTemplate wechselt von ``""`` (erbt NORMAL, für alle Rollen
        sichtbar) auf den unbekannten Literalwert ``"XXXX"``.
        ``sensitivity_rank()`` ist fail-closed und stuft jeden unbekannten
        Wert OBERHALB jedes Rollen-Maximums ein — das Feld würde dann für
        JEDE Rolle, auch ``facility_admin``, aus dem Formular entfernt."""
        doc_type = DocumentType.objects.create(facility=facility, name="Leerer Typ ohne Felder")

        class _ProbeForm(forms.Form):
            ungebundenes_feld = forms.CharField(required=False)

        data_form = _ProbeForm()

        removed = remove_restricted_fields(admin_user, doc_type, data_form)

        assert removed == []
        assert "ungebundenes_feld" in data_form.fields

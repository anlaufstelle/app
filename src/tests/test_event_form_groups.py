"""Tests für Feldgruppen im Event-Formular (M0/P4, Refs #1056).

Deckt die neue ``DynamicEventDataForm.grouped_fields``-Gruppierung
(Reihenfolge der Gruppen, Ungruppiert-zuletzt, Feld-Reihenfolge innerhalb der
Gruppe, Überspringen leerer bzw. durch ``remove_restricted_fields`` (#774)
leer gewordener Gruppen) sowie das Rendering im Partial/Create-/Edit-Flow ab
(Gruppen-Header, Einklappbarkeit, Include-Regression).
"""

import pytest
from django.urls import reverse
from django.utils import timezone

from core.forms.events import DynamicEventDataForm
from core.models import DocumentType, DocumentTypeField, Event, FieldCondition, FieldGroup, FieldTemplate
from core.services.events.fields import remove_restricted_fields


def _text_field(facility, name):
    return FieldTemplate.objects.create(
        facility=facility,
        name=name,
        field_type=FieldTemplate.FieldType.TEXT,
    )


def _assign(doc_type, field_template, *, group=None, sort_order=0):
    return DocumentTypeField.objects.create(
        document_type=doc_type,
        field_template=field_template,
        field_group=group,
        sort_order=sort_order,
    )


def _group_names(grouped):
    """Namen der Gruppen (None → ``None``) in Rückgabereihenfolge."""
    return [g.name if g is not None else None for g, _ in grouped]


def _field_names_of(grouped, index):
    return [bf.name for bf in grouped[index][1]]


def _condition(doc_type, *, target, source, operator=FieldCondition.Operator.IS_NOT_EMPTY, value=None, group_index=0):
    """Legt eine ``FieldCondition`` per Slug an (Muster test_dynamic_fields_template.py).

    Default-Operator ``is_not_empty`` braucht keinen ``value`` — für die
    ``grouped_blocks()``-Tests unten zählt nur, DASS ein Feld Bedingungs-Ziel
    ist, nicht die konkrete Regel-Semantik (die ist in test_conditions.py /
    den JS-Paritätstests abgedeckt).
    """
    return FieldCondition.objects.create(
        target_field=doc_type.fields.get(field_template__slug=target),
        depends_on_field=doc_type.fields.get(field_template__slug=source),
        operator=operator,
        value=value,
        group_index=group_index,
    )


def _blocks_by_group_name(blocks):
    """``{gruppenname_oder_None: condition_slugs}`` — Kurzform für Assertions."""
    return {b["group"].name if b["group"] is not None else None: b["condition_slugs"] for b in blocks}


# ---------------------------------------------------------------------------
# grouped_fields — Gruppierungslogik (reine Form-Ebene)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGroupedFields:
    def test_no_groups_yields_single_none_section(self, facility):
        """Ohne jegliche Gruppe → genau eine None-Sektion (flach) mit allen Feldern."""
        dt = DocumentType.objects.create(facility=facility, name="Flach")
        _assign(dt, _text_field(facility, "Feld A"), sort_order=0)
        _assign(dt, _text_field(facility, "Feld B"), sort_order=1)

        form = DynamicEventDataForm(document_type=dt, facility=facility)
        grouped = form.grouped_fields()

        assert _group_names(grouped) == [None]
        assert _field_names_of(grouped, 0) == ["feld-a", "feld-b"]

    def test_groups_ordered_by_sort_order_then_name(self, facility):
        """Benannte Gruppen erscheinen in ``sort_order`` (dann Name)."""
        dt = DocumentType.objects.create(facility=facility, name="Mit Gruppen")
        g_late = FieldGroup.objects.create(document_type=dt, name="Zuletzt", sort_order=2)
        g_early = FieldGroup.objects.create(document_type=dt, name="Zuerst", sort_order=0)
        _assign(dt, _text_field(facility, "Spät"), group=g_late, sort_order=0)
        _assign(dt, _text_field(facility, "Früh"), group=g_early, sort_order=1)

        form = DynamicEventDataForm(document_type=dt, facility=facility)
        grouped = form.grouped_fields()

        assert _group_names(grouped) == ["Zuerst", "Zuletzt"]

    def test_same_sort_order_breaks_tie_by_name(self, facility):
        """Bei gleichem ``sort_order`` entscheidet der Name alphabetisch."""
        dt = DocumentType.objects.create(facility=facility, name="Tie")
        g_b = FieldGroup.objects.create(document_type=dt, name="Beta", sort_order=1)
        g_a = FieldGroup.objects.create(document_type=dt, name="Alpha", sort_order=1)
        _assign(dt, _text_field(facility, "F1"), group=g_b, sort_order=0)
        _assign(dt, _text_field(facility, "F2"), group=g_a, sort_order=1)

        form = DynamicEventDataForm(document_type=dt, facility=facility)
        assert _group_names(form.grouped_fields()) == ["Alpha", "Beta"]

    def test_free_field_interleaves_by_block_position(self, facility):
        """#1713: ein freies Feld an Blockpos 0 steht VOR der Gruppe an Blockpos 5.

        Invertiert die frühere „Ohne Gruppe zuletzt"-Fixregel: unter der globalen
        Block-Sequenz bestimmt allein die Blockposition die Reihenfolge.
        """
        dt = DocumentType.objects.create(facility=facility, name="Gemischt")
        g = FieldGroup.objects.create(document_type=dt, name="Gruppe", sort_order=5)
        _assign(dt, _text_field(facility, "Ohne"), group=None, sort_order=0)
        _assign(dt, _text_field(facility, "In Gruppe"), group=g, sort_order=0)

        form = DynamicEventDataForm(document_type=dt, facility=facility)
        grouped = form.grouped_fields()

        # Freies Feld (Blockpos 0) VOR der Gruppe (Blockpos 5).
        assert _group_names(grouped) == [None, "Gruppe"]
        assert _field_names_of(grouped, 0) == ["ohne"]
        assert _field_names_of(grouped, 1) == ["in-gruppe"]

    def test_free_group_free_yields_two_none_runs(self, facility):
        """Freier Block, Gruppe, freier Block → zwei getrennte None-Runs."""
        dt = DocumentType.objects.create(facility=facility, name="Interleaved")
        g = FieldGroup.objects.create(document_type=dt, name="Mitte", sort_order=1)
        _assign(dt, _text_field(facility, "Vorne"), group=None, sort_order=0)
        _assign(dt, _text_field(facility, "In Gruppe"), group=g, sort_order=0)
        _assign(dt, _text_field(facility, "Hinten"), group=None, sort_order=2)

        form = DynamicEventDataForm(document_type=dt, facility=facility)
        grouped = form.grouped_fields()

        assert _group_names(grouped) == [None, "Mitte", None]
        assert _field_names_of(grouped, 0) == ["vorne"]
        assert _field_names_of(grouped, 1) == ["in-gruppe"]
        assert _field_names_of(grouped, 2) == ["hinten"]

    def test_consecutive_free_blocks_merge_into_one_run(self, facility):
        """Zwei aufeinanderfolgende freie Blöcke werden zu einem None-Run gemerged."""
        dt = DocumentType.objects.create(facility=facility, name="Merge")
        g = FieldGroup.objects.create(document_type=dt, name="Kopf", sort_order=0)
        _assign(dt, _text_field(facility, "In Gruppe"), group=g, sort_order=0)
        _assign(dt, _text_field(facility, "Frei A"), group=None, sort_order=1)
        _assign(dt, _text_field(facility, "Frei B"), group=None, sort_order=2)

        form = DynamicEventDataForm(document_type=dt, facility=facility)
        grouped = form.grouped_fields()

        assert _group_names(grouped) == ["Kopf", None]
        assert _field_names_of(grouped, 1) == ["frei-a", "frei-b"]

    def test_field_order_within_group_by_document_type_field_sort_order(self, facility):
        """Feld-Reihenfolge innerhalb einer Gruppe = DocumentTypeField.sort_order."""
        dt = DocumentType.objects.create(facility=facility, name="Reihenfolge")
        g = FieldGroup.objects.create(document_type=dt, name="Gruppe", sort_order=0)
        _assign(dt, _text_field(facility, "Dritte"), group=g, sort_order=30)
        _assign(dt, _text_field(facility, "Erste"), group=g, sort_order=10)
        _assign(dt, _text_field(facility, "Zweite"), group=g, sort_order=20)

        form = DynamicEventDataForm(document_type=dt, facility=facility)
        grouped = form.grouped_fields()

        assert _field_names_of(grouped, 0) == ["erste", "zweite", "dritte"]

    def test_empty_group_is_skipped(self, facility):
        """Eine Gruppe ohne zugeordnete Felder taucht nicht auf."""
        dt = DocumentType.objects.create(facility=facility, name="Leer")
        FieldGroup.objects.create(document_type=dt, name="Verwaist", sort_order=0)
        g_used = FieldGroup.objects.create(document_type=dt, name="Benutzt", sort_order=1)
        _assign(dt, _text_field(facility, "Feld"), group=g_used, sort_order=0)

        form = DynamicEventDataForm(document_type=dt, facility=facility)
        assert _group_names(form.grouped_fields()) == ["Benutzt"]

    def test_group_emptied_by_restriction_is_skipped(self, facility, assistant_user):
        """Wird eine Gruppe durch remove_restricted_fields (#774) leer → übersprungen."""
        dt = DocumentType.objects.create(
            facility=facility,
            name="Sensibel-Gemischt",
            sensitivity=DocumentType.Sensitivity.NORMAL,
        )
        g_secret = FieldGroup.objects.create(document_type=dt, name="Geheim", sort_order=0)
        g_normal = FieldGroup.objects.create(document_type=dt, name="Offen", sort_order=1)
        ft_secret = FieldTemplate.objects.create(
            facility=facility,
            name="Nur Lead",
            field_type=FieldTemplate.FieldType.TEXTAREA,
            is_encrypted=True,
            sensitivity=DocumentType.Sensitivity.HIGH,
        )
        _assign(dt, ft_secret, group=g_secret, sort_order=0)
        _assign(dt, _text_field(facility, "Offen Feld"), group=g_normal, sort_order=1)

        form = DynamicEventDataForm(document_type=dt, facility=facility)
        # Vorher sind beide Gruppen da.
        assert _group_names(form.grouped_fields()) == ["Geheim", "Offen"]

        # Assistant darf das HIGH-Feld nicht sehen → Gruppe "Geheim" wird leer.
        removed = remove_restricted_fields(assistant_user, dt, form)
        assert "nur-lead" in removed
        assert _group_names(form.grouped_fields()) == ["Offen"]

    def test_empty_form_yields_empty_grouping(self, facility):
        """Form ohne document_type → keine Felder, leere Gruppierung."""
        form = DynamicEventDataForm()
        assert form.grouped_fields() == []


# ---------------------------------------------------------------------------
# grouped_blocks — condition_slugs je Block (#1867)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGroupedBlocks:
    """``condition_slugs`` ist NUR gefüllt, wenn ALLE Felder eines Blocks
    Bedingungs-Ziele sind — die Voraussetzung dafür, dass das Template die
    Karte per ``conditionalGroup``-Wrapper komplett ausblenden darf.
    """

    def test_condition_slugs_filled_when_all_fields_are_targets(self, facility):
        """Gruppe mit zwei Zielfeldern (keines davon die freie Quelle) → beide Slugs, komma-getrennt."""
        dt = DocumentType.objects.create(facility=facility, name="Bedingt-Alle-Ziele")
        g = FieldGroup.objects.create(document_type=dt, name="Gruppe", sort_order=1)
        _assign(dt, _text_field(facility, "Quelle"), group=None, sort_order=0)
        _assign(dt, _text_field(facility, "Ziel A"), group=g, sort_order=0)
        _assign(dt, _text_field(facility, "Ziel B"), group=g, sort_order=1)
        _condition(dt, target="ziel-a", source="quelle")
        _condition(dt, target="ziel-b", source="quelle")

        form = DynamicEventDataForm(document_type=dt, facility=facility)
        by_group = _blocks_by_group_name(form.grouped_blocks())

        assert by_group["Gruppe"] == "ziel-a,ziel-b"
        # Die freie Quelle ist nie Bedingungs-Ziel → ihr eigener Block bleibt leer.
        assert by_group[None] == ""

    def test_condition_slugs_empty_for_mixed_block(self, facility):
        """Gruppe mit Ziel- UND freiem Feld → condition_slugs leer (Karte bleibt immer sichtbar)."""
        dt = DocumentType.objects.create(facility=facility, name="Bedingt-Gemischt")
        g = FieldGroup.objects.create(document_type=dt, name="Gemischt", sort_order=0)
        _assign(dt, _text_field(facility, "Quelle"), group=None, sort_order=0)
        _assign(dt, _text_field(facility, "Ziel"), group=g, sort_order=0)
        _assign(dt, _text_field(facility, "Frei In Gruppe"), group=g, sort_order=1)
        _condition(dt, target="ziel", source="quelle")

        form = DynamicEventDataForm(document_type=dt, facility=facility)
        by_group = _blocks_by_group_name(form.grouped_blocks())

        assert by_group["Gemischt"] == ""

    def test_condition_slugs_empty_without_any_conditions(self, facility):
        """DocType ganz ohne Bedingungen → jeder Block hat condition_slugs == ''."""
        dt = DocumentType.objects.create(facility=facility, name="Ohne-Bedingung")
        g = FieldGroup.objects.create(document_type=dt, name="Gruppe", sort_order=0)
        _assign(dt, _text_field(facility, "Feld"), group=g, sort_order=0)
        _assign(dt, _text_field(facility, "Frei"), group=None, sort_order=1)

        form = DynamicEventDataForm(document_type=dt, facility=facility)
        blocks = form.grouped_blocks()

        assert blocks  # Gegenprobe: es gibt überhaupt Blöcke.
        assert all(b["condition_slugs"] == "" for b in blocks)

    def test_condition_slugs_empty_after_target_dropped_by_sensitivity(self, facility, assistant_user):
        """Fällt das Ziel per ``remove_restricted_fields`` (#774) aus dem Feldset,
        verschwindet seine Gruppe komplett (wie ``grouped_fields``) und KEINE
        verbleibende Gruppe wird fälschlich als all-konditional markiert.

        ``conditions_payload()`` ist lazy-gecacht und darf laut Vertrag
        (dynamic.py) erst NACH dem Restrict-Schritt zum ersten Mal aufgerufen
        werden — deshalb zwei getrennte Form-Instanzen statt derselben Form
        vor/nach ``remove_restricted_fields``.
        """
        dt = DocumentType.objects.create(
            facility=facility, name="Bedingt-Sensibel", sensitivity=DocumentType.Sensitivity.NORMAL
        )
        g_target = FieldGroup.objects.create(document_type=dt, name="Ziel-Gruppe", sort_order=0)
        g_source = FieldGroup.objects.create(document_type=dt, name="Quell-Gruppe", sort_order=1)
        _assign(dt, _text_field(facility, "Quelle"), group=g_source, sort_order=0)
        ft_secret = FieldTemplate.objects.create(
            facility=facility,
            name="Geheimes Ziel",
            field_type=FieldTemplate.FieldType.TEXTAREA,
            is_encrypted=True,
            sensitivity=DocumentType.Sensitivity.HIGH,
        )
        _assign(dt, ft_secret, group=g_target, sort_order=0)
        _condition(dt, target="geheimes-ziel", source="quelle")

        # Volle Rechte (facility_admin-artiger Zugriff, keine Restriktion):
        # Ziel-Gruppe ist all-konditional.
        form_full = DynamicEventDataForm(document_type=dt, facility=facility)
        full = _blocks_by_group_name(form_full.grouped_blocks())
        assert full["Ziel-Gruppe"] == "geheimes-ziel"
        assert full["Quell-Gruppe"] == ""

        # Assistant: HIGH-Feld fällt VOR dem ersten Payload-Zugriff weg.
        form_restricted = DynamicEventDataForm(document_type=dt, facility=facility)
        removed = remove_restricted_fields(assistant_user, dt, form_restricted)
        assert "geheimes-ziel" in removed
        restricted = _blocks_by_group_name(form_restricted.grouped_blocks())

        assert "Ziel-Gruppe" not in restricted
        assert restricted["Quell-Gruppe"] == ""


# ---------------------------------------------------------------------------
# Rendering — Partial-/Create-/Edit-Flow (View-Smoke)
# ---------------------------------------------------------------------------


def _doc_type_with_named_group(facility, *, collapsible=False, group_name="Stammdaten"):
    dt = DocumentType.objects.create(facility=facility, name="Kontakt-Gruppiert")
    group = FieldGroup.objects.create(
        document_type=dt,
        name=group_name,
        sort_order=0,
        is_collapsible=collapsible,
    )
    ft = FieldTemplate.objects.create(
        facility=facility,
        name="Gruppenfeld-XYZ",
        field_type=FieldTemplate.FieldType.TEXT,
    )
    _assign(dt, ft, group=group, sort_order=0)
    return dt, group, ft


@pytest.mark.django_db
class TestPartialViewRendersGroups:
    def test_partial_renders_group_header(self, client, staff_user, facility):
        dt, group, ft = _doc_type_with_named_group(facility, group_name="Sichtbare-Gruppe")
        client.force_login(staff_user)
        resp = client.get(reverse("core:event_fields_partial") + f"?document_type={dt.pk}")
        assert resp.status_code == 200
        body = resp.content.decode()
        assert "Sichtbare-Gruppe" in body
        assert ft.name in body

    def test_collapsible_group_renders_alpine_component(self, client, staff_user, facility):
        dt, group, ft = _doc_type_with_named_group(facility, collapsible=True)
        client.force_login(staff_user)
        resp = client.get(reverse("core:event_fields_partial") + f"?document_type={dt.pk}")
        body = resp.content.decode()
        assert 'x-data="collapsibleGroup"' in body
        assert "data-start-open" in body

    def test_non_collapsible_group_has_no_toggle_component(self, client, staff_user, facility):
        dt, group, ft = _doc_type_with_named_group(facility, collapsible=False)
        client.force_login(staff_user)
        resp = client.get(reverse("core:event_fields_partial") + f"?document_type={dt.pk}")
        body = resp.content.decode()
        assert 'x-data="collapsibleGroup"' not in body
        # Header trotzdem vorhanden.
        assert group.name in body

    def test_named_group_plus_ungrouped_renders_ungrouped_header(self, client, staff_user, facility):
        """Benannte Gruppe + ungruppiertes Feld → Ungruppiert-Sektion trägt „Weitere Angaben".

        Refs #1805: der Header hiess „Ohne Gruppe" und beschrieb damit
        einen Konfigurationszustand statt des Inhalts.
        """
        dt, group, ft = _doc_type_with_named_group(facility, group_name="Benannt")
        loose = _text_field(facility, "Loses Feld")
        _assign(dt, loose, group=None, sort_order=1)
        client.force_login(staff_user)
        resp = client.get(reverse("core:event_fields_partial") + f"?document_type={dt.pk}")
        body = resp.content.decode()
        assert "Benannt" in body
        assert "Weitere Angaben" in body
        assert loose.name in body

    def test_flat_doc_type_has_no_ungrouped_header(self, client, staff_user, doc_type_contact):
        """Ohne benannte Gruppen erscheint NIE ein „Weitere Angaben"-Header (nur „Angaben")."""
        client.force_login(staff_user)
        resp = client.get(reverse("core:event_fields_partial") + f"?document_type={doc_type_contact.pk}")
        body = resp.content.decode()
        assert "Weitere Angaben" not in body
        assert "Angaben" in body

    def test_flat_doc_type_has_no_group_component(self, client, staff_user, doc_type_contact):
        """Ohne benannte Gruppen: flache Darstellung, keine Collapsible-Komponente."""
        client.force_login(staff_user)
        resp = client.get(reverse("core:event_fields_partial") + f"?document_type={doc_type_contact.pk}")
        body = resp.content.decode()
        assert 'x-data="collapsibleGroup"' not in body
        # Bestehende Felder werden weiterhin gerendert.
        assert "Dauer" in body
        assert "Notiz" in body


@pytest.mark.django_db
class TestCreateAndEditRenderGroups:
    def test_create_prefill_renders_group_header(self, client, staff_user, facility):
        dt, group, ft = _doc_type_with_named_group(facility, group_name="Create-Gruppe")
        client.force_login(staff_user)
        resp = client.get(reverse("core:event_create") + f"?document_type={dt.pk}")
        assert resp.status_code == 200
        body = resp.content.decode()
        assert "Create-Gruppe" in body
        assert ft.name in body

    def test_edit_view_renders_grouped_fields(self, client, staff_user, facility):
        dt, group, ft = _doc_type_with_named_group(facility, group_name="Edit-Gruppe")
        event = Event.objects.create(
            facility=facility,
            document_type=dt,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        client.force_login(staff_user)
        resp = client.get(reverse("core:event_update", kwargs={"pk": event.pk}))
        assert resp.status_code == 200
        body = resp.content.decode()
        assert "Edit-Gruppe" in body
        assert ft.name in body

    def test_edit_view_flat_doc_type_still_renders_fields(self, client, staff_user, facility, doc_type_contact):
        """Include-Regression: ein Typ ohne Gruppen rendert die Felder weiterhin flach."""
        event = Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        client.force_login(staff_user)
        resp = client.get(reverse("core:event_update", kwargs={"pk": event.pk}))
        assert resp.status_code == 200
        body = resp.content.decode()
        assert "Dauer" in body
        assert "Notiz" in body
        # Speichern-Button und Hidden-Field bleiben erhalten (Form-Struktur intakt).
        assert 'name="expected_updated_at"' in body


# ---------------------------------------------------------------------------
# Einfüge-Reihenfolge von self.fields == Anzeige-Reihenfolge (Refs #1719-Fund)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFieldInsertionOrderIsBlockOrder:
    """``self.fields`` folgt der globalen Block-Reihenfolge (#1713).

    Regression zum Serienerfassungs-Autofocus: Konsumenten wie
    ``next(iter(form.fields.values()))`` verlassen sich darauf, dass das ERSTE
    Formularfeld auch das erste ANGEZEIGTE Feld ist. Vor dem Fix ergab das
    flache ``order_by("sort_order")`` bei Innen-Index-Ties (dauer=0 in Gruppe A,
    leistungen=0 in Gruppe B) eine DB-abhängige Reihenfolge.
    """

    def _tie_doctype(self, facility):
        dt = DocumentType.objects.create(facility=facility, name="Tie-Regression")
        g1 = FieldGroup.objects.create(document_type=dt, name="Basis", sort_order=0)
        g2 = FieldGroup.objects.create(document_type=dt, name="Details", sort_order=1)
        # Innen-Indizes kollidieren bewusst über Gruppen hinweg (beide 0/1) und
        # mit der Globalposition des freien Felds (2).
        _assign(dt, _text_field(facility, "B Eins"), group=g1, sort_order=0)
        _assign(dt, _text_field(facility, "B Zwei"), group=g1, sort_order=1)
        _assign(dt, _text_field(facility, "D Eins"), group=g2, sort_order=0)
        _assign(dt, _text_field(facility, "D Zwei"), group=g2, sort_order=1)
        _assign(dt, _text_field(facility, "Frei"), sort_order=2)
        return dt

    def test_fields_iteration_matches_grouped_fields_flattened(self, facility):
        dt = self._tie_doctype(facility)
        form = DynamicEventDataForm(document_type=dt, facility=facility)

        flattened = [bf.name for _group, bounds in form.grouped_fields() for bf in bounds]
        assert list(form.fields) == flattened
        assert flattened == ["b-eins", "b-zwei", "d-eins", "d-zwei", "frei"]

    def test_first_field_is_first_displayed_field(self, facility):
        """Der Autofocus-Konsument (Serienerfassung, views/events.py) sieht das erste sichtbare Feld."""
        dt = self._tie_doctype(facility)
        form = DynamicEventDataForm(document_type=dt, facility=facility)

        assert next(iter(form.fields)) == "b-eins"

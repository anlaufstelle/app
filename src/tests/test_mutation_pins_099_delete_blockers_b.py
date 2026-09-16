"""Pin-Tests der Welle L (0.99, Refs #2174/#2193) — Vollmutation-Survivor-
Triage in ``core.services.config.delete_blockers``, Batch ``delete_blockers-b``
(zweite Haelfte der Datei; ``delete_blockers-a`` deckt die restlichen
Survivor derselben Datei in einer eigenen Pin-Datei ab).

Quelle: ``welle-l-0.99/reports/m-scratch/batch-delete_blockers-b.json``
(Triage ``triage-099-delete_blockers-b.json``).

Konventionen wie in den uebrigen ``test_mutation_pins_099_*``-Dateien:
Zusicherungen vergleichen EXAKT (``==``), nie per Teilstring (mutmut erzeugt
``"XX<text>XX"``-Varianten, die ein Substring-Match uebersehen wuerde). Die
beiden ``order_by``-Tiebreak-Mutanten werden **plan-unabhaengig ueber die
Sortierkette der internen Query** gepinnt (Spy auf ``QuerySet.order_by``,
Muster ``test_mutation_pins_099_facility_export_a.py`` /
``test_mutation_pins_098_client_export.py``), nicht ueber die tatsaechliche
Zeilenreihenfolge bei einem Sort-Order-Gleichstand — die haelt ohne
entscheidendes ORDER-BY-Kriterium nicht zuverlaessig.

``_encryption_key`` liegt modulweit an (Lehre 112): der Mutantenbaum hat
keine ``.env`` und damit keinen ``ENCRYPTION_KEY``.

Gepinnte Mutanten dieser Datei (Details je Test in dessen Docstring):

* ``x__append_trash_share__mutmut_1``, ``_2``, ``_23``, ``_28``, ``_31``,
  ``_32``, ``_33``, ``_39``, ``_44``, ``_47``, ``_48``, ``_49``
* ``x__trashed_client_ids_with_slug__mutmut_4``, ``_5``, ``_6``, ``_10``,
  ``_11``, ``_16``
* ``x__trashed_case_ids_with_slug__mutmut_4``, ``_5``, ``_6``, ``_10``,
  ``_16``
* ``x__trashed_case_ids_with_attachment__mutmut_3``, ``_4``, ``_5``, ``_8``,
  ``_9``, ``_13``
* ``x_delete_field_group__mutmut_6``, ``_7``
"""

from __future__ import annotations

import pytest
from django.db.models.query import QuerySet
from django.test import RequestFactory
from django.utils import translation

from core.models import (
    Case,
    Client,
    DocumentType,
    DocumentTypeField,
    FieldGroup,
    FieldTemplate,
)
from core.services.config import (
    _append_trash_share,
    _trashed_case_ids_with_attachment,
    _trashed_case_ids_with_slug,
    _trashed_client_ids_with_slug,
    delete_field_group,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


@pytest.fixture
def req(staff_user, facility):
    """Request mit authentifiziertem User + Facility-Scope (wie FacilityScopeMiddleware)."""
    request = RequestFactory().post("/")
    request.user = staff_user
    request.current_facility = facility
    return request


def _attach_file(facility, owner, field_template, user):
    """Legt einen minimalen verschluesselten Anhang an (Muster
    ``test_config_delete_blockers_trash.py::_attach_to``)."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    from core.services.file_vault import store_encrypted_file

    pdf = (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
        b"xref\n0 3\n0000000000 65535 f\n"
        b"trailer<</Size 3/Root 1 0 R>>\n"
        b"startxref\n9\n%%EOF\n"
    )
    upload = SimpleUploadedFile("blocker.pdf", pdf, content_type="application/pdf")
    return store_encrypted_file(facility, upload, field_template, owner, user)


class TestAppendTrashSharePins:
    """``_append_trash_share`` ergaenzt eine Blocker-Begruendung um den
    Papierkorb-Anteil; zwei Aufrufstellen (``delete_document_type`` und
    ``field_template_delete_blockers``, jeweils Personen-Zweig) rufen OHNE
    ``of_attachments``/``of_cases``-Keyword auf und haengen damit an den
    Defaultwerten (beide ``False``)."""

    def test_default_flags_report_person_singular(self):
        """Pins ``x__append_trash_share__mutmut_1`` (Default von
        ``of_attachments`` faellt von ``False`` auf ``True``) und
        ``x__append_trash_share__mutmut_2`` (Default von ``of_cases`` faellt
        von ``False`` auf ``True``): ohne explizite Flags (wie am
        Aufrufort ueblich) MUSS der Satz die Personen-Formulierung tragen,
        nicht „Fall“/„Anhang“. Das exakte Gleichheitszeichen deckt zugleich
        ``mutmut_44`` (Singular-Text mit mutmut-``XX``-Markierung) ab, die
        ein reiner Teilstring-Check auf „Person“ uebersaehe."""
        result = _append_trash_share("Grund.", 1)
        assert result == "Grund. Davon liegt 1 Person im Papierkorb."

    def test_person_plural_uses_the_passed_plural_argument_without_catalog(self):
        """Pins ``x__append_trash_share__mutmut_39`` (Plural-msgid des
        Personen-Zweigs faellt auf ``None``) sowie die reinen Text-Varianten
        ``_47`` (mutmut-``XX``-Markierung), ``_48``
        (Klein-/``_49`` Grossschreibung) desselben Plural-msgid.

        Mit aktivem ``de``-Katalog schlaegt ``ngettext`` ueber das
        Singular-msgid nach und liefert dessen katalogisierte Pluralform —
        ein kaputtes/veraendertes Plural-Argument im Code bliebe unsichtbar
        (Muster ``test_config_delete_blockers_trash.py::
        test_plural_fallback_carries_the_passed_msgid``). Erst ohne aktiven
        Katalog (``translation.override(None)``) traegt das uebergebene
        Plural-msgid den Satz allein — ``None`` hiesse dort ein
        ``TypeError`` bei der ``%``-Formatierung statt der Blocker-Meldung,
        die Text-Varianten liefern dann ihren veraenderten Wortlaut
        unmaskiert zurueck.
        """
        with translation.override(None):
            result = _append_trash_share("Grund.", 2)
        assert result == "Grund. Davon liegen 2 Personen im Papierkorb."

    def test_attachment_flag_singular_wording(self):
        """Pins ``x__append_trash_share__mutmut_28`` (Singular-Text des
        Anhang-Zweigs mit mutmut-``XX``-Markierung): das mutierte msgid
        findet im Katalog keinen Treffer mehr und faellt auf den mutierten
        Literal-Text zurueck — beobachtbar schon ohne Katalog-Override, weil
        der Katalog-Schluessel selbst (das Singular-msgid) veraendert ist."""
        result = _append_trash_share("Grund.", 1, of_attachments=True)
        assert result == "Grund. Davon gehört 1 Anhang zu einer Person im Papierkorb."

    def test_attachment_plural_uses_the_passed_plural_argument_without_catalog(self):
        """Pins ``x__append_trash_share__mutmut_23`` (Plural-msgid des
        Anhang-Zweigs faellt auf ``None``) sowie die reinen Text-Varianten
        ``_31`` (mutmut-``XX``-Markierung), ``_32`` (Klein-)/``_33``
        (Grossschreibung) desselben Plural-msgid — gleiches Muster wie
        ``test_person_plural_uses_the_passed_plural_argument_without_catalog``,
        hier auf den ``of_attachments``-Zweig angewandt."""
        with translation.override(None):
            result = _append_trash_share("Grund.", 2, of_attachments=True)
        assert result == "Grund. Davon gehören 2 Anhänge zu Personen im Papierkorb."


class TestTrashedClientIdsWithSlugPins:
    """``_trashed_client_ids_with_slug`` liefert die PKs soft-geloeschter
    Personen mit einem Wert unter dem Vorlagen-Slug; speist sowohl den
    Papierkorb-Anteilssatz als auch den Ausweg-Hinweis-Zaehler der
    Lösch-Dialoge."""

    def test_returns_a_flat_pk_set_not_tuples_or_a_crash(self, facility):
        """Pins ``x__trashed_client_ids_with_slug__mutmut_4`` (``flat=None``),
        ``_6`` (``flat``-Keyword faellt komplett weg, Django-Default
        ``False``) und ``_16`` (``flat=False``): alle drei liefern ein
        Set aus 1-Tupeln (``{(pk,)}``) statt eines Sets aus rohen PKs
        (``{pk}``) — beim Vereinigen mit
        ``_trashed_client_ids_with_attachment`` (int-PKs) in
        ``field_template_trashed_client_count`` dedupliziert das nicht mehr
        gegen dieselbe Person, sie zaehlte doppelt.

        Pins zugleich ``x__trashed_client_ids_with_slug__mutmut_5``
        (``.values_list(flat=True)`` OHNE Feldnamen): ``Client`` hat weit
        mehr als eine Spalte, ``flat=True`` bei mehreren Feldern wirft einen
        ``TypeError`` — die Funktion bricht komplett ab, statt die
        Papierkorb-PKs zu liefern (bereits ein einfacher Vergleich auf den
        Rueckgabewert wird durch die nicht abgefangene Exception rot)."""
        template = FieldTemplate.objects.create(
            facility=facility, name="Slug-Feld", slug="slug-feld", field_type=FieldTemplate.FieldType.TEXT
        )
        trashed = Client.objects.create(facility=facility, pseudonym="P-Korb", data_json={"slug-feld": "Wert"})
        trashed.soft_delete()

        assert _trashed_client_ids_with_slug(template) == {trashed.pk}

    def test_is_scoped_to_the_field_templates_facility(self, facility, other_facility):
        """Pins ``x__trashed_client_ids_with_slug__mutmut_10``: der
        ``facility_id``-Filter faellt komplett weg. Eine soft-geloeschte
        Person einer FREMDEN Einrichtung mit demselben Slug-Wert darf beim
        Loeschen dieser (anderen) Feldvorlage weder mitgezaehlt werden noch
        die PK-Menge veraendern — sonst haengt der Papierkorb-Hinweis von
        Daten ab, die diese Feldvorlage gar nicht betreffen."""
        template = FieldTemplate.objects.create(
            facility=facility, name="Slug-Feld", slug="slug-feld", field_type=FieldTemplate.FieldType.TEXT
        )
        eigene = Client.objects.create(facility=facility, pseudonym="P-Eigen", data_json={"slug-feld": "Wert"})
        eigene.soft_delete()
        fremde = Client.objects.create(facility=other_facility, pseudonym="P-Fremd", data_json={"slug-feld": "Wert"})
        fremde.soft_delete()

        assert _trashed_client_ids_with_slug(template) == {eigene.pk}

    def test_only_counts_clients_with_a_value_under_this_slug(self, facility):
        """Pins ``x__trashed_client_ids_with_slug__mutmut_11``: der
        ``data_json__has_key``-Filter faellt komplett weg. Eine
        soft-geloeschte Person OHNE Wert unter diesem Slug (aber mit
        anderen Papierkorb-Daten) darf nicht mitgezaehlt werden — sonst
        wird jede Person im Papierkorb der Einrichtung faelschlich dieser
        Feldvorlage als Blocker zugerechnet."""
        template = FieldTemplate.objects.create(
            facility=facility, name="Slug-Feld", slug="slug-feld", field_type=FieldTemplate.FieldType.TEXT
        )
        mit_slug = Client.objects.create(facility=facility, pseudonym="P-Mit-Slug", data_json={"slug-feld": "Wert"})
        mit_slug.soft_delete()
        ohne_slug = Client.objects.create(facility=facility, pseudonym="P-Ohne-Slug", data_json={})
        ohne_slug.soft_delete()

        assert _trashed_client_ids_with_slug(template) == {mit_slug.pk}


class TestTrashedCaseIdsWithSlugPins:
    """``_trashed_case_ids_with_slug`` ist das Fall-Gegenstueck zu
    ``_trashed_client_ids_with_slug`` (Refs #1818)."""

    def _fall(self, facility, *, slug_value=None, deleted=False, titel="Fall"):
        case = Case.objects.create(
            facility=facility,
            client=Client.objects.create(facility=facility, pseudonym=f"P-{titel}"),
            title=titel,
            data_json={"slug-feld": slug_value} if slug_value else {},
        )
        if deleted:
            case.soft_delete()
        return case

    def test_returns_a_flat_pk_set_not_tuples_or_a_crash(self, facility):
        """Pins ``x__trashed_case_ids_with_slug__mutmut_4`` (``flat=None``),
        ``_6`` (``flat``-Keyword faellt weg) und ``_16`` (``flat=False``) —
        Set aus 1-Tupeln statt roher PKs, gleiches Deduplizierungs-Risiko
        wie bei der Personen-Variante (``field_template_trashed_case_count``
        vereinigt mit ``_trashed_case_ids_with_attachment``).

        Pins zugleich ``x__trashed_case_ids_with_slug__mutmut_5``
        (``.values_list(flat=True)`` OHNE Feldnamen): ``Case`` hat mehrere
        Spalten, ``flat=True`` ohne Feldname wirft einen ``TypeError``."""
        template = FieldTemplate.objects.create(
            facility=facility, name="Slug-Feld", slug="slug-feld", field_type=FieldTemplate.FieldType.TEXT
        )
        trashed = self._fall(facility, slug_value="Wert", deleted=True, titel="Korb")

        assert _trashed_case_ids_with_slug(template) == {trashed.pk}

    def test_is_scoped_to_the_field_templates_facility(self, facility, other_facility):
        """Pins ``x__trashed_case_ids_with_slug__mutmut_10``: der
        ``facility_id``-Filter faellt komplett weg — ein Papierkorb-Fall
        einer fremden Einrichtung mit demselben Slug-Wert darf nicht
        mitgezaehlt werden."""
        template = FieldTemplate.objects.create(
            facility=facility, name="Slug-Feld", slug="slug-feld", field_type=FieldTemplate.FieldType.TEXT
        )
        eigener = self._fall(facility, slug_value="Wert", deleted=True, titel="Eigen")
        self._fall(other_facility, slug_value="Wert", deleted=True, titel="Fremd")

        assert _trashed_case_ids_with_slug(template) == {eigener.pk}


class TestTrashedCaseIdsWithAttachmentPins:
    """``_trashed_case_ids_with_attachment`` ist das Fall-Gegenstueck zu
    ``_trashed_client_ids_with_attachment`` (Refs #1818, ADR-039)."""

    def test_returns_a_flat_pk_set_not_tuples_or_a_crash(self, facility, staff_user):
        """Pins ``x__trashed_case_ids_with_attachment__mutmut_3``
        (``flat=None``), ``_5`` (``flat``-Keyword faellt weg) und ``_13``
        (``flat=False``) — Set aus 1-Tupeln statt roher PKs.

        Pins zugleich ``x__trashed_case_ids_with_attachment__mutmut_4``
        (``.values_list(flat=True)`` OHNE Feldnamen): ``Attachment`` hat
        mehrere Spalten, ``flat=True`` ohne Feldname wirft einen
        ``TypeError``."""
        file_template = FieldTemplate.objects.create(
            facility=facility, name="Datei", slug="datei", field_type=FieldTemplate.FieldType.FILE
        )
        case = Case.objects.create(
            facility=facility,
            client=Client.objects.create(facility=facility, pseudonym="P-Anhang"),
            title="Blocker-Fall",
            created_by=staff_user,
        )
        _attach_file(facility, case, file_template, staff_user)
        case.soft_delete()

        assert _trashed_case_ids_with_attachment(file_template) == {case.pk}

    def test_is_scoped_to_this_field_template(self, facility, staff_user):
        """Pins ``x__trashed_case_ids_with_attachment__mutmut_8``: der
        ``field_template``-Filter faellt komplett weg. Ein Papierkorb-Fall,
        dessen Anhang eine ANDERE Feldvorlage referenziert, darf beim
        Loeschen dieser Feldvorlage nicht als Blocker gezaehlt werden."""
        file_template_a = FieldTemplate.objects.create(
            facility=facility, name="Datei A", slug="datei-a", field_type=FieldTemplate.FieldType.FILE
        )
        file_template_b = FieldTemplate.objects.create(
            facility=facility, name="Datei B", slug="datei-b", field_type=FieldTemplate.FieldType.FILE
        )
        case_a = Case.objects.create(
            facility=facility,
            client=Client.objects.create(facility=facility, pseudonym="P-A"),
            title="Fall A",
            created_by=staff_user,
        )
        _attach_file(facility, case_a, file_template_a, staff_user)
        case_a.soft_delete()
        case_b = Case.objects.create(
            facility=facility,
            client=Client.objects.create(facility=facility, pseudonym="P-B"),
            title="Fall B",
            created_by=staff_user,
        )
        _attach_file(facility, case_b, file_template_b, staff_user)
        case_b.soft_delete()

        assert _trashed_case_ids_with_attachment(file_template_a) == {case_a.pk}

    def test_only_counts_cases_that_are_actually_trashed(self, facility, staff_user):
        """Pins ``x__trashed_case_ids_with_attachment__mutmut_9``: der
        ``case__is_deleted``-Filter faellt komplett weg. Ein LEBENDER Fall
        mit einem Anhang dieser Feldvorlage darf nicht als „im Papierkorb“
        gezaehlt werden — er ist ueber die Fallliste erreichbar, der
        Ausweg-Hinweis fuehrte sonst ins Leere."""
        file_template = FieldTemplate.objects.create(
            facility=facility, name="Datei", slug="datei", field_type=FieldTemplate.FieldType.FILE
        )
        lebender_fall = Case.objects.create(
            facility=facility,
            client=Client.objects.create(facility=facility, pseudonym="P-Lebend"),
            title="Lebender Fall",
            created_by=staff_user,
        )
        _attach_file(facility, lebender_fall, file_template, staff_user)

        assert _trashed_case_ids_with_attachment(file_template) == set()


class TestDeleteFieldGroupOrderByPins:
    """``delete_field_group`` liest die Innen-Felder der Gruppe VOR dem
    Delete in Block-Reihenfolge, um sie an der Gruppenposition als freie
    Bloecke wiedereinzusetzen (#1713)."""

    def test_inner_fields_query_orders_by_sort_order_then_pk_tiebreak(self, req, facility, monkeypatch):
        """Pins ``x_delete_field_group__mutmut_6``
        (``.order_by("sort_order", "pk")`` -> ``.order_by("pk")``, das
        fachliche Sortierkriterium faellt komplett weg — die Innen-Felder
        stuenden nach PK-/Erstellungsreihenfolge statt nach ihrer
        tatsaechlichen Innen-Position) und ``x_delete_field_group__mutmut_7``
        (-> ``.order_by("sort_order", )``, der ``pk``-Tiebreak faellt weg —
        bei gleichem ``sort_order`` waere die Reihenfolge undefiniert).

        Plan-unabhaengiger Nachweis direkt auf der Sortierkette der
        internen ``DocumentTypeField``-Query (Muster
        ``test_mutation_pins_099_facility_export_a.py``): ``QuerySet.order_by``
        wird abgehorcht, waehrend ``delete_field_group`` laeuft (das
        umschliesst auch den ``iter_blocks_in_order``-Aufruf mit einer
        eigenen, laengeren Sortierkette — deshalb Mitgliedschaftspruefung,
        nicht Gleichheit der gesamten Aufrufliste). Original: genau EIN
        Aufruf mit ``("sort_order", "pk")``. Mutant _6: nur ``("pk",)``.
        Mutant _7: nur ``("sort_order",)``.
        """
        doc_type = DocumentType.objects.create(facility=facility, name="Ordnungstest")
        grp = FieldGroup.objects.create(document_type=doc_type, name="G", sort_order=0)
        ft = FieldTemplate.objects.create(facility=facility, name="Feld", field_type=FieldTemplate.FieldType.TEXT)
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft, field_group=grp, sort_order=0)

        captured: list[tuple] = []
        original_order_by = QuerySet.order_by

        def _spy(self, *args, **kwargs):
            captured.append(args)
            return original_order_by(self, *args, **kwargs)

        monkeypatch.setattr(QuerySet, "order_by", _spy)

        delete_field_group(req, grp)

        assert ("sort_order", "pk") in captured

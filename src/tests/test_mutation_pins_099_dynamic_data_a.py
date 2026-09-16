"""Pin-Tests der Welle L (0.99) — Vollmutation #2174, Batch ``dynamic_data-a``.

Quelle: Füllauftrag https://github.com/anlaufstelle/app/issues/2174
(Refs auch https://github.com/anlaufstelle/app/issues/2193), Batch
``dynamic_data-a`` aus ``reports/m-scratch/batch-dynamic_data-a.json`` — 70
Survivor-Mutanten in ``core.services.dynamic_data`` (Teil a der beiden
Batches a/b des Moduls): ``_display_entry`` (20),
``decrypt_dynamic_text_data`` (11), ``_file_display_entry`` (11),
``validate_data_json`` (8), ``normalize_file_marker`` (6),
``build_dynamic_data_context`` (6), ``build_dynamic_fields_display`` (5),
``count_filled_dynamic_values`` (3).

Konventionen wie ``test_mutation_pins_098_storage.py``: eine Testklasse je
Funktion, echte Model-Objekte (kein Mock der Fachlogik — nur Spy-Patches auf
beobachtete Aufrufparameter), Zusicherungen EXAKT (volle Dict-/Mengen-
Gleichheit bzw. exakter Textvergleich statt Teilstring-Suche — ``in`` bzw.
``pytest.raises(match=...)`` würden die mutmut-XX-Marker-Varianten
übersehen).
"""

from __future__ import annotations

import logging
from unittest import mock

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import translation

import core.services.dynamic_data as dynamic_data_module
from core.models import DocumentType, DocumentTypeField, FieldTemplate
from core.services.dynamic_data import (
    _display_entry,
    _file_display_entry,
    build_dynamic_data_context,
    build_dynamic_fields_display,
    count_filled_dynamic_values,
    decrypt_dynamic_text_data,
    normalize_file_marker,
    owner_attachment_index,
    validate_data_json,
)
from core.services.file_vault import store_encrypted_file

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]

#: Minimales, gueltiges PDF — die Pipeline prueft Magic-Bytes gegen den
#: deklarierten ``content_type``.
PDF_HEADER = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
    b"xref\n0 3\n0000000000 65535 f\n"
    b"trailer<</Size 3/Root 1 0 R>>\n"
    b"startxref\n9\n%%EOF\n"
)


def _upload(name="beleg.pdf"):
    return SimpleUploadedFile(name, PDF_HEADER + name.encode(), content_type="application/pdf")


def _make_document_type(facility, *, sensitivity=DocumentType.Sensitivity.NORMAL):
    return DocumentType.objects.create(
        facility=facility,
        name="Pin099-Typ",
        entity_type=DocumentType.EntityType.CLIENT,
        sensitivity=sensitivity,
    )


def _attach_field(document_type, field_template, *, sort_order=0):
    return DocumentTypeField.objects.create(
        document_type=document_type, field_template=field_template, sort_order=sort_order
    )


class TestCountFilledDynamicValuesLoopContinuesPastSkippedEntries:
    """``count_filled_dynamic_values`` MUSS nach einem uebersprungenen (leeren)
    Wert mit dem Rest von ``data_json`` weiterzaehlen — die Zahl geht in die
    Rueckfrage vor dem Speichern (Refs #1733): eine abgebrochene Zaehlung
    wuerde dem Benutzer weniger zu verlierende Werte melden, als tatsaechlich
    verworfen wuerden."""

    def test_none_wert_bricht_die_zaehlung_nicht_ab(self):
        """Pin: ``x_count_filled_dynamic_values__mutmut_10`` (``continue`` ->
        ``break`` bei ``value is None``). Abweichung: die Mutante liefert 0
        statt 1, weil sie nach dem ersten ``None``-Wert abbricht."""
        assert count_filled_dynamic_values({"a": None, "b": "wert"}) == 1

    def test_leerstring_bricht_die_zaehlung_nicht_ab(self):
        """Pin: ``x_count_filled_dynamic_values__mutmut_13`` (``continue`` ->
        ``break`` bei einem nur aus Leerzeichen bestehenden String)."""
        assert count_filled_dynamic_values({"a": "   ", "b": "wert"}) == 1

    def test_leere_liste_bricht_die_zaehlung_nicht_ab(self):
        """Pin: ``x_count_filled_dynamic_values__mutmut_16`` (``continue`` ->
        ``break`` bei einer leeren Liste/Map/Menge)."""
        assert count_filled_dynamic_values({"a": [], "b": "wert"}) == 1


class TestNormalizeFileMarkerSortAndIdHandling:
    """``normalize_file_marker`` MUSS den expliziten ``sort``-Wert je Eintrag
    uebernehmen, einen fehlenden ``sort``-Wert auf den Listenindex zurueck-
    fallen lassen (nicht auf ``None`` crashen) und Eintraege ohne ``id``
    verwerfen."""

    def test_expliziter_sort_wert_bleibt_erhalten(self):
        """Pin: ``x_normalize_file_marker__mutmut_15`` (``e.get(None, i)``
        statt ``e.get("sort", i)``), ``_19`` (``"XXsortXX"``) und ``_20``
        (``"SORT"``). Abweichung: alle drei finden den Schluessel ``"sort"``
        nicht mehr und liefern den Listenindex (0, 1) statt der gesetzten
        Werte (5, 1)."""
        marker = {
            "__files__": True,
            "entries": [
                {"id": "11111111-1111-1111-1111-111111111111", "sort": 5},
                {"id": "22222222-2222-2222-2222-222222222222", "sort": 1},
            ],
        }
        assert normalize_file_marker(marker) == [
            {"id": "11111111-1111-1111-1111-111111111111", "sort": 5},
            {"id": "22222222-2222-2222-2222-222222222222", "sort": 1},
        ]

    def test_fehlender_sort_wert_faellt_auf_den_index_zurueck(self):
        """Pin: ``x_normalize_file_marker__mutmut_16``
        (``e.get("sort", None)``) und ``_18`` (``e.get("sort", )``, gleicher
        Effekt). Abweichung: beide liefern ``None`` als Default statt des
        Listenindex — ``int(None)`` wirft ``TypeError`` statt ``sort: 0`` zu
        liefern."""
        marker = {"__files__": True, "entries": [{"id": "11111111-1111-1111-1111-111111111111"}]}
        assert normalize_file_marker(marker) == [{"id": "11111111-1111-1111-1111-111111111111", "sort": 0}]

    def test_eintrag_ohne_id_wird_nicht_aufgenommen(self):
        """Pin: ``x_normalize_file_marker__mutmut_22`` (Filter ``e and
        e.get("id")`` -> ``e or e.get("id")``). Abweichung: ein nicht-leeres
        Dict ohne ``"id"`` wird zusaetzlich aufgenommen (mit ``id: "None"``)."""
        marker = {
            "__files__": True,
            "entries": [
                {"sort": 0},
                {"id": "11111111-1111-1111-1111-111111111111", "sort": 1},
            ],
        }
        assert normalize_file_marker(marker) == [{"id": "11111111-1111-1111-1111-111111111111", "sort": 1}]


class TestValidateDataJsonUnknownFieldLogging:
    """Unbekannte Slugs werden aus ``data_json`` entfernt UND mit exaktem
    Meldungstext geloggt — Betriebs-/Auditsignal, dass ein Bestandsrest
    still verworfen wurde."""

    def test_unbekannte_slugs_werden_mit_exaktem_text_geloggt(self, facility, caplog):
        """Pin: ``x_validate_data_json__mutmut_16`` (``unknown = None`` — kein
        Log mehr), ``_20`` (``%s`` mit ``None`` statt der Menge), ``_21``
        (Meldungstext entfaellt komplett), ``_22`` (``%s`` bleibt
        unsubstituiert), ``_23`` (XX-Marker-Variante des Wortlauts) und
        ``_24`` (Kleinschreibung). Eine ``in``-Pruefung wuerde ``_23``
        uebersehen (der echte Text ist als Teilstring in der XX-Variante
        enthalten) — deshalb exakte Listen-Gleichheit."""
        dt = _make_document_type(facility)
        ft = FieldTemplate.objects.create(
            facility=facility, name="Bekanntes Feld", field_type=FieldTemplate.FieldType.TEXT
        )
        _attach_field(dt, ft)

        with caplog.at_level(logging.WARNING, logger="core.services.dynamic_data"):
            validate_data_json(dt, {ft.slug: "wert", "unbekannter_slug": "x"})

        messages = [record.getMessage() for record in caplog.records]
        assert messages == ["Unknown fields in data_json removed: {'unbekannter_slug'}"]


class TestValidateDataJsonLoopContinuesPastRejectedKeys:
    """Ein verworfener Schluessel (unbekannter Slug oder FILE-Feld ohne
    gueltigen Marker) darf die restlichen, gueltigen Felder NICHT aus dem
    Ergebnis werfen — sonst gingen bei jedem Speichern Werte verloren, deren
    Slug rein zufaellig vor einem gueltigen Feld iteriert wird."""

    def test_unbekannter_slug_vor_einem_bekannten_feld_verwirft_nur_sich_selbst(self, facility):
        """Pin: ``x_validate_data_json__mutmut_28`` (``continue`` -> ``break``
        bei ``k not in allowed_slugs``). Abweichung: die Mutante liefert ein
        leeres Dict, weil sie beim ersten unbekannten Schluessel abbricht."""
        dt = _make_document_type(facility)
        ft = FieldTemplate.objects.create(
            facility=facility, name="Bekanntes Feld", field_type=FieldTemplate.FieldType.TEXT
        )
        _attach_field(dt, ft)

        cleaned = validate_data_json(dt, {"unbekannter_slug": "x", ft.slug: "wert"})

        assert cleaned == {ft.slug: "wert"}

    def test_datei_feld_ohne_marker_vor_einem_bekannten_feld_verwirft_nur_sich_selbst(self, facility):
        """Pin: ``x_validate_data_json__mutmut_34`` (``continue`` -> ``break``
        bei ``elif k in file_slugs``). Abweichung: die Mutante liefert ein
        leeres Dict, weil sie beim FILE-Feld ohne gueltigen Marker abbricht,
        statt nur dieses eine Feld zu verwerfen."""
        dt = _make_document_type(facility)
        ft_file = FieldTemplate.objects.create(facility=facility, name="Datei", field_type=FieldTemplate.FieldType.FILE)
        ft_text = FieldTemplate.objects.create(facility=facility, name="Text", field_type=FieldTemplate.FieldType.TEXT)
        _attach_field(dt, ft_file, sort_order=0)
        _attach_field(dt, ft_text, sort_order=1)

        cleaned = validate_data_json(dt, {ft_file.slug: "kein-marker-nur-text", ft_text.slug: "wert"})

        assert cleaned == {ft_text.slug: "wert"}


class TestDecryptDynamicTextDataSkipsMarkersWithoutAbortingTheLoop:
    """Beide Marker-Formate (Stufe A ``__file__`` und Stufe B ``__files__``)
    werden aus der Formular-Vorbelegung uebersprungen — OHNE die restliche
    Schleife abzubrechen. Ein durchgereichter Marker landete sonst als rohes
    Dict im Formular; ein Schleifenabbruch liesse spaeter iterierte Felder
    beim Bearbeiten leer erscheinen (Datenverlust-Risiko beim naechsten
    Speichern)."""

    def test_beide_markerformate_werden_uebersprungen_ohne_abbruch(self):
        """Pin: ``x_decrypt_dynamic_text_data__mutmut_4`` (``and`` statt
        ``or`` — kein Einzel-Marker wird mehr erkannt), ``_5``/``_6``/``_7``
        (Stufe-A-Schluessel ``"__file__"`` falsch/fehlend), ``_8``/``_9``/
        ``_10`` (Stufe-B-Schluessel ``"__files__"`` falsch/fehlend) und
        ``_11`` (``continue`` -> ``break`` — das nachfolgende Textfeld wird
        gar nicht mehr erreicht). Abweichung: unter jeder dieser Mutanten
        weicht das Ergebnis von ``{"text": "Klartext"}`` ab (rohe Marker-
        Dicts im Ergebnis oder das Textfeld fehlt komplett)."""
        stufe_a = {"__file__": True, "attachment_id": "11111111-1111-1111-1111-111111111111"}
        stufe_b = {"__files__": True, "entries": [{"id": "22222222-2222-2222-2222-222222222222", "sort": 0}]}
        data_json = {"datei_a": stufe_a, "datei_b": stufe_b, "text": "Klartext"}

        assert decrypt_dynamic_text_data(data_json) == {"text": "Klartext"}


class TestDecryptDynamicTextDataFallbackDefaultOnCorruptToken:
    """Ein korruptes Fernet-Token faellt auf den LEERSTRING zurueck — nicht
    auf ``None`` und nicht auf den unuebersetzten Rohstring aus
    ``safe_decrypt``s eigenem Funktions-Default."""

    def test_korruptes_token_liefert_leerstring(self):
        """Pin: ``x_decrypt_dynamic_text_data__mutmut_14`` (``default=None``),
        ``_16`` (Default-Argument entfaellt, ``safe_decrypt`` nutzt seinen
        eigenen Funktions-Default ``"[verschlüsselt]"``) und ``_17``
        (``default="XXXX"``)."""
        broken = {"__encrypted__": True, "value": "kaputter-token-kein-gueltiges-fernet-format"}

        assert decrypt_dynamic_text_data({"feld": broken}) == {"feld": ""}


class TestFileDisplayEntryReturnsExactlyTheDocumentedKeys:
    """``_file_display_entry`` liefert GENAU die sieben dokumentierten
    Schluessel mit den durchgereichten Werten — kein umbenannter Schluessel,
    keine maskierte Sensitivity/Verschluesselung, ``restricted`` bleibt
    ``False`` (der Aufrufer hat das Sensitivity-Gate bereits VOR dem Aufruf
    entschieden), ``orphaned`` faellt ohne expliziten Wert auf ``False``
    zurueck."""

    def test_datei_eintrag_hat_exakt_die_erwarteten_schluessel_und_werte(self, facility, staff_user, client_identified):
        """Pin: ``x__file_display_entry__mutmut_1`` (Default ``orphaned=True``),
        ``_10``/``_11`` (Schluessel ``"label"`` umbenannt), ``_17``/``_18``
        (``"is_sensitive"`` umbenannt), ``_19`` (``bool(None)`` statt
        ``bool(field_sensitivity)`` — maskiert die Sensitivity-Anzeige),
        ``_20``/``_21`` (``"is_encrypted"`` umbenannt), ``_22``/``_23``
        (``"restricted"`` umbenannt) und ``_24`` (``restricted: True`` statt
        ``False``)."""
        ft = FieldTemplate.objects.create(
            facility=facility,
            name="Nachweis (Pin099)",
            field_type=FieldTemplate.FieldType.FILE,
            sensitivity=DocumentType.Sensitivity.ELEVATED,
        )
        attachment = store_encrypted_file(facility, _upload(), ft, client_identified, staff_user)
        attachments_by_pk, predecessor_index = owner_attachment_index(client_identified)
        marker = {"__files__": True, "entries": [{"id": str(attachment.pk), "sort": 0}]}

        entry = _file_display_entry(
            "Nachweis-Label",
            marker,
            field_sensitivity=DocumentType.Sensitivity.ELEVATED,
            is_encrypted=True,
            attachments_by_pk=attachments_by_pk,
            predecessor_index=predecessor_index,
        )

        assert entry is not None
        assert set(entry.keys()) == {
            "label",
            "is_file",
            "entries",
            "is_sensitive",
            "is_encrypted",
            "restricted",
            "orphaned",
        }
        assert entry["label"] == "Nachweis-Label"
        assert entry["is_file"] is True
        assert len(entry["entries"]) == 1
        assert entry["is_sensitive"] is True
        assert entry["is_encrypted"] is True
        assert entry["restricted"] is False
        assert entry["orphaned"] is False


class TestDisplayEntryRestrictedBranchReturnsExactlyTheDocumentedKeys:
    """Der restricted-Zweig (Feld fuer die Rolle nicht sichtbar) liefert
    GENAU die fuenf dokumentierten Schluessel mit dem uebersetzten
    Platzhaltertext — kein umbenannter Schluessel, keine maskierte
    Sensitivity, kein faelschlich gesetztes ``orphaned`` bei einem regulaeren
    (nicht-verwaisten) Feld."""

    def test_eingeschraenkter_eintrag_hat_exakt_die_erwarteten_schluessel_und_werte(self, assistant_user):
        """Pin: ``x__display_entry__mutmut_1`` (Default ``orphaned=True`` —
        ``build_dynamic_fields_display`` ruft die Funktion nie mit
        explizitem ``orphaned`` auf, ein regulaeres Feld truege damit
        faelschlich ``"orphaned": True``), ``_15`` (XX-Marker-Variante von
        ``"[Eingeschränkt]"``), ``_18``/``_19`` (``"is_sensitive"``
        umbenannt), ``_20`` (``bool(None)`` maskiert die Sensitivity-Anzeige
        GENAU im restricted-Fall) sowie ``_21``/``_22`` (``"is_encrypted"``
        umbenannt). ``assistant_user`` (Rollenmaximum NORMAL) darf ein
        HIGH-Feld nicht sehen — das Sensitivity-Gate greift zuverlaessig."""
        entry = _display_entry(
            "Testfeld",
            "wert-fuer-restricted",
            field_sensitivity=DocumentType.Sensitivity.HIGH,
            is_encrypted=True,
            ft=None,
            user=assistant_user,
            doc_sensitivity=DocumentType.Sensitivity.NORMAL,
            attachments_by_pk={},
            predecessor_index={},
        )

        assert entry == {
            "label": "Testfeld",
            "value": "[Eingeschränkt]",
            "is_sensitive": True,
            "is_encrypted": True,
            "restricted": True,
        }


class TestDisplayEntryFileBranchPassesTheRealArgumentsThrough:
    """Der FILE-Zweig reicht Label, Sensitivity und Verschluesselungs-Flag
    UNVERAENDERT an ``_file_display_entry`` weiter — keines der drei darf
    durch ``None`` ersetzt werden."""

    def test_label_sensitivity_und_verschluesselung_kommen_unveraendert_an(
        self, facility, staff_user, client_identified
    ):
        """Pin: ``x__display_entry__mutmut_31`` (``label`` -> ``None`` beim
        Aufruf von ``_file_display_entry``), ``_33`` (``field_sensitivity``
        -> ``None`` — maskiert die Sensitivity-Anzeige fuer Datei-Felder) und
        ``_34`` (``is_encrypted`` -> ``None`` — maskiert die
        Verschluesselungs-Anzeige fuer Datei-Felder)."""
        ft = FieldTemplate.objects.create(
            facility=facility,
            name="Nachweis (Pin099b)",
            field_type=FieldTemplate.FieldType.FILE,
            sensitivity=DocumentType.Sensitivity.ELEVATED,
        )
        attachment = store_encrypted_file(facility, _upload(name="beleg2.pdf"), ft, client_identified, staff_user)
        attachments_by_pk, predecessor_index = owner_attachment_index(client_identified)
        marker = {"__files__": True, "entries": [{"id": str(attachment.pk), "sort": 0}]}

        entry = _display_entry(
            "Beleg-Label",
            marker,
            field_sensitivity=DocumentType.Sensitivity.ELEVATED,
            is_encrypted=True,
            ft=None,
            user=staff_user,
            doc_sensitivity=DocumentType.Sensitivity.NORMAL,
            attachments_by_pk=attachments_by_pk,
            predecessor_index=predecessor_index,
        )

        assert entry is not None
        assert entry["label"] == "Beleg-Label"
        assert entry["is_sensitive"] is True
        assert entry["is_encrypted"] is True


class TestDisplayEntryNormalBranchFallbackTextIsTranslated:
    """Ein korruptes Fernet-Token faellt im normalen (nicht-restricted,
    nicht-FILE) Zweig auf den UEBERSETZTEN Platzhaltertext zurueck — nicht
    auf ``None`` und nicht auf einen unuebersetzten Rohstring. Unter
    aktiver EN-Sprache unterscheidet das den echten ``gettext_lazy``-Aufruf
    zuverlaessig von jedem Rohstring-Fallback."""

    def test_korruptes_token_liefert_den_uebersetzten_platzhaltertext(self, staff_user):
        """Pin: ``x__display_entry__mutmut_47`` (``default=None``), ``_49``
        (Default-Argument entfaellt — ``safe_decrypt`` nutzt seinen eigenen,
        NICHT uebersetzten Funktions-Default), ``_50`` (``_(None)``), ``_51``
        (XX-Marker-Variante, kein Katalogeintrag -> unuebersetzter Rueckfall)
        und ``_52`` (Grossschreibungs-Variante, ebenfalls kein
        Katalogeintrag)."""
        broken = {"__encrypted__": True, "value": "kaputter-token-kein-gueltiges-fernet-format"}

        with translation.override("en"):
            entry = _display_entry(
                "Feld",
                broken,
                field_sensitivity="",
                is_encrypted=True,
                ft=None,
                user=staff_user,
                doc_sensitivity=DocumentType.Sensitivity.NORMAL,
                attachments_by_pk={},
                predecessor_index={},
            )
            assert entry["value"] == "[encrypted]"


class TestDisplayEntryNormalBranchReturnsExactlyTheDocumentedKeys:
    """Der normale (nicht-restricted, nicht-FILE) Zweig liefert GENAU die
    fuenf dokumentierten Schluessel — kein umbenannter Schluessel, keine
    maskierte Sensitivity/Verschluesselung."""

    def test_normaler_eintrag_hat_exakt_die_erwarteten_schluessel_und_werte(self, staff_user):
        """Pin: ``x__display_entry__mutmut_62``/``_63`` (``"is_sensitive"``
        umbenannt), ``_64`` (``bool(None)`` maskiert die Sensitivity-
        Anzeige) sowie ``_65``/``_66`` (``"is_encrypted"`` umbenannt)."""
        entry = _display_entry(
            "Textfeld",
            "Klartextwert",
            field_sensitivity=DocumentType.Sensitivity.ELEVATED,
            is_encrypted=True,
            ft=None,
            user=staff_user,
            doc_sensitivity=DocumentType.Sensitivity.NORMAL,
            attachments_by_pk={},
            predecessor_index={},
        )

        assert entry == {
            "label": "Textfeld",
            "value": "Klartextwert",
            "is_sensitive": True,
            "is_encrypted": True,
            "restricted": False,
        }


class TestBuildDynamicDataContextGateReturnsExactlyTheDocumentedKeys:
    """Das Sensitivity-Gate (#774-Muster) liefert bei einem fuer die Rolle
    nicht sichtbaren Dokumentationstyp GENAU die drei dokumentierten
    Schluessel mit leeren Werten — kein umbenannter Schluessel, der den
    Typnamen oder Feldlabels ungewollt (oder gar nicht mehr, fail-open) ins
    Template durchreichen wuerde."""

    def test_gate_liefert_exakt_die_drei_erwarteten_schluessel(self, facility, assistant_user):
        """Pin: ``x_build_dynamic_data_context__mutmut_8``/``_9``
        (``"dynamic_document_type"`` umbenannt), ``_10``/``_11``
        (``"dynamic_fields"`` umbenannt) und ``_12``/``_13``
        (``"dynamic_orphan_fields"`` umbenannt)."""
        dt = _make_document_type(facility, sensitivity=DocumentType.Sensitivity.HIGH)

        context = build_dynamic_data_context(dt, {"irgendein_feld": "wert"}, assistant_user)

        assert context == {"dynamic_document_type": None, "dynamic_fields": [], "dynamic_orphan_fields": []}


class TestBuildDynamicFieldsDisplayQueriesTheOrderedLookup:
    """Die Feldreihenfolge der Anzeige MUSS ueber ``ordered=True`` aus der
    konfigurierten Block-Reihenfolge kommen — nicht aus einer unsortierten
    Query, deren Reihenfolge fuer Benutzer willkuerlich waere."""

    def test_build_field_template_lookup_wird_mit_ordered_true_aufgerufen(self, facility, staff_user):
        """Pin: ``x_build_dynamic_fields_display__mutmut_6``
        (``ordered=None``), ``_8`` (Argument entfaellt, Default ``False``)
        und ``_9`` (``ordered=False``)."""
        dt = _make_document_type(facility)
        ft = FieldTemplate.objects.create(facility=facility, name="Feld", field_type=FieldTemplate.FieldType.TEXT)
        _attach_field(dt, ft)

        original_lookup = dynamic_data_module.build_field_template_lookup
        calls: list[object] = []

        def spy(document_type, *, ordered=False):
            calls.append(ordered)
            return original_lookup(document_type, ordered=ordered)

        with mock.patch.object(dynamic_data_module, "build_field_template_lookup", spy):
            build_dynamic_fields_display(dt, {ft.slug: "wert"}, staff_user)

        assert calls == [True]


class TestBuildDynamicFieldsDisplayLoopContinuesPastUnfilledFields:
    """Ein noch nicht ausgefuelltes Feld (Slug ohne Wert in ``data_json``)
    darf ein SPAETER in der Feldreihenfolge liegendes, tatsaechlich
    gefuelltes Feld nicht aus der Anzeige werfen."""

    def test_ein_leeres_feld_wirft_ein_spaeter_gefuelltes_feld_nicht_aus_der_anzeige(self, facility, staff_user):
        """Pin: ``x_build_dynamic_fields_display__mutmut_14`` (``continue``
        -> ``break`` bei ``slug not in values``). Abweichung: die Mutante
        liefert eine leere Liste, weil sie beim ersten leeren Feld
        abbricht, statt das gefuellte Folgefeld noch anzuzeigen."""
        dt = _make_document_type(facility)
        ft_empty = FieldTemplate.objects.create(facility=facility, name="Leer", field_type=FieldTemplate.FieldType.TEXT)
        ft_filled = FieldTemplate.objects.create(
            facility=facility, name="Gefuellt", field_type=FieldTemplate.FieldType.TEXT
        )
        _attach_field(dt, ft_empty, sort_order=0)
        _attach_field(dt, ft_filled, sort_order=1)

        entries = build_dynamic_fields_display(dt, {ft_filled.slug: "wert"}, staff_user)

        assert [entry["label"] for entry in entries] == ["Gefuellt"]


class TestBuildDynamicFieldsDisplayPassesIsEncryptedThrough:
    """Das ``is_encrypted``-Flag des ``FieldTemplate`` MUSS unveraendert im
    Anzeige-Eintrag landen — nicht als ``None``, was die
    Verschluesselungs-Anzeige maskieren wuerde."""

    def test_is_encrypted_flag_wird_unveraendert_durchgereicht(self, facility, staff_user):
        """Pin: ``x_build_dynamic_fields_display__mutmut_19``
        (``is_encrypted=None`` statt ``is_encrypted=ft.is_encrypted``)."""
        dt = _make_document_type(facility)
        ft = FieldTemplate.objects.create(
            facility=facility, name="Verschluesselt", field_type=FieldTemplate.FieldType.TEXT, is_encrypted=True
        )
        _attach_field(dt, ft)

        entries = build_dynamic_fields_display(dt, {ft.slug: "wert"}, staff_user)

        assert entries[0]["is_encrypted"] is True

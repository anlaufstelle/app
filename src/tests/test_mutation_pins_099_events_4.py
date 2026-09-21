"""Pin-Tests der Welle L (0.99, Refs #2174/#2193) — Vollmutation-Survivor-Triage,
Batch ``events-4`` (Dateifamilie ``core.services.events.context`` /
``core.services.events.deletion`` / ``core.services.events.fields``).

Quelle: ``welle-l-0.99/reports/m-scratch/batch-events-4.json`` (Triage
``triage-099-events-4.json``). 63 Survivors ueber drei Module; zwei davon
(``x__build_field_display_entry__mutmut_7`` und
``x_build_event_detail_context__mutmut_16``) sind nachweislich aequivalent —
siehe Begruendung in ``triage-099-events-4.json`` — und werden hier NICHT
gepinnt.

``_encryption_key`` liegt modulweit an (Lehre 112): der Mutantenbaum hat
keine ``.env`` und damit keinen ``ENCRYPTION_KEY``.
"""

from __future__ import annotations

import uuid

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone, translation

from core.models import (
    Attachment,
    DeletionRequest,
    DocumentType,
    DocumentTypeField,
    Event,
    FieldGroup,
    FieldTemplate,
    User,
)
from core.services.events.context import (
    _build_field_display_entry,
    _build_history_with_slug_info,
    _build_secondary_action_flags,
    build_event_detail_context,
    filtered_server_data_json,
)
from core.services.events.deletion import (
    approve_deletion,
    get_pending_event_deletion_request,
    reject_deletion,
    request_deletion,
)
from core.services.events.fields import (
    _snapshot_field_metadata,
    compute_event_search_text,
    split_file_and_text_data,
    stage_index,
)
from core.services.file_vault import encrypt_field

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


# ---------------------------------------------------------------------------
# Lokale Bau-Helfer (bewusst datei-lokal, keine Importe aus anderen Testdateien)
# ---------------------------------------------------------------------------


def _make_doc_type(facility, *, name: str = "Doc", sensitivity: str = DocumentType.Sensitivity.NORMAL) -> DocumentType:
    return DocumentType.objects.create(facility=facility, sensitivity=sensitivity, name=name)


def _make_field_template(
    facility,
    *,
    name: str,
    field_type: str = FieldTemplate.FieldType.TEXT,
    sensitivity: str = "",
    is_encrypted: bool = False,
) -> FieldTemplate:
    return FieldTemplate.objects.create(
        facility=facility,
        name=name,
        field_type=field_type,
        sensitivity=sensitivity,
        is_encrypted=is_encrypted,
    )


def _attach(
    doc_type: DocumentType, field_template: FieldTemplate, *, sort_order: int = 0, field_group=None
) -> DocumentTypeField:
    return DocumentTypeField.objects.create(
        document_type=doc_type, field_template=field_template, sort_order=sort_order, field_group=field_group
    )


def _make_event(facility, client, doc_type, user, *, data_json) -> Event:
    return Event.objects.create(
        facility=facility,
        client=client,
        document_type=doc_type,
        occurred_at=timezone.now(),
        data_json=data_json,
        created_by=user,
    )


def _make_user(facility, *, role: str, suffix: str, can_confirm_deletion: bool = False) -> User:
    user = User.objects.create_user(
        username=f"events4-{role}-{suffix}",
        role=role,
        facility=facility,
        is_staff=True,
        can_confirm_deletion=can_confirm_deletion,
    )
    user.set_password("x" * 24)
    user.save()
    return user


# ============================================================================
# core.services.events.context.filtered_server_data_json
# ============================================================================


class TestFilteredServerDataJson:
    def test_erkennt_verwaiste_slugs_und_gated_sie_ueber_ihre_eigene_vorlage(
        self, facility, staff_user, client_identified
    ):
        """Pins ``x_filtered_server_data_json__mutmut_6`` (Orphan-Filter
        ``slug not in field_templates`` -> ``slug in field_templates``,
        invertiert die Waisen-Erkennung), ``…__mutmut_19`` und ``…__mutmut_20``
        (``orphan_gating_sensitivity(orphan_templates.get(slug))`` ->
        ``orphan_gating_sensitivity(None)`` bzw. ``orphan_templates.get(None)``,
        beide erzwingen HIGH-Gating unabhaengig von der echten Vorlage).

        Ein Slug OHNE Zuordnung am aktuellen DocumentType, aber mit einer real
        existierenden ELEVATED-Vorlage in derselben Einrichtung (Refs #1725,
        „Feld entfernt, Daten bleiben") muss fuer STAFF sichtbar bleiben — die
        Vorlage erlaubt ELEVATED. Alle drei Mutanten gaten stattdessen
        HIGH-fail-closed und blenden den Wert fuer STAFF aus.
        """
        doc_type = _make_doc_type(facility)
        ft_orphan = _make_field_template(facility, name="Alt-Feld", sensitivity=DocumentType.Sensitivity.ELEVATED)
        # ft_orphan bewusst NICHT an doc_type angehaengt — der Slug liegt nur
        # noch in data_json (Refs #1725).
        event = _make_event(facility, client_identified, doc_type, staff_user, data_json={ft_orphan.slug: "alter wert"})

        result = filtered_server_data_json(staff_user, event)

        assert result == {ft_orphan.slug: "alter wert"}

    def test_verarbeitet_felder_nach_einem_datei_marker_weiter(self, facility, staff_user, client_identified):
        """Pins ``x_filtered_server_data_json__mutmut_77``: das ``continue`` nach
        dem FILE-Marker-Zweig wird zu ``break`` — jedes weitere Feld nach einem
        Datei-Marker in ``data_json`` faellt still aus dem Konflikt-Diff
        (Datenverlust im Merge-Vergleich)."""
        doc_type = _make_doc_type(facility)
        ft_file = _make_field_template(facility, name="Anlage", field_type=FieldTemplate.FieldType.FILE)
        ft_text = _make_field_template(facility, name="Notiz")
        _attach(doc_type, ft_file, sort_order=0)
        _attach(doc_type, ft_text, sort_order=1)
        event = _make_event(
            facility,
            client_identified,
            doc_type,
            staff_user,
            data_json={
                ft_file.slug: {"__file__": True, "attachment_id": str(uuid.uuid4())},
                ft_text.slug: "sichtbarer Text",
            },
        )

        result = filtered_server_data_json(staff_user, event)

        assert result[ft_text.slug] == "sichtbarer Text"

    def test_fallback_bei_kaputtem_verschluesselungstoken_ist_leerstring(self, facility, staff_user, client_identified):
        """Pins ``x_filtered_server_data_json__mutmut_80`` (``default=""`` ->
        ``default=None``), ``…__mutmut_82`` (``default``-Keyword faellt ganz
        weg -> ``safe_decrypt``s eigener Default ``"[verschlüsselt]"``) und
        ``…__mutmut_83`` (XX-Marker ``default="XXXX"``). Der Konflikt-Diff
        braucht fuer ein kaputtes Token exakt den Leerstring — nicht ``None``,
        nicht den Anzeige-Platzhalter aus der Detail-Ansicht."""
        doc_type = _make_doc_type(facility)
        ft = _make_field_template(facility, name="Notiz")
        _attach(doc_type, ft)
        event = _make_event(
            facility,
            client_identified,
            doc_type,
            staff_user,
            data_json={ft.slug: {"__encrypted__": True, "value": "kaputtes-token"}},
        )

        result = filtered_server_data_json(staff_user, event)

        assert result == {ft.slug: ""}


# ============================================================================
# core.services.events.context._build_field_display_entry
# ============================================================================


class TestBuildFieldDisplayEntry:
    def test_restricted_eintrag_hat_exakten_wortlaut_und_schluessel(self, facility, assistant_user):
        """Pins ``x__build_field_display_entry__mutmut_29`` (XX-Marker auf
        ``"[Eingeschränkt]"`` — der bestehende ``test_restricted_returns_masked_entry``
        prueft nur eine Teilstring-Enthaltung und uebersieht den Marker),
        ``…__mutmut_32``/``…__mutmut_33`` (Schluessel ``is_sensitive`` ->
        ``XXis_sensitiveXX``/``IS_SENSITIVE``) und ``…__mutmut_34``
        (``bool(field_sensitivity)`` -> ``bool(None)``, hier mit
        ELEVATED-Feld-Sensitivity beobachtbar, da ``bool("elevated")`` != ``bool(None)``)."""
        doc_type = _make_doc_type(facility, sensitivity=DocumentType.Sensitivity.NORMAL)
        ft = _make_field_template(facility, name="Diagnose", sensitivity=DocumentType.Sensitivity.ELEVATED)

        entry = _build_field_display_entry(ft.slug, "geheim", ft, assistant_user, doc_type.sensitivity, {}, {})

        assert entry == {
            "label": "Diagnose",
            "value": "[Eingeschränkt]",
            "is_sensitive": True,
            "restricted": True,
            "orphaned": False,
        }

    def test_datei_eintrag_hat_alle_erwarteten_schluessel_und_werte(self, facility, staff_user, sample_event):
        """Pins die dreizehn Schluessel-/Wert-Mutanten des FILE-Zweigs:
        ``x__build_field_display_entry__mutmut_50``/``…_51`` (``label``),
        ``…_59``/``…_60`` (``original_filename``), ``…_63``/``…_64``
        (``file_size_display``), ``…_67``/``…_68`` (``prior_versions``),
        ``…_73``/``…_74`` (``is_sensitive``-Schluessel), ``…_75``
        (``bool(field_sensitivity)`` -> ``bool(None)``), ``…_76``/``…_77``
        (``orphaned``). Eine vollstaendige Dict-Gleichheit erfasst jede
        Schluessel-Umbenennung sofort (fehlender Original-Key ->
        ``AssertionError``)."""
        doc_type = _make_doc_type(facility, sensitivity=DocumentType.Sensitivity.NORMAL)
        ft = _make_field_template(
            facility,
            name="Anlage",
            field_type=FieldTemplate.FieldType.FILE,
            sensitivity=DocumentType.Sensitivity.ELEVATED,
        )
        att = Attachment.objects.create(
            event=sample_event,
            facility=facility,
            field_template=ft,
            storage_filename=f"{uuid.uuid4().hex}.enc",
            original_filename_encrypted=encrypt_field("Anlage.pdf"),
            file_size=1234,
            mime_type="application/pdf",
        )
        marker = {"__file__": True, "attachment_id": str(att.pk)}

        entry = _build_field_display_entry(ft.slug, marker, ft, staff_user, doc_type.sensitivity, {att.pk: att}, {})

        assert entry["is_file"] is True
        assert set(entry.keys()) == {
            "label",
            "is_file",
            "attachment_id",
            "original_filename",
            "file_size_display",
            "prior_versions",
            "entries",
            "is_sensitive",
            "orphaned",
        }
        assert entry["label"] == "Anlage"
        assert entry["attachment_id"] == str(att.pk)
        assert entry["original_filename"] == "Anlage.pdf"
        assert entry["prior_versions"] == []
        assert len(entry["entries"]) == 1
        assert entry["entries"][0]["attachment_id"] == str(att.pk)
        assert entry["is_sensitive"] is True
        assert entry["orphaned"] is False

    def test_entschluesselungsfehler_zeigt_uebersetzten_platzhalter(self, facility, staff_user):
        """Pins ``x__build_field_display_entry__mutmut_80`` (``default=None``),
        ``…__mutmut_82`` (``_()``-Wrapping faellt weg — im Deutschen textgleich,
        aber unuebersetzt), ``…__mutmut_83`` (``_(None)``), ``…__mutmut_84``
        (XX-Marker) und ``…__mutmut_85`` (Grossschreibung). ``docs/en``-Katalog
        uebersetzt ``"[verschlüsselt]"`` -> ``"[encrypted]"``
        (``src/locale/en/LC_MESSAGES/django.po``); unter aktiver EN-Lokalisierung
        zeigt nur das Original genau diese Uebersetzung — jeder Mutant faellt auf
        einen unuebersetzten/falschen Text zurueck."""
        doc_type = _make_doc_type(facility)
        ft = _make_field_template(facility, name="Notiz")
        corrupt = {"__encrypted__": True, "value": "kaputtes-token"}

        with translation.override("en"):
            entry = _build_field_display_entry(ft.slug, corrupt, ft, staff_user, doc_type.sensitivity, {}, {})
            assert entry["value"] == "[encrypted]"


# ============================================================================
# core.services.events.context._build_history_with_slug_info
# ============================================================================


class TestBuildHistoryWithSlugInfo:
    def test_nutzt_select_related_fuer_field_template(
        self, django_assert_num_queries, facility, client_identified, staff_user
    ):
        """Pins ``x__build_history_with_slug_info__mutmut_2``:
        ``select_related("field_template")`` -> ``select_related(None)``. Das
        Ergebnis bleibt inhaltlich gleich, die Query-Zahl nicht: mit dem Join
        kostet der Aufbau des ``shared_slug_info``-Lookups + die History-Query
        genau 2 Queries; ohne Join kommt je Feld eine zusaetzliche
        ``field_template``-Query dazu."""
        doc_type = _make_doc_type(facility)
        ft1 = _make_field_template(facility, name="Feld1")
        ft2 = _make_field_template(facility, name="Feld2")
        _attach(doc_type, ft1, sort_order=0)
        _attach(doc_type, ft2, sort_order=1)
        event = _make_event(facility, client_identified, doc_type, staff_user, data_json={ft1.slug: "a", ft2.slug: "b"})

        with django_assert_num_queries(2):
            history = _build_history_with_slug_info(event)

        assert history == []


# ============================================================================
# core.services.events.context._build_secondary_action_flags
# ============================================================================


class TestBuildSecondaryActionFlags:
    def test_primary_position_hat_exakten_wortlaut(self, facility, staff_user, assistant_user):
        """Pins ``x__build_secondary_action_flags__mutmut_25`` (XX-Marker auf
        ``"primary"``) und ``…__mutmut_26`` (Grossschreibung ``"PRIMARY"``).
        Ohne Client/Fall und ohne Loeschrecht bleibt ``bleibt_im_menue`` False —
        der Fallback-Wert muss exakt ``"primary"`` lauten."""
        doc_type = _make_doc_type(facility)
        event = Event.objects.create(
            facility=facility, document_type=doc_type, occurred_at=timezone.now(), data_json={}, created_by=staff_user
        )

        flags = _build_secondary_action_flags(event, assistant_user)

        assert flags["secondary_action_position"] == "primary"


# ============================================================================
# core.services.events.context.build_event_detail_context
# ============================================================================


class TestBuildEventDetailContext:
    def test_folgt_der_block_reihenfolge_nicht_der_rohen_sort_order(self, facility, client_identified, staff_user):
        """Pins ``x_build_event_detail_context__mutmut_3`` (``ordered=True`` ->
        ``ordered=None``), ``…__mutmut_5`` (Keyword faellt ganz weg -> Default
        ``False``) und ``…__mutmut_6`` (``ordered=False``) — alle drei machen
        den nachfolgenden Lookup ``falsy`` und damit identisch zueinander.

        Eine Gruppe mit globaler Blockposition 0 und ein freies Feld mit
        Blockposition 1 muessen die Gruppe zuerst zeigen — obwohl das
        GRUPPEN-interne ``sort_order`` des gruppierten Feldes (99) roh
        sortiert HINTER dem freien Feld (1) laege. ``ordered=True`` loest ueber
        ``block_ordered_dtf_queryset`` die Gruppenposition auf; ohne sie
        gewinnt das nackte ``Meta.ordering = ["sort_order"]``."""
        doc_type = _make_doc_type(facility)
        group = FieldGroup.objects.create(document_type=doc_type, name="Gruppe", sort_order=0)
        ft_grouped = _make_field_template(facility, name="Gruppiert")
        ft_free = _make_field_template(facility, name="Frei")
        _attach(doc_type, ft_grouped, sort_order=99, field_group=group)
        _attach(doc_type, ft_free, sort_order=1)
        event = _make_event(
            facility,
            client_identified,
            doc_type,
            staff_user,
            data_json={ft_free.slug: "frei", ft_grouped.slug: "gruppe"},
        )

        ctx = build_event_detail_context(event, staff_user)

        labels = [entry["label"] for entry in ctx["fields_display"]]
        assert labels == ["Gruppiert", "Frei"]

    def test_verwaistes_datei_feld_mit_anhang_stuerzt_nicht_ab(self, facility, client_identified, staff_user):
        """Pins ``x_build_event_detail_context__mutmut_45`` (``attachments_by_pk``
        -> ``None`` im Waisen-Zweig) und ``…__mutmut_46`` (``predecessor_index``
        -> ``None`` ebendort). Beide fuehren zu ``AttributeError`` in
        ``resolve_file_entries``/``build_prior_versions``, sobald ein verwaistes
        Datei-Feld einen aufloesbaren Anhang hat — die gesamte Detail-Ansicht
        stuerzt ab statt den Anhang korrekt anzuzeigen."""
        doc_type = _make_doc_type(facility)
        ft_orphan = _make_field_template(facility, name="Alte Anlage", field_type=FieldTemplate.FieldType.FILE)
        # ft_orphan bewusst NICHT an doc_type angehaengt (verwaist, Refs #1725).
        event = _make_event(facility, client_identified, doc_type, staff_user, data_json={})
        att = Attachment.objects.create(
            event=event,
            facility=facility,
            field_template=ft_orphan,
            storage_filename=f"{uuid.uuid4().hex}.enc",
            original_filename_encrypted=encrypt_field("Alt.pdf"),
            file_size=10,
            mime_type="application/pdf",
        )
        event.data_json = {ft_orphan.slug: {"__file__": True, "attachment_id": str(att.pk)}}
        event.save(update_fields=["data_json"])

        ctx = build_event_detail_context(event, staff_user)

        orphaned_entries = [entry for entry in ctx["fields_display"] if entry.get("orphaned")]
        assert len(orphaned_entries) == 1
        assert orphaned_entries[0]["is_file"] is True
        assert orphaned_entries[0]["attachment_id"] == str(att.pk)


# ============================================================================
# core.services.events.deletion.get_pending_event_deletion_request
# ============================================================================


class TestGetPendingEventDeletionRequest:
    def test_ignoriert_antraege_fremder_target_types(self, facility, staff_user, sample_event):
        """Pins ``x_get_pending_event_deletion_request__mutmut_4``: der Filter
        ``target_type="Event"`` faellt aus dem Lookup weg. Ein PENDING-Antrag
        eines ANDEREN Zieltyps mit zufaellig gleicher ``target_id`` (hier
        konstruiert — beide sind UUID-Felder ohne FK-Bindung) wuerde ohne den
        Filter faelschlich als Loeschantrag DIESES Ereignisses gelten."""
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.CLIENT,
            target_id=sample_event.pk,
            reason="Fremdtyp mit kollidierender ID",
            requested_by=staff_user,
        )

        assert get_pending_event_deletion_request(sample_event) is None


# ============================================================================
# core.services.events.deletion.approve_deletion — Meldungen
# ============================================================================


class TestApproveDeletionMeldungen:
    def test_vier_augen_meldung_hat_exakten_wortlaut(self, facility, client_qualified, doc_type_contact, staff_user):
        """Pins ``x_approve_deletion__mutmut_2`` (Meldung -> ``None``),
        ``…__mutmut_3`` (XX-Marker), ``…__mutmut_4``/``…__mutmut_5``
        (Klein-/Grossschreibung) — alle vier veraendern den Wortlaut der
        Vier-Augen-Meldung, wenn Antragsteller und Reviewer identisch sind."""
        event = _make_event(facility, client_qualified, doc_type_contact, staff_user, data_json={"notiz": "x"})
        dr = request_deletion(event, staff_user, "Antrag")

        with translation.override("de"), pytest.raises(ValidationError) as excinfo:
            approve_deletion(dr, staff_user)

        assert excinfo.value.messages == ["Reviewer darf nicht der Antragsteller sein (Vier-Augen-Prinzip)."]

    def test_fehlendes_recht_meldung_hat_exakten_wortlaut(
        self, facility, client_qualified, doc_type_contact, staff_user
    ):
        """Pins ``x_approve_deletion__mutmut_8`` (XX-Marker auf der
        Recht-fehlt-Meldung). Reviewer ohne ``can_confirm_deletion`` (aber
        != Antragsteller) muss genau diesen Wortlaut bekommen."""
        reviewer = _make_user(facility, role=User.Role.STAFF, suffix="ohne-recht", can_confirm_deletion=False)
        event = _make_event(facility, client_qualified, doc_type_contact, staff_user, data_json={"notiz": "x"})
        dr = request_deletion(event, staff_user, "Antrag")

        with translation.override("de"), pytest.raises(ValidationError) as excinfo:
            approve_deletion(dr, reviewer)

        assert excinfo.value.messages == ["Reviewer benötigt das Recht 'Löschbestätigung' (Vier-Augen-Prinzip)."]

    def test_bereits_bearbeitet_meldung_hat_exakten_wortlaut(
        self, facility, client_qualified, doc_type_contact, staff_user, lead_user
    ):
        """Pins ``x_approve_deletion__mutmut_17`` (Meldung -> ``None``),
        ``…__mutmut_18`` (XX-Marker), ``…__mutmut_19``/``…__mutmut_20``
        (Klein-/Grossschreibung) am „bereits bearbeitet"-Zweig: ein zweiter
        Approve-Aufruf auf denselben (bereits APPROVED) Antrag."""
        event = _make_event(facility, client_qualified, doc_type_contact, staff_user, data_json={"notiz": "x"})
        dr = request_deletion(event, staff_user, "Antrag")
        approve_deletion(dr, lead_user)

        with translation.override("de"), pytest.raises(ValidationError) as excinfo:
            approve_deletion(dr, lead_user)

        assert excinfo.value.messages == ["Löschantrag wurde bereits bearbeitet (nicht mehr ausstehend)."]


# ============================================================================
# core.services.events.deletion.reject_deletion — Meldungen
# ============================================================================


class TestRejectDeletionMeldungen:
    def test_vier_augen_meldung_hat_exakten_wortlaut(self, facility, client_qualified, doc_type_contact, staff_user):
        """Pins ``x_reject_deletion__mutmut_3`` (XX-Marker auf der
        Vier-Augen-Meldung)."""
        event = _make_event(facility, client_qualified, doc_type_contact, staff_user, data_json={"notiz": "x"})
        dr = request_deletion(event, staff_user, "Antrag")

        with translation.override("de"), pytest.raises(ValidationError) as excinfo:
            reject_deletion(dr, staff_user)

        assert excinfo.value.messages == ["Reviewer darf nicht der Antragsteller sein (Vier-Augen-Prinzip)."]

    def test_fehlendes_recht_meldung_hat_exakten_wortlaut(
        self, facility, client_qualified, doc_type_contact, staff_user
    ):
        """Pinnt den Rechte-Guard des Reject-Pfads mit seinem vollständigen Wortlaut."""
        reviewer = _make_user(facility, role=User.Role.STAFF, suffix="reject-ohne-recht")
        event = _make_event(facility, client_qualified, doc_type_contact, staff_user, data_json={"notiz": "x"})
        dr = request_deletion(event, staff_user, "Antrag")

        with translation.override("de"), pytest.raises(ValidationError) as excinfo:
            reject_deletion(dr, reviewer)

        assert excinfo.value.messages == ["Reviewer benötigt das Recht 'Löschbestätigung' (Vier-Augen-Prinzip)."]

    def test_bereits_bearbeitet_meldung_hat_exakten_wortlaut(
        self, facility, client_qualified, doc_type_contact, staff_user, lead_user
    ):
        """Pins ``x_reject_deletion__mutmut_13`` (XX-Marker) und
        ``…__mutmut_14`` (Kleinschreibung) am „bereits bearbeitet"-Zweig: ein
        zweiter Reject-Aufruf auf denselben (bereits REJECTED) Antrag."""
        event = _make_event(facility, client_qualified, doc_type_contact, staff_user, data_json={"notiz": "x"})
        dr = request_deletion(event, staff_user, "Antrag")
        reject_deletion(dr, lead_user)

        with translation.override("de"), pytest.raises(ValidationError) as excinfo:
            reject_deletion(dr, lead_user)

        assert excinfo.value.messages == ["Löschantrag wurde bereits bearbeitet (nicht mehr ausstehend)."]


# ============================================================================
# core.services.events.fields.compute_event_search_text
# ============================================================================


class TestComputeEventSearchText:
    def test_ohne_dokumentationstyp_liefert_leerstring(self):
        """Pins ``x_compute_event_search_text__mutmut_4``: ``return ""`` ->
        ``return "XXXX"`` fuer ``document_type is None``."""
        assert compute_event_search_text({"a": "b"}, None) == ""

    def test_nutzt_select_related_fuer_field_template(self, django_assert_num_queries, facility):
        """Pins ``x_compute_event_search_text__mutmut_6``:
        ``select_related("field_template")`` -> ``select_related(None)`` beim
        Aufbau von ``field_meta`` — mit Join genau 1 Query, ohne Join eine
        zusaetzliche je Feld."""
        doc_type = _make_doc_type(facility)
        ft1 = _make_field_template(facility, name="Feld1")
        ft2 = _make_field_template(facility, name="Feld2")
        _attach(doc_type, ft1, sort_order=0)
        _attach(doc_type, ft2, sort_order=1)
        data = {ft1.slug: "hallo", ft2.slug: "welt"}

        with django_assert_num_queries(1):
            result = compute_event_search_text(data, doc_type)

        assert result == "hallo welt"

    def test_verarbeitet_alle_felder_trotz_verschluesselter_dict_und_leerer_werte(self, facility):
        """Pins sechs Mutanten in einem Durchlauf ueber fuenf Felder in
        definierter ``data_json``-Reihenfolge (a, verschluesselt, dict-Wert,
        Leerstring, e, Liste):

        * ``x_compute_event_search_text__mutmut_14`` (``continue`` -> ``break``
          nach einem verschluesselten Feld) — ohne den Kill wuerde Feld ``e``
          NIE erreicht, weil das verschluesselte Feld vorher abbricht.
        * ``…__mutmut_22`` (``continue`` -> ``break`` nach einem dict-Wert) —
          derselbe Verlust, ausgeloest durch das dict-wertige Feld ``c``.
        * ``…__mutmut_25`` (``and`` -> ``or`` im Listen-Filter) und die
          Liste ``["a", {"x": 1}, None, "b"]`` — der dict- und der
          None-Eintrag duerfen NICHT im Suchtext landen.
        * ``…__mutmut_28`` (``and`` -> ``or`` am finalen ``elif``) und
          ``…__mutmut_31`` (Leerstring-Literal -> ``"XXXX"``) — ein leeres
          Feld ``d`` zwischen zwei Textfeldern darf NICHT in den Suchtext
          einfliessen (sonst doppeltes Leerzeichen/zusaetzliches Token).
        * ``…__mutmut_35`` (Trenner ``" "`` -> ``"XX XX"`` beim finalen
          ``.join``) — die exakte Ein-Leerzeichen-Fuge zwischen den Teilen.
        """
        doc_type = _make_doc_type(facility)
        ft_a = _make_field_template(facility, name="A")
        ft_b = _make_field_template(facility, name="B", is_encrypted=True)
        ft_c = _make_field_template(facility, name="C")
        ft_d = _make_field_template(facility, name="D")
        ft_e = _make_field_template(facility, name="E")
        ft_f = _make_field_template(facility, name="F")
        for i, ft in enumerate((ft_a, ft_b, ft_c, ft_d, ft_e, ft_f)):
            _attach(doc_type, ft, sort_order=i)
        data = {
            ft_a.slug: "hallo",
            ft_b.slug: "geheim",
            ft_c.slug: {"foo": "bar"},
            ft_d.slug: "",
            ft_e.slug: "welt",
            ft_f.slug: ["a", {"x": 1}, None, "b"],
        }

        result = compute_event_search_text(data, doc_type)

        assert result == "hallo welt a b"


# ============================================================================
# core.services.events.fields.stage_index
# ============================================================================


class TestStageIndex:
    def test_unbekannte_stufe_liefert_minus_eins(self):
        """Pins ``x_stage_index__mutmut_4``: Fallback bei unbekannter Stufe
        ``-1`` -> ``-2``."""
        assert stage_index("nicht-vorhanden") == -1


# ============================================================================
# core.services.events.fields._snapshot_field_metadata
# ============================================================================


class TestSnapshotFieldMetadata:
    def test_nutzt_select_related_fuer_field_template(self, django_assert_num_queries, facility):
        """Pins ``x__snapshot_field_metadata__mutmut_2``:
        ``select_related("field_template")`` -> ``select_related(None)`` — mit
        Join genau 1 Query, ohne Join eine zusaetzliche je Feld."""
        doc_type = _make_doc_type(facility)
        ft1 = _make_field_template(facility, name="Feld1")
        ft2 = _make_field_template(facility, name="Feld2")
        _attach(doc_type, ft1, sort_order=0)
        _attach(doc_type, ft2, sort_order=1)

        with django_assert_num_queries(1):
            result = _snapshot_field_metadata(doc_type)

        assert set(result) == {ft1.slug, ft2.slug}


# ============================================================================
# core.services.events.fields.split_file_and_text_data
# ============================================================================


class TestSplitFileAndTextData:
    def test_bewahrt_die_hochgeladene_datei(self):
        """Pins ``x_split_file_and_text_data__mutmut_7``:
        ``file_fields[key] = [value]`` -> ``file_fields[key] = None`` — eine
        hochgeladene Einzeldatei ginge beim Split spurlos verloren
        (Datenverlust vor dem eigentlichen Speichern)."""
        upload = SimpleUploadedFile("dokument.pdf", b"Inhalt", content_type="application/pdf")

        file_fields, text_data = split_file_and_text_data({"anlage": upload, "notiz": "Text"})

        assert file_fields == {"anlage": [upload]}
        assert text_data == {"notiz": "Text"}
